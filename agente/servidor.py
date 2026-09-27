import json
import logging
import math
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agente.configuracion import Configuracion, cargar_configuracion
from agente.instrucciones import PREGUNTAS_SUGERIDAS, cargar_cv, construir_instrucciones
from agente.modelo import ErrorModelo, Generacion, ModeloGemini, ModeloLenguaje
from agente.protocolo import (
    FIN_SSE,
    EscritorSSE,
    Solicitud,
    SolicitudInvalida,
    interpretar_solicitud,
    item_mensaje,
    nuevo_id,
    objeto_respuesta,
    parte_texto,
)
from agente.registros import configurar_registros, registrar
from agente.salvaguardas import RECHAZO_INYECCION, RESPUESTA_BLOQUEO, detectar_inyeccion
from agente.seguridad import LimitadorTasa, PresupuestoTokens, autenticar

logger = logging.getLogger("agente_cv")

# Con menos tokens, el razonamiento interno de Gemini puede gastarse todo el presupuesto y dejar la respuesta vacía.
MIN_TOKENS_SALIDA = 256
MENSAJE_ERROR_MODELO = "El modelo no pudo generar una respuesta."


@dataclass
class Turno:
    """Estado de una petición mientras se atiende."""

    id_respuesta: str
    id_clave: str
    solicitud: Solicitud
    max_tokens: int
    inicio: float = field(default_factory=time.perf_counter)
    generacion: Generacion = field(default_factory=Generacion)
    salvaguarda: str | None = None


def _error(estado: int, mensaje: str, tipo: str, codigo: str | None = None, parametro: str | None = None):
    """Respuesta de error con el formato de Open Responses."""
    cuerpo = {"error": {"message": mensaje, "type": tipo, "code": codigo or tipo, "param": parametro}}
    return JSONResponse(cuerpo, status_code=estado)


async def _leer_cuerpo(request: Request, maximo: int) -> bytes | None:
    """Lee el cuerpo por partes y se detiene en cuanto pasa el máximo, aunque no venga Content-Length."""
    declarado = request.headers.get("content-length", "")
    if declarado.isdigit() and int(declarado) > maximo:
        return None
    cuerpo = bytearray()
    async for trozo in request.stream():
        cuerpo += trozo
        if len(cuerpo) > maximo:
            return None
    return bytes(cuerpo)


