import os
from dataclasses import dataclass, field


def _entero(nombre: str, por_defecto: int) -> int:
    return int(os.environ.get(nombre, por_defecto))


@dataclass(frozen=True)
class LimitesEntrada:
    """Topes de tamaño de cada petición; acotan cuántos tokens puede costar una sola llamada."""

    max_bytes_cuerpo: int = 256_000
    max_caracteres_mensaje: int = 4000
    max_caracteres_historial: int = 16000
    max_mensajes_historial: int = 20
    max_caracteres_instrucciones: int = 2000


@dataclass(frozen=True)
class Configuracion:
    """Todo lo que se puede ajustar por variables de entorno, fijo mientras vive el proceso."""

    claves_api: tuple[str, ...]
    proyecto_gcp: str | None
    ubicacion_gcp: str
    modelo: str
    nivel_razonamiento: str
    max_tokens_salida: int
    limite_por_minuto: int
    limite_rafaga: int
    presupuesto_tokens_diario: int
    registrar_preguntas: bool
    limites: LimitesEntrada = field(default_factory=LimitesEntrada)


def cargar_configuracion() -> Configuracion:
    """Lee la configuración del entorno; sin API keys el servicio se niega a arrancar."""
    claves = tuple(c.strip() for c in os.environ.get("CLAVES_API", "").split(",") if c.strip())
    if not claves:
        raise RuntimeError("CLAVES_API no está configurada; el servicio no arranca sin autenticación.")
    return Configuracion(
        claves_api=claves,
        proyecto_gcp=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        ubicacion_gcp=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
        modelo=os.environ.get("MODELO_GEMINI", "gemini-3.5-flash"),
        nivel_razonamiento=os.environ.get("NIVEL_RAZONAMIENTO", "LOW"),
        max_tokens_salida=_entero("MAX_TOKENS_SALIDA", 2048),
        limite_por_minuto=_entero("LIMITE_POR_MINUTO", 60),
        limite_rafaga=_entero("LIMITE_RAFAGA", 20),
        presupuesto_tokens_diario=_entero("PRESUPUESTO_TOKENS_DIARIO", 5_000_000),
        registrar_preguntas=os.environ.get("REGISTRAR_PREGUNTAS", "false").lower() == "true",
    )
