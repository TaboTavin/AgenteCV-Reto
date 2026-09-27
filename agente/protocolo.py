import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from agente.configuracion import LimitesEntrada
from agente.modelo import Mensaje, Uso

FIN_SSE = "data: [DONE]\n\n"

_PARTES_CON_TEXTO = {"input_text", "output_text", "text"}
_ITEMS_IGNORADOS = {"reasoning", "function_call", "function_call_output", "item_reference", "compaction"}


class SolicitudInvalida(Exception):
    """Error de validación que se devuelve al cliente como 400."""

    def __init__(self, mensaje: str, parametro: str | None = None, codigo: str = "invalid_request"):
        super().__init__(mensaje)
        self.parametro = parametro
        self.codigo = codigo


@dataclass
class Solicitud:
    """Lo que el agente necesita de una petición de Open Responses."""

    mensajes: list[Mensaje]
    instrucciones_operador: str | None
    streaming: bool
    max_tokens_salida: int | None
    metadatos: dict[str, str] = field(default_factory=dict)
    instrucciones: str | None = None


def nuevo_id(prefijo: str) -> str:
    """Genera un id aleatorio con el prefijo que usa la especificación (resp_, msg_)."""
    return f"{prefijo}_{secrets.token_hex(16)}"


def _extraer_texto(contenido: Any, parametro: str) -> str:
    """Junta el texto de un `content`; ignora imágenes y archivos porque el agente solo trabaja con texto."""
    if isinstance(contenido, str):
        return contenido
    if not isinstance(contenido, list):
        raise SolicitudInvalida("content debe ser string o arreglo de partes.", parametro)
    textos = []
    for parte in contenido:
        if not isinstance(parte, dict):
            raise SolicitudInvalida("Cada parte de content debe ser un objeto.", parametro)
        tipo = parte.get("type")
        if tipo in _PARTES_CON_TEXTO:
            textos.append(str(parte.get("text", "")))
        elif tipo == "refusal":
            textos.append(str(parte.get("refusal", "")))
    return "\n".join(t for t in textos if t)


def _preparar_historial(mensajes: list[Mensaje], limites: LimitesEntrada) -> list[Mensaje]:
    """Recorta el historial a los límites y lo deja en el formato de turnos que espera Gemini."""
    # La plataforma manda todo el historial en cada turno; lo acotamos para que ninguna petición cueste de más.
    historial = [
        Mensaje(rol=m.rol, texto=m.texto[: limites.max_caracteres_mensaje])
        for m in mensajes[-limites.max_mensajes_historial :]
    ]
    while len(historial) > 1 and sum(len(m.texto) for m in historial) > limites.max_caracteres_historial:
        historial.pop(0)
    while historial and historial[0].rol != "user":
        historial.pop(0)

    # Gemini espera turnos alternados user/model, así que juntamos los consecutivos del mismo rol.
    alternados: list[Mensaje] = []
    for m in historial:
        if alternados and alternados[-1].rol == m.rol:
            alternados[-1] = Mensaje(rol=m.rol, texto=f"{alternados[-1].texto}\n\n{m.texto}")
        else:
            alternados.append(m)
    return alternados


