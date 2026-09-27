import re
import unicodedata

RECHAZO_INYECCION = (
    "Solo puedo ayudarte con preguntas sobre el perfil profesional de Christian: su experiencia, "
    "formación, habilidades y proyectos. ¿Qué te gustaría saber de él?"
)
RESPUESTA_BLOQUEO = (
    "No puedo responder eso. Puedo ayudarte con preguntas sobre la experiencia, formación, "
    "habilidades o proyectos de Christian."
)

_PATRONES_INYECCION = {
    nombre: re.compile(expresion)
    for nombre, expresion in {
        "ignorar_reglas_es": r"\b(ignora|olvida|omite|descarta|saltate|desobedece)\b.{0,40}"
        r"\b(instrucciones|reglas|indicaciones|restricciones|directrices)\b",
        "ignorar_reglas_en": r"\b(ignore|forget|disregard|bypass|override)\b.{0,40}"
        r"\b(instructions|rules|guidelines|restrictions|prompt)\b",
        "pedir_prompt_propio": r"\b(tu|tus)\s+(prompt|instrucciones|reglas|directrices|configuracion)\b",
        "prompt_sistema_en": r"\b(system|developer|initial|hidden)\s+(prompt|message|instructions)\b",
        "prompt_sistema_es": r"\b(prompt|instrucciones|mensaje)\s+(del\s+)?(sistema|inicial|oculto|interno)\b",
        "modo_especial": r"\b(modo|mode)\s+(desarrollador|developer|dan|jailbreak|dios|god|sin restricciones)\b",
        "jailbreak": r"\bjailbreak\b|\beres\s+dan\b|\byou\s+are\s+dan\b|\bdo anything now\b",
        "sin_limites": r"\b(responde|actua|habla|eres|seas|estas)\b.{0,20}"
        r"\bsin\s+(restricciones|filtros|limites|reglas)\b"
        r"|\bno tienes\s+(restricciones|filtros|limites|reglas)\b",
        "cambio_de_rol": r"\b(a partir de ahora|desde ahora)\b.{0,25}\b(eres|seras|actua|responde|ignora)\b"
        r"|\bfrom now on\b|\bnueva regla\b|\bnew rule\b",
        "falsa_autoridad": r"\b(soy|habla)\s+(el|la|tu)\s+(administrador|administradora|desarrollador|"
        r"desarrolladora|creador|creadora|admin)\b|\bi am your (developer|admin|creator)\b",
        "delimitadores": r"<\s*/?\s*(system|cv|instrucciones|instrucciones_del_operador)\s*>"
        r"|<\|im_(start|end)\|>|\[/?(inst|system)\]",
    }.items()
}


def detectar_inyeccion(texto: str) -> str | None:
    """Devuelve el nombre del patrón de inyección que aparece en el texto, o None si parece una pregunta normal."""
    # Sin acentos ni mayúsculas, para que "Ignóra" e "ignora" caigan en el mismo patrón.
    sin_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    normalizado = re.sub(r"\s+", " ", sin_acentos.lower())
    return next((nombre for nombre, patron in _PATRONES_INYECCION.items() if patron.search(normalizado)), None)