def crear_app(config: Configuracion | None = None, modelo: ModeloLenguaje | None = None) -> FastAPI:
    """Arma la aplicación; las pruebas le pasan su propia configuración y un modelo falso."""
    config = config or cargar_configuracion()
    if modelo is None:
        configurar_registros()
        modelo = ModeloGemini(config.proyecto_gcp, config.ubicacion_gcp, config.modelo, config.nivel_razonamiento)
    cv = cargar_cv()
    limitador = LimitadorTasa(config.limite_por_minuto, config.limite_rafaga)
    presupuesto = PresupuestoTokens(config.presupuesto_tokens_diario)

    app = FastAPI(title="Agente de CV", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(StarletteHTTPException)
    async def error_http(_: Request, exc: StarletteHTTPException):
        tipo = "not_found" if exc.status_code == 404 else "invalid_request"
        return _error(exc.status_code, str(exc.detail), tipo)

    @app.exception_handler(Exception)
    async def error_no_controlado(_: Request, exc: Exception):
        logger.exception("error_no_controlado")
        return _error(500, "Error interno del servidor.", "server_error")

    @app.get("/salud")
    async def salud():
        return {"estado": "ok"}

    @app.get("/.well-known/agent-card.json")
    async def tarjeta_agente(request: Request):
        """Tarjeta A2A que la plataforma usa para llenar el formulario al importar el agente."""
        base = str(request.base_url).rstrip("/")
        persona = cv["persona"]
        return {
            "protocolVersion": "0.3.0",
            "name": f"Agente de CV — {persona['nombre']}",
            "description": (
                f"Conversa en español sobre el perfil profesional, experiencia, formación, habilidades "
                f"y proyectos de {persona['nombre']}. Implementa la especificación Open Responses."
            ),
            "url": f"{base}/v1",
            "version": "1.0.0",
            "provider": {"organization": persona["nombre"], "url": base},
            "capabilities": {"streaming": True, "pushNotifications": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
            "security": [{"bearer": []}],
            "skills": [
                {
                    "id": "preguntas-cv",
                    "name": "Preguntas sobre el CV",
                    "description": "Responde con base exclusivamente en el CV; declina temas fuera de alcance.",
                    "tags": ["cv", "reclutamiento", "machine-learning", "mlops", "rag"],
                    "examples": PREGUNTAS_SUGERIDAS,
                }
            ],
        }

    async def textos(turno: Turno, sistema: str) -> AsyncIterator[str]:
        """Única fuente de texto de la respuesta, con o sin streaming."""
        if turno.salvaguarda:
            yield RECHAZO_INYECCION
            return
        emitido = False
        async for trozo in modelo.generar(sistema, turno.solicitud.mensajes, turno.max_tokens, turno.generacion):
            emitido = True
            yield trozo
        if turno.generacion.motivo_fin == "bloqueado" and not emitido:
            turno.salvaguarda = "bloqueo_modelo"
            yield RESPUESTA_BLOQUEO

    def registrar_fin(turno: Turno, estado: str, streaming: bool) -> None:
        """Descuenta los tokens del presupuesto y deja una línea de log por respuesta."""
        uso = turno.generacion.uso
        presupuesto.registrar(uso.tokens_entrada + uso.tokens_salida)
        campos = {
            "id_respuesta": turno.id_respuesta,
            "id_clave": turno.id_clave,
            "estado": estado,
            "streaming": streaming,
            "salvaguarda": turno.salvaguarda,
            "motivo_fin": turno.generacion.motivo_fin,
            "latencia_ms": round((time.perf_counter() - turno.inicio) * 1000),
            "turnos": len(turno.solicitud.mensajes),
            "tokens_entrada": uso.tokens_entrada,
            "tokens_salida": uso.tokens_salida,
            "modelo": config.modelo,
        }
        if config.registrar_preguntas:
            campos["pregunta"] = turno.solicitud.mensajes[-1].texto[:500]
        registrar(logger, "respuesta", **campos)

    async def atender(request: Request):
        """POST /v1/responses: valida la petición y responde en JSON o por streaming."""
        id_clave = autenticar(request.headers.get("authorization"), config.claves_api)
        if id_clave is None:
            return _error(401, "API key inválida o ausente.", "invalid_request", "invalid_api_key")

        if (espera := limitador.consumir(id_clave)) is not None:
            respuesta = _error(429, "Límite de peticiones excedido.", "too_many_requests", "rate_limit_exceeded")
            respuesta.headers["Retry-After"] = str(math.ceil(espera))
            registrar(logger, "limite_excedido", logging.WARNING, id_clave=id_clave)
            return respuesta

        if presupuesto.agotado():
            registrar(logger, "presupuesto_agotado", logging.ERROR, id_clave=id_clave)
            return _error(
                429, "Se agotó el presupuesto diario del servicio.", "too_many_requests", "daily_budget_exhausted"
            )

        crudo = await _leer_cuerpo(request, config.limites.max_bytes_cuerpo)
        if crudo is None:
            return _error(400, "El cuerpo de la petición es demasiado grande.", "invalid_request", "request_too_large")
        try:
            solicitud = interpretar_solicitud(json.loads(crudo), config.limites)
        except (ValueError, RecursionError):
            return _error(400, "El cuerpo no es JSON válido.", "invalid_request", "invalid_json")
        except SolicitudInvalida as exc:
            return _error(400, str(exc), "invalid_request", exc.codigo, exc.parametro)

        solicitados = solicitud.max_tokens_salida or config.max_tokens_salida
        turno = Turno(
            id_respuesta=nuevo_id("resp"),
            id_clave=id_clave,
            solicitud=solicitud,
            max_tokens=max(MIN_TOKENS_SALIDA, min(solicitados, config.max_tokens_salida)),
        )
        ultima_pregunta = next(m for m in reversed(solicitud.mensajes) if m.rol == "user")
        turno.salvaguarda = detectar_inyeccion(ultima_pregunta.texto)
        sistema = construir_instrucciones(cv, solicitud.instrucciones_operador)
        creada_en = int(time.time())
        id_mensaje = nuevo_id("msg")

        def respuesta(estado: str, salida: list[dict], **extra) -> dict:
            """Objeto `response` de este turno; solo cambian el estado y la salida."""
            return objeto_respuesta(
                id_respuesta=turno.id_respuesta,
                creada_en=creada_en,
                modelo=config.modelo,
                solicitud=solicitud,
                max_tokens_salida=turno.max_tokens,
                estado=estado,
                salida=salida,
                **extra,
            )

        def respuesta_final(texto: str) -> dict:
            """Respuesta terminada; queda `incomplete` si el modelo se quedó sin tokens."""
            estado = "incomplete" if turno.generacion.motivo_fin == "limite_tokens" else "completed"
            return respuesta(
                estado,
                [item_mensaje(id_mensaje, texto, estado)],
                uso=turno.generacion.uso,
                completada_en=int(time.time()),
                motivo_incompleta="max_output_tokens" if estado == "incomplete" else None,
            )

        if not solicitud.streaming:
            try:
                texto = "".join([t async for t in textos(turno, sistema)])
            except ErrorModelo as exc:
                logger.exception("error_modelo", extra={"campos": {"id_respuesta": turno.id_respuesta}})
                registrar_fin(turno, "failed", False)
                if exc.estado == 429:
                    return _error(429, "El modelo está saturado; reintenta en unos segundos.", "too_many_requests")
                return _error(500, MENSAJE_ERROR_MODELO, "model_error")
            final = respuesta_final(texto)
            registrar_fin(turno, final["status"], False)
            return final

        async def eventos() -> AsyncIterator[str]:
            """Emite la secuencia de eventos SSE que define Open Responses."""
            sse = EscritorSSE()
            posicion = {"item_id": id_mensaje, "output_index": 0, "content_index": 0}
            estado_final = "cancelled"
            try:
                en_curso = respuesta("in_progress", [])
                yield sse.evento("response.created", response=en_curso)
                yield sse.evento("response.in_progress", response=en_curso)
                yield sse.evento(
                    "response.output_item.added", output_index=0, item=item_mensaje(id_mensaje, None, "in_progress")
                )
                yield sse.evento("response.content_part.added", **posicion, part=parte_texto(""))

                partes: list[str] = []
                try:
                    async for delta in textos(turno, sistema):
                        partes.append(delta)
                        yield sse.evento("response.output_text.delta", **posicion, delta=delta, logprobs=[])
                except ErrorModelo:
                    logger.exception("error_modelo", extra={"campos": {"id_respuesta": turno.id_respuesta}})
                    estado_final = "failed"
                    error = {"code": "model_error", "message": MENSAJE_ERROR_MODELO}
                    yield sse.evento("error", error={**error, "type": "model_error", "param": None})
                    yield sse.evento("response.failed", response=respuesta("failed", [], error=error))
                    yield FIN_SSE
                    return

                texto = "".join(partes)
                final = respuesta_final(texto)
                yield sse.evento("response.output_text.done", **posicion, text=texto, logprobs=[])
                yield sse.evento("response.content_part.done", **posicion, part=parte_texto(texto))
                yield sse.evento("response.output_item.done", output_index=0, item=final["output"][0])
                tipo_final = "response.completed" if final["status"] == "completed" else "response.incomplete"
                yield sse.evento(tipo_final, response=final)
                yield FIN_SSE
                estado_final = final["status"]
            finally:
                # También se registra cuando el cliente corta el stream a la mitad.
                registrar_fin(turno, estado_final, True)

        return StreamingResponse(
            eventos(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.add_api_route("/v1/responses", atender, methods=["POST"])
    return app
