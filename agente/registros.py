import json
import logging
import sys


class _FormatoCloudLogging(logging.Formatter):
    """Una línea JSON por evento; Cloud Logging la indexa por campo."""

    def format(self, record: logging.LogRecord) -> str:
        entrada = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name}
        entrada.update(getattr(record, "campos", {}))
        if record.exc_info:
            entrada["exception"] = self.formatException(record.exc_info)
        return json.dumps(entrada, ensure_ascii=False, default=str)


def configurar_registros() -> None:
    """Manda todos los logs a stdout en JSON, que es lo que Cloud Run envía a Cloud Logging."""
    manejador = logging.StreamHandler(sys.stdout)
    manejador.setFormatter(_FormatoCloudLogging())
    raiz = logging.getLogger()
    raiz.handlers = [manejador]
    raiz.setLevel(logging.INFO)


def registrar(logger: logging.Logger, evento: str, nivel: int = logging.INFO, **campos) -> None:
    """Escribe un evento con campos estructurados."""
    logger.log(nivel, evento, extra={"campos": campos})
