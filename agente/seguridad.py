import hashlib
import hmac
import time
from datetime import UTC, date, datetime


def autenticar(encabezado: str | None, claves_api: tuple[str, ...]) -> str | None:
    """Revisa que la API key venga en el header y sea válida; devuelve un id corto de la key para los logs."""
    if not encabezado or not encabezado.startswith("Bearer "):
        return None
    token = encabezado.removeprefix("Bearer ").strip().encode()
    # Se compara contra todas las keys, sin salir al primer acierto, para no dar pistas por el tiempo de respuesta.
    validas = [clave for clave in claves_api if hmac.compare_digest(token, clave.encode())]
    if not validas:
        return None
    return hashlib.sha256(validas[0].encode()).hexdigest()[:12]


class LimitadorTasa:
    """Token bucket por key: permite ráfagas cortas pero acota el promedio por minuto."""

    def __init__(self, por_minuto: int, rafaga: int):
        self._tasa = por_minuto / 60.0
        self._capacidad = float(rafaga)
        self._cubetas: dict[str, tuple[float, float]] = {}

    def consumir(self, id_clave: str) -> float | None:
        """Gasta una ficha; si no hay, devuelve cuántos segundos hay que esperar."""
        ahora = time.monotonic()
        fichas, ultimo = self._cubetas.get(id_clave, (self._capacidad, ahora))
        fichas = min(self._capacidad, fichas + (ahora - ultimo) * self._tasa)
        if fichas >= 1:
            self._cubetas[id_clave] = (fichas - 1, ahora)
            return None
        self._cubetas[id_clave] = (fichas, ahora)
        return (1 - fichas) / self._tasa


class PresupuestoTokens:
    """Tope diario de tokens por instancia; se reinicia al cambiar el día en UTC."""

    def __init__(self, tokens_por_dia: int):
        self._limite = tokens_por_dia
        self._dia: date | None = None
        self._consumidos = 0

    def _reiniciar_si_cambio_el_dia(self) -> None:
        hoy = datetime.now(UTC).date()
        if hoy != self._dia:
            self._dia, self._consumidos = hoy, 0

    def agotado(self) -> bool:
        """Indica si ya se gastó el presupuesto de hoy."""
        self._reiniciar_si_cambio_el_dia()
        return self._consumidos >= self._limite

    def registrar(self, tokens: int) -> None:
        """Suma los tokens que consumió una respuesta."""
        self._reiniciar_si_cambio_el_dia()
        self._consumidos += tokens
