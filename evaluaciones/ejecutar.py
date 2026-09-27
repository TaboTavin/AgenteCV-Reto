"""Uso: URL_BASE_EVAL=https://<servicio>/v1 CLAVE_API_EVAL=... uv run python evaluaciones/ejecutar.py"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import yaml

RUTA_CASOS = Path(__file__).with_name("casos.yaml")


def plegar(texto: str) -> str:
    """Minúsculas, sin acentos y sin separadores de miles, para comparar sin importar cómo lo escriba el modelo."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    texto = re.sub(r"(?<=\d)[,.](?=\d{3}\b)", "", texto)
    return re.sub(r"\s+", " ", texto)


def consultar(cliente: httpx.Client, url_base: str, clave: str, caso: dict) -> str:
    """Hace la pregunta del caso y devuelve el texto de la respuesta, o un marcador de error."""
    peticion = {"model": "agente-cv", "input": caso["entrada"]}
    if instrucciones := caso.get("instrucciones"):
        peticion["instructions"] = instrucciones
    try:
        for intento in range(5):
            r = cliente.post(f"{url_base}/responses", headers={"Authorization": f"Bearer {clave}"}, json=peticion)
            if r.status_code != 429 or intento == 4:
                break
            time.sleep(int(r.headers.get("Retry-After", 2)))
        r.raise_for_status()
        return "".join(p["text"] for item in r.json()["output"] for p in item.get("content", []) if "text" in p)
    except (httpx.HTTPError, KeyError) as exc:
        return f"<ERROR: {exc}>"


def verificar(caso: dict, respuesta: str) -> list[str]:
    """Devuelve la lista de reglas del caso que la respuesta no cumple."""
    if respuesta.startswith("<ERROR"):
        return [respuesta]
    texto = f" {plegar(respuesta)} "
    fallas = [f"falta '{t}'" for t in caso.get("contiene_todos", []) if plegar(t) not in texto]
    if (alguno := caso.get("contiene_alguno")) and not any(plegar(t) in texto for t in alguno):
        fallas.append(f"no contiene ninguno de {alguno[:4]}...")
    fallas += [f"contiene prohibido '{t}'" for t in caso.get("no_contiene", []) if plegar(t) in texto]
    return fallas


def principal() -> int:
    """Corre todos los casos y sale con 1 si no se alcanza el umbral o falla un caso crítico."""
    analizador = argparse.ArgumentParser(description="Evalúa el agente de CV contra evaluaciones/casos.yaml")
    analizador.add_argument("--url-base", default=os.environ.get("URL_BASE_EVAL", "http://localhost:8080/v1"))
    analizador.add_argument("--clave-api", default=os.environ.get("CLAVE_API_EVAL"))
    analizador.add_argument("--umbral", type=float, default=0.85)
    analizador.add_argument("--solo", help="ids separados por coma")
    analizador.add_argument("--reporte", type=Path)
    argumentos = analizador.parse_args()
    if not argumentos.clave_api:
        analizador.error("Falta --clave-api o CLAVE_API_EVAL")

    casos = yaml.safe_load(RUTA_CASOS.read_text(encoding="utf-8"))["casos"]
    if argumentos.solo:
        seleccion = set(argumentos.solo.split(","))
        casos = [c for c in casos if c["id"] in seleccion]

    url_base = argumentos.url_base.rstrip("/")
    with httpx.Client(timeout=90) as cliente, ThreadPoolExecutor(max_workers=2) as hilos:
        respuestas = list(hilos.map(lambda c: consultar(cliente, url_base, argumentos.clave_api, c), casos))

    resultados = []
    for caso, respuesta in zip(casos, respuestas, strict=True):
        fallas = verificar(caso, respuesta)
        resultados.append({**caso, "respuesta": respuesta, "fallas": fallas, "aprobado": not fallas})
        marca = "OK  " if not fallas else "FALLA"
        print(f"[{marca}] {caso['categoria']:<16} {caso['id']:<22} {'; '.join(fallas)}")

    aprobados = sum(r["aprobado"] for r in resultados)
    tasa = aprobados / len(resultados)
    criticos_fallidos = [r["id"] for r in resultados if r.get("critico") and not r["aprobado"]]
    print(f"\n{aprobados}/{len(resultados)} casos aprobados ({tasa:.0%}); umbral {argumentos.umbral:.0%}")
    if criticos_fallidos:
        print(f"Casos críticos fallidos: {criticos_fallidos}")
    for r in resultados:
        if not r["aprobado"]:
            print(f"\n--- {r['id']} ---\n{r['respuesta']}")

    if argumentos.reporte:
        argumentos.reporte.write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if tasa >= argumentos.umbral and not criticos_fallidos else 1


if __name__ == "__main__":
    sys.exit(principal())