def interpretar_solicitud(cuerpo: Any, limites: LimitesEntrada) -> Solicitud:
    """Valida el cuerpo de la petición y lo convierte en una `Solicitud`."""
    if not isinstance(cuerpo, dict):
        raise SolicitudInvalida("El cuerpo debe ser un objeto JSON.")
    if cuerpo.get("previous_response_id"):
        raise SolicitudInvalida(
            "Este servidor no almacena respuestas (store=false); envía la transcripción completa en input.",
            parametro="previous_response_id",
            codigo="previous_response_not_found",
        )

    entrada = cuerpo.get("input")
    if isinstance(entrada, str):
        entrada = [{"type": "message", "role": "user", "content": entrada}]
    if not isinstance(entrada, list) or not entrada:
        raise SolicitudInvalida("input es requerido (string o arreglo de items).", parametro="input")

    instrucciones = cuerpo.get("instructions")
    if instrucciones is not None and not isinstance(instrucciones, str):
        raise SolicitudInvalida("instructions debe ser string.", parametro="instructions")

    mensajes: list[Mensaje] = []
    del_operador: list[str] = [instrucciones] if instrucciones else []
    for i, item in enumerate(entrada):
        parametro = f"input[{i}]"
        if not isinstance(item, dict):
            raise SolicitudInvalida("Cada item de input debe ser un objeto.", parametro)
        tipo = item.get("type", "message")
        if tipo in _ITEMS_IGNORADOS:
            continue
        if tipo != "message":
            raise SolicitudInvalida(f"Tipo de item no soportado: {tipo!r}.", parametro)
        rol = item.get("role")
        texto = _extraer_texto(item.get("content"), f"{parametro}.content").strip()
        if rol not in ("system", "developer", "user", "assistant"):
            raise SolicitudInvalida(f"role no soportado: {rol!r}.", f"{parametro}.role")
        if not texto:
            continue
        if rol in ("system", "developer"):
            del_operador.append(texto)
        else:
            mensajes.append(Mensaje(rol=rol, texto=texto))

    preguntas = [m for m in mensajes if m.rol == "user"]
    if not preguntas:
        raise SolicitudInvalida("input debe contener al menos un mensaje de usuario con texto.", parametro="input")
    if len(preguntas[-1].texto) > limites.max_caracteres_mensaje:
        raise SolicitudInvalida(
            f"El mensaje excede {limites.max_caracteres_mensaje} caracteres.",
            parametro="input",
            codigo="message_too_long",
        )
    instrucciones_operador = "\n\n".join(del_operador)
    if len(instrucciones_operador) > limites.max_caracteres_instrucciones:
        raise SolicitudInvalida(
            f"Las instrucciones exceden {limites.max_caracteres_instrucciones} caracteres.",
            parametro="instructions",
            codigo="instructions_too_long",
        )

    max_tokens = cuerpo.get("max_output_tokens")
    if max_tokens is not None and (not isinstance(max_tokens, int) or max_tokens <= 0):
        raise SolicitudInvalida("max_output_tokens debe ser un entero positivo.", parametro="max_output_tokens")

    metadatos = cuerpo.get("metadata") or {}
    if not isinstance(metadatos, dict):
        raise SolicitudInvalida("metadata debe ser un objeto.", parametro="metadata")

    return Solicitud(
        mensajes=_preparar_historial(mensajes, limites),
        instrucciones_operador=instrucciones_operador or None,
        streaming=bool(cuerpo.get("stream", False)),
        max_tokens_salida=max_tokens,
        metadatos={str(k)[:64]: str(v)[:512] for k, v in list(metadatos.items())[:16]},
        instrucciones=instrucciones,
    )


def parte_texto(texto: str) -> dict:
    """Parte `output_text` de un mensaje del asistente."""
    return {"type": "output_text", "text": texto, "annotations": [], "logprobs": []}


def item_mensaje(id_item: str, texto: str | None, estado: str) -> dict:
    """Item `message` del asistente; sin texto cuando apenas empieza el stream."""
    return {
        "type": "message",
        "id": id_item,
        "status": estado,
        "role": "assistant",
        "content": [] if texto is None else [parte_texto(texto)],
    }


def objeto_respuesta(
    *,
    id_respuesta: str,
    creada_en: int,
    modelo: str,
    solicitud: Solicitud,
    max_tokens_salida: int,
    estado: str,
    salida: list[dict],
    uso: Uso | None = None,
    completada_en: int | None = None,
    error: dict | None = None,
    motivo_incompleta: str | None = None,
) -> dict:
    """Objeto `response` con todos los campos que la especificación marca como requeridos."""
    return {
        "id": id_respuesta,
        "object": "response",
        "created_at": creada_en,
        "completed_at": completada_en,
        "status": estado,
        "incomplete_details": {"reason": motivo_incompleta} if motivo_incompleta else None,
        "model": modelo,
        "previous_response_id": None,
        "instructions": solicitud.instrucciones,
        "output": salida,
        "error": error,
        "tools": [],
        "tool_choice": "none",
        "truncation": "auto",
        "parallel_tool_calls": False,
        "text": {"format": {"type": "text"}},
        "top_p": 0.95,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
        "top_logprobs": 0,
        "temperature": 1.0,
        "reasoning": None,
        "usage": None
        if uso is None
        else {
            "input_tokens": uso.tokens_entrada,
            "output_tokens": uso.tokens_salida,
            "total_tokens": uso.tokens_entrada + uso.tokens_salida,
            "input_tokens_details": {"cached_tokens": uso.tokens_cache},
            "output_tokens_details": {"reasoning_tokens": uso.tokens_razonamiento},
        },
        "max_output_tokens": max_tokens_salida,
        "max_tool_calls": None,
        "store": False,
        "background": False,
        "service_tier": "default",
        "metadata": solicitud.metadatos,
        "safety_identifier": None,
        "prompt_cache_key": None,
    }


class EscritorSSE:
    """Da formato a los eventos del stream y lleva el `sequence_number` que exige la especificación."""

    def __init__(self) -> None:
        self._secuencia = 0

    def evento(self, tipo: str, **datos: Any) -> str:
        """Serializa un evento SSE con su número de secuencia."""
        cuerpo = {"type": tipo, "sequence_number": self._secuencia, **datos}
        self._secuencia += 1
        return f"event: {tipo}\ndata: {json.dumps(cuerpo, ensure_ascii=False)}\n\n"
