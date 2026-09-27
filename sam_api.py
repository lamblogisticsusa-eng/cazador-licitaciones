"""
sam_api.py - Cliente de SAM.gov Contract Opportunities API v2.

HALLAZGOS VERIFICADOS contra la API real (Sept 2026) que explican por que el
codigo original no encontraba nada:

1) El campo `description` de /v2/search NO contiene texto. Devuelve una URL:
       "description": "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid=<id>"
   El texto real hay que pedirlo a /v1/noticedesc, que responde con HTML.
   El codigo original pasaba esa URL a Gemini => no habia nada que evaluar.

2) `offset` esta ROTO / acotado. Medido con limit=100 sobre 2083 registros:
       offset=0,1,10  -> 100 registros
       offset=50,99,100,101,500 -> 0 registros
   Conclusion: NO se puede paginar con offset. La estrategia correcta es
   TROCEAR la ventana de fechas en bloques chicos y pedir cada bloque con
   limit=1000 y offset=0.

3) Volumen real medido (ptype=o): ~450 avisos/dia, 2083 en 14 dias.
   Con ptype=o,a son 7647 en 14 dias. El limit=250 del codigo original
   cubria ~4% de una ventana de 10 dias.

4) `responseDeadLine` puede venir VENCIDO (hay avisos con fecha limite anterior
   a postedDate). Hay que filtrarlos.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator
from urllib.parse import urlencode

import requests

import config
import store

log = logging.getLogger("kyomoto.sam")

BASE_SEARCH = "https://api.sam.gov/prod/opportunities/v2/search"
BASE_DESC = "https://api.sam.gov/prod/opportunities/v1/noticedesc"

_sesion = requests.Session()
_sesion.headers.update({"Accept": "application/json", "User-Agent": "Kyomoto/2.0"})

# Momento en que SAM.gov dice que vuelve a dejar usar la API. None = sin tope.
_proximo_acceso: str | None = None

# Ventanas servidas hace poco desde la cache, para no repetir los mismos
# bloques dentro del intervalo de barrido.
_cache_rangos: dict[str, float] = {}

TAG_HTML = re.compile(r"<[^>]+>")
ESPACIOS = re.compile(r"[ \t\r\f\v]+")
SALTOS = re.compile(r"\n{3,}")


class SamError(Exception):
    """Error de la API de SAM.gov con contexto suficiente para diagnosticarlo."""


def _fecha(dt: datetime) -> str:
    """SAM.gov exige MM/DD/YYYY con DIAGONALES. Nunca usar to_native de Windows,
    que en locale es-419 devuelve 09-23-2026 y la API responde 400."""
    return f"{dt.month:02d}/{dt.day:02d}/{dt.year:04d}"


def _parse_fecha(valor: str | None) -> datetime | None:
    if not valor:
        return None
    texto = valor.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(texto[: len(fmt) + 6], fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def limpiar_html(bruto: str) -> str:
    """El endpoint noticedesc devuelve HTML, a veces con ** de markdown dentro."""
    texto = TAG_HTML.sub(" ", bruto or "")
    texto = html.unescape(texto)
    texto = texto.replace("**", " ").replace("\r", " ")
    texto = ESPACIOS.sub(" ", texto)
    texto = SALTOS.sub("\n\n", texto)
    return texto.strip()


def verificar_api() -> dict:
    """Prueba de humo de la API. Se usa en /selftest."""
    if not config.SAM_API_KEY:
        return {"ok": False, "detalle": "SAM_API_KEY no configurada"}
    hoy = datetime.now(timezone.utc)
    params = {
        "api_key": config.SAM_API_KEY,
        "postedFrom": _fecha(hoy - timedelta(days=1)),
        "postedTo": _fecha(hoy),
        "limit": 1,
        "ptype": "o",
    }
    r = _get(params, reintentos=1)
    if r.status_code != 200:
        return {"ok": False, "detalle": f"HTTP {r.status_code}: {r.text[:200]}"}
    datos = r.json()
    return {
        "ok": True,
        "detalle": f"OK - {datos.get('totalRecords', 0)} avisos en 24h",
    }


def _get(params: dict, reintentos: int | None = None) -> requests.Response:
    intentos = config.MAX_REINTENTOS if reintentos is None else reintentos
    url = BASE_SEARCH if "postedFrom" in params else BASE_DESC
    ultima = None
    for i in range(intentos):
        try:
            r = _sesion.get(url, params=params, timeout=config.TIMEOUT_HTTP)
        except requests.RequestException as e:
            ultima = SamError(f"Error de red: {e}")
        else:
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                espera = 2 ** i + 1
                log.warning("SAM.gov %s, reintento %s en %ss", r.status_code, i + 1, espera)
                ultima = SamError(f"HTTP {r.status_code}: {r.text[:300]}")
                time.sleep(espera)
                continue
            # 400/401/403 no se arreglan reintentando.
            raise SamError(f"HTTP {r.status_code}: {r.text[:300]}")
        time.sleep(1)
    raise ultima or SamError("Fallo desconocido")


def obtener_descripcion(notice_id: str) -> str:
    """Texto real de la oportunidad. Devuelve "" si falla (no revienta el escaneo)."""
    try:
        r = _get({"api_key": config.SAM_API_KEY, "noticeid": notice_id}, reintentos=2)
        if r.status_code != 200:
            return ""
        crudo = r.text
        if not crudo or not crudo.lstrip().startswith("{"):
            return ""
        return limpiar_html(r.json().get("description") or "")[: config.MAX_CHARS_DESCRIPCION]
    except SamError as e:
        log.warning("No se pudo bajar la descripcion de %s: %s", notice_id, e)
        return ""


def throttle(respuesta: requests.Response) -> None:
    """
    SAM.gov tambien tiene tope de peticiones por dia. Medido: al agotarlo
    responde HTTP 429 con nextAccessTime, y dice exactamente cuando vuelve.
    Guardarlo permite avisar con la hora real en vez de un "se espera" vago.
    """
    global _proximo_acceso
    if respuesta.status_code != 429:
        return
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return
    siguiente = cuerpo.get("nextAccessTime")
    if siguiente:
        _proximo_acceso = siguiente
        log.error("SAM.gov: cuota agotada. Se puede volver a usar en %s", siguiente)


def _bloque(desde: datetime, hasta: datetime, ptype: str) -> list[dict]:
    params = {
        "api_key": config.SAM_API_KEY,
        "postedFrom": _fecha(desde),
        "postedTo": _fecha(hasta),
        # offset=0 SIEMPRE: el offset de SAM.gov no pagina de forma confiable.
        "offset": 0,
        "limit": config.LIMITE_POR_CHUNK,
        "ptype": ptype,
    }
    r = _get(params)
    throttle(r)
    datos = r.json()
    return datos.get("opportunitiesData") or []


def _rango_vivo(desde: datetime, hasta: datetime, ptype: str) -> list[dict]:
    """Pide un bloque a la API y lo cachea."""
    lote = _bloque(desde, hasta, ptype)
    for opp in lote:
        store.guardar_busqueda(opp)
    return lote


def barrer(dias: int | None = None, ptype: str = "o,a", stats: dict | None = None) -> Iterator[dict]:
    """
    Trae la ventana de fechas con FETCHING INCREMENTAL.

    La ventana de 10 dias son 4 peticiones por barrido, y con barridos cada
    2h eso son 48 al dia: demasiado para el tope diario de SAM.gov. La
    solucion es no volver a pedir lo que ya no cambia:

      - los ultimos DIAS_VIVOS dias SIEMPRE se piden a la API, porque ahi es
        donde aparecen los avisos nuevos
      - el resto de la ventana sale de cache_busqueda, que esta en SQLite y
        sobrevive entre barridos y reinicios

    Resultado: 2 peticiones por barrido en vez de 4, y si el tope esta alto
    el coste se amortiza aun mas porque la parte vieja no se vuelve a pedir
    en todo el dia.

    La primera vez que corre (base vacia) si hace falta pedir la parte vieja
    una vez para llenarla.
    """
    import store

    if stats is not None:
        stats.update(bloques_ok=0, bloques_error=0, total_api=0,
                     errores=[], desde_cache=0)

    if not config.SAM_API_KEY:
        raise SamError("SAM_API_KEY no configurada")
    if _proximo_acceso:
        raise SamError(
            f"SAM.gov tiene el tope diario de peticiones alcanzado. "
            f"Vuelve a las {_proximo_acceso}."
        )

    dias = dias or config.DIAS_DE_VENTANA
    dias = max(dias, config.DIAS_POR_CHUNK)
    dias_vivos = max(config.DIAS_POR_CHUNK, min(config.DIAS_VIVOS, dias))
    fin = datetime.now(timezone.utc)
    vistos: set[str] = set()
    publicados = 0

    def emitir(opp: dict, de_cache: bool):
        nonlocal publicados
        nid = opp.get("noticeId")
        if not nid or nid in vistos:
            return None
        vistos.add(nid)
        publicados += 1
        if stats is not None:
            stats["desde_cache" if de_cache else "total_api"] += 1
        return opp

    # ---- Parte 1: lo viejo, desde la base ----
    viejos = store.desde_cache_rango(dias, dias_vivos)
    if not viejos and dias > dias_vivos:
        # Base vacia para esa parte: hay que llenarla una vez.
        log.info("Cache de la parte vieja vacio: se pide una vez a la API")
        desde_antiguo = fin - timedelta(days=dias)
        hasta_antiguo = fin - timedelta(days=dias_vivos)
        for i in range(config.MAX_CHUNKS):
            hasta = hasta_antiguo - timedelta(days=i * config.DIAS_POR_CHUNK)
            desde = hasta - timedelta(days=config.DIAS_POR_CHUNK)
            if desde < desde_antiguo:
                break
            try:
                for opp in _rango_vivo(desde, hasta, ptype):
                    if emitir(opp, False) is not None:
                        yield opp
                if stats is not None:
                    stats["bloques_ok"] += 1
            except SamError as e:
                if stats is not None:
                    stats["bloques_error"] += 1
                    stats["errores"].append(f"cache {desde.date()}: {e}")
                log.error("Fallo llenando la cache: %s", e)
                break
    else:
        for opp in viejos:
            if emitir(opp, True) is not None:
                yield opp
        log.info("Parte vieja de la ventana: %s avisos desde la cache (0 peticiones)",
                 len(vistos))

    # ---- Parte 2: lo reciente, siempre a la API ----
    for i in range(config.MAX_CHUNKS):
        hasta = fin - timedelta(days=i * config.DIAS_POR_CHUNK)
        desde = hasta - timedelta(days=config.DIAS_POR_CHUNK)
        if desde < fin - timedelta(days=dias_vivos):
            break
        try:
            lote = _rango_vivo(desde, hasta, ptype)
        except SamError as e:
            log.error("Fallo el bloque %s -> %s: %s", _fecha(desde), _fecha(hasta), e)
            if stats is not None:
                stats["bloques_error"] += 1
                stats["errores"].append(f"{_fecha(desde)}..{_fecha(hasta)}: {e}")
            continue
        if stats is not None:
            stats["bloques_ok"] += 1
        nuevos = 0
        for opp in lote:
            if emitir(opp, False) is not None:
                nuevos += 1
                yield opp
        log.info("Bloque %s..%s -> %s nuevos (total %s)",
                 _fecha(desde), _fecha(hasta), nuevos, publicados)


def proximo_acceso() -> str:
    """Cuando SAM.gov vuelve a dejar usar la API, o "" si no hay tope."""
    return _proximo_acceso or ""


def throttled() -> bool:
    return bool(_proximo_acceso)


def deadline_de(opp: dict) -> datetime | None:
    return _parse_fecha(opp.get("responseDeadLine"))


def dias_restantes(opp: dict) -> int:
    dl = deadline_de(opp)
    if not dl:
        return 999
    return (dl - datetime.now(timezone.utc)).days
