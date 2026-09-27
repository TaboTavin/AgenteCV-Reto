from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

from google import genai
from google.genai import errors, types

_MOTIVOS_BLOQUEO = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE", "OTHER"}


@dataclass
class Mensaje:
    """Un turno de la conversación, ya sin el formato de Open Responses."""

    rol: Literal["user", "assistant"]
    texto: str


@dataclass
class Uso:
    """Tokens que reportó Gemini para una respuesta."""

    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_razonamiento: int = 0
    tokens_cache: int = 0


@dataclass
class Generacion:
    """Lo que el modelo va anotando mientras genera: tokens usados y por qué terminó."""

    uso: Uso = field(default_factory=Uso)
    motivo_fin: Literal["completo", "limite_tokens", "bloqueado"] = "completo"


class ErrorModelo(Exception):
    """Vertex AI falló; `estado` es 429 si fue por cuota y 500 en cualquier otro caso."""

    def __init__(self, mensaje: str, estado: int = 500):
        super().__init__(mensaje)
        self.estado = estado


class ModeloLenguaje(Protocol):
    """Lo mínimo que el servidor necesita de un LLM; las pruebas lo cumplen con un modelo falso."""

    def generar(
        self, sistema: str, mensajes: list[Mensaje], max_tokens_salida: int, generacion: Generacion
    ) -> AsyncIterator[str]: ...


class ModeloGemini:
    """Gemini en Vertex AI, autenticado con la service account de Cloud Run."""

    def __init__(self, proyecto: str | None, ubicacion: str, modelo: str, nivel_razonamiento: str):
        # Vertex responde 429 cuando la cuota compartida se satura; con reintentar un par de veces suele bastar.
        reintentos = types.HttpRetryOptions(
            attempts=4, initial_delay=1.0, max_delay=8.0, http_status_codes=[429, 500, 503]
        )
        self._cliente = genai.Client(
            vertexai=True,
            project=proyecto,
            location=ubicacion,
            http_options=types.HttpOptions(timeout=60_000, retry_options=reintentos),
        )
        self._modelo = modelo
        self._nivel_razonamiento = nivel_razonamiento

    async def generar(
        self, sistema: str, mensajes: list[Mensaje], max_tokens_salida: int, generacion: Generacion
    ) -> AsyncIterator[str]:
        """Va entregando el texto de Gemini conforme llega y anota el uso en `generacion`."""
        contenidos = [
            types.Content(role="model" if m.rol == "assistant" else "user", parts=[types.Part(text=m.texto)])
            for m in mensajes
        ]
        ajustes = types.GenerateContentConfig(
            system_instruction=sistema,
            max_output_tokens=max_tokens_salida,
            thinking_config=types.ThinkingConfig(thinking_level=self._nivel_razonamiento),
        )
        try:
            flujo = await self._cliente.aio.models.generate_content_stream(
                model=self._modelo, contents=contenidos, config=ajustes
            )
            async for trozo in flujo:
                if metadatos := trozo.usage_metadata:
                    razonamiento = metadatos.thoughts_token_count or 0
                    # Google cobra el razonamiento interno como tokens de salida.
                    generacion.uso = Uso(
                        tokens_entrada=metadatos.prompt_token_count or 0,
                        tokens_salida=(metadatos.candidates_token_count or 0) + razonamiento,
                        tokens_razonamiento=razonamiento,
                        tokens_cache=metadatos.cached_content_token_count or 0,
                    )
                if trozo.candidates and (motivo := trozo.candidates[0].finish_reason):
                    if motivo.name == "MAX_TOKENS":
                        generacion.motivo_fin = "limite_tokens"
                    elif motivo.name in _MOTIVOS_BLOQUEO:
                        generacion.motivo_fin = "bloqueado"
                if trozo.prompt_feedback and trozo.prompt_feedback.block_reason:
                    generacion.motivo_fin = "bloqueado"
                if trozo.text:
                    yield trozo.text
        except errors.APIError as exc:
            estado = 429 if exc.code == 429 else 500
            raise ErrorModelo(f"Vertex AI: {exc.status or exc.code}", estado=estado) from exc
