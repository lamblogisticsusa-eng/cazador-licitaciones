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
import threading
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

# Downloads de descripcion simultaneas. Cubre lo que se pidio con un
# asyncio.Semaphore(5), con el mismo efecto sobre la API.
#
# El cliente HTTP es requests, que es BLOQUEANTE: un asyncio.Semaphore no
# gobierna una llamada bloqueante, seria decoracion. httpx solo esta como
# dependencia indirecta de google-genai y no esta en requirements.txt, asi que
# hacer el async de verdad seria un cambio de despliegue sobre justo la ruta que
# baja los datos. Este semaforo va donde ocurre el trabajo: el pool de hilos.
#
# Se crea con el valor de config al importar, y hay una funcion para
# reconfigurarlo si en pruebas se quiere bajar.
def _nuevo_semaforo() -> "threading.Semaphore":
    return threading.Semaphore(max(1, int(config.MAX_DESCARGAS_CONCURRENTES)))


_DESCARGAS = _nuevo_semaforo()


def limite_descargas(n: int | None = None):
    """Fija el limite de descargas simultaneas. Sin argumento, devuelve el actual."""
    global _DESCARGAS
    if n is not None:
        config.MAX_DESCARGAS_CONCURRENTES = max(1, int(n))
        _DESCARGAS = threading.Semaphore(config.MAX_DESCARGAS_CONCURRENTES)
    return config.MAX_DESCARGAS_CONCURRENTES


# Rotacion del indice PSC. Sin esto, si siempre se Asking los primeros N codigos,
# los demas no se ven nunca: el starve es silencioso y no sale en ningun log.
_INDICE_ROTACION = 0

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


def fin_de_barrido() -> datetime:
    """
    El "ahora" del barrido.

    Se llama una vez por barrido y se usa para las dos ventanas, para que la
    de publicacion y la de vencimiento no se calculen con dos relojes
    distintos: si cruzara un medianoche entre una y otra, el bloque mas viejo
    del dia saldría con una fecha de vencimiento que no cuadra con el resto.
    """
    return datetime.now(timezone.utc)


def _fecha(dt: datetime) -> str:
    """SAM.gov exige MM/DD/YYYY con DIAGONALES. Nunca usar to_native de Windows,
    que en locale es-419 devuelve 09-23-2026 y la API responde 400."""
    return f"{dt.month:02d}/{dt.day:02d}/{dt.year:04d}"


def _parse_fecha(valor: str | None) -> datetime | None:
    if not valor:
        return None
    texto = valor.strip()
    # MM/DD/YYYY va primero porque es el formato que la API exige EN LAS
    # PETICIONES (postedFrom, postedTo, rdlfrom, rdlto). Antes no estaba, y
    # cualquier codigo que leyera de vuelta una de esas fechas recibia None en
    # silencio: _parse_fecha devolvia None sin error, que es la forma mas
    # dificil de detectar de un fallo.
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
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
    """Prueba de humo de la API. Se usa en /selftest y /estado.

    NUNCA lanza: si falla devuelve {"ok": False, ...}. El autodiagnostico
    existe para reportar problemas, asi que reventar al preguntar por el
    estado seria lo peor que podria hacer.
    """
    if not config.SAM_API_KEY:
        return {"ok": False, "detalle": "SAM_API_KEY no configurada"}
    if _proximo_acceso:
        return {
            "ok": False,
            "detalle": (
                f"Tope diario de SAM.gov alcanzado. Vuelve a las "
                f"{_proximo_acceso}."
            ),
            "throttled": True,
        }
    hoy = datetime.now(timezone.utc)
    params = {
        "api_key": config.SAM_API_KEY,
        "postedFrom": _fecha(hoy - timedelta(days=1)),
        "postedTo": _fecha(hoy),
        "limit": 1,
        "ptype": "o",
    }
    try:
        r = _get(params, reintentos=1)
    except SamError as e:
        return {
            "ok": False,
            "detalle": str(e)[:250],
            "throttled": "429" in str(e),
        }
    if r.status_code != 200:
        return {"ok": False, "detalle": f"HTTP {r.status_code}: {r.text[:200]}"}
    datos = r.json()
    return {
        "ok": True,
        "detalle": f"OK - {datos.get('totalRecords', 0)} avisos en 24h",
    }


def _espera(i: int) -> int:
    """
    Espera antes del reintento i, de la escala que se pidio: 2s, 4s, 8s.

    Antes era 2 ** i + 1, que daba 2, 3, 5. No era una escalera, era otra
    cosa. Ademas, cuando se pasaban de intentos, 2 ** i + 1 se iba a 9, 17,
    33... y un barrido podia quedarse bloqueado medio minuto por una sola
    llamada. Con la escala fija, el peor caso de una peticion es 14 segundos en
    total y se puede sumar de un vistazo.

    Si el numero de intentos supera la escala, se usa el ultimo valor en vez de
    crecer sin limite.
    """
    escala = tuple(config.BACKOFF_SEGUNDOS) or (2, 4, 8)
    return escala[min(i, len(escala) - 1)]


def _get(params: dict, reintentos: int | None = None) -> requests.Response:
    """
    Una peticion a SAM.gov, con reintentos y backoff escalonado.

    Reintenta en dos casos que antes se trataban distinto o no se trataban:

      - 429 y 5xx: igual que antes, pero con la escala 2/4/8 en vez de 2/3/5.
      - Timeout de LECTURA: antes caia en el mismo "Error de red" genérico y
        esperaba 1 segundo plano. Un timeout de lectura es tan transitorio como
        un 429 en esta API (es GSA saturandose), asi que ahora espera lo mismo
        que un 429.

    Los 400/401/403 no se reintentan: no se arreglan esperando.
    """
    intentos = config.MAX_REINTENTOS if reintentos is None else reintentos
    url = BASE_SEARCH if "postedFrom" in params else BASE_DESC
    ultima = None
    for i in range(intentos):
        try:
            r = _sesion.get(url, params=params, timeout=config.TIMEOUT_HTTP)
        except requests.Timeout as e:
            # Timeout de lectura: la peticion entro, la respuesta no llego.
            espera = _espera(i)
            log.warning("SAM.gov timeout de lectura, reintento %s en %ss",
                        i + 1, espera)
            ultima = SamError(f"Timeout de lectura: {e}")
            time.sleep(espera)
            continue
        except requests.RequestException as e:
            espera = _espera(i)
            log.warning("SAM.gov error de red, reintento %s en %ss",
                        i + 1, espera)
            ultima = SamError(f"Error de red: {e}")
            time.sleep(espera)
            continue
        if r.status_code == 200:
            return r
        if r.status_code in (429, 500, 502, 503, 504):
            espera = _espera(i)
            log.warning("SAM.gov %s, reintento %s en %ss",
                        r.status_code, i + 1, espera)
            ultima = SamError(f"HTTP {r.status_code}: {r.text[:300]}")
            throttle(r)
            time.sleep(espera)
            continue
        # 400/401/403 no se arreglan reintentando.
        raise SamError(f"HTTP {r.status_code}: {r.text[:300]}")
    raise ultima or SamError("Fallo desconocido")


def _descripcion_interna(opp: dict) -> str:
    """
    Texto que YA venia en la respuesta de la busqueda, si lo hubo.

    La documentacion del modulo (medido contra la API real en septiembre de
    2026) dice que `description` de /v2/search NO trae texto: trae una URL a
    /v1/noticedesc. Pero eso es lo que se vio entonces, no una garantia del
    contrato: si un dia la busqueda devuelve el texto ya dentro, seguir
    pidiendo noticedesc seria gastar una peticion del tope diario por aviso
    para volver a leer lo que ya teniamos en la mano.

    Por eso se comprueba antes de pagar la llamada. Cuando la busqueda trae
    una URL (lo normal), se devuelve "" y el llamador sigue el camino de
    siempre, que es el que funciona. Cuando trae texto de verdad, se devuelve
    ese y se ahorra la peticion.

    La distincion se hace por forma: una URL empieza por http. Un texto real
    no. Es una heuristica, no una garantia, asi que el criterio es
    conservador: si hay duda, se pide igual.
    """
    bruto = str(opp.get("description") or "").strip()
    if not bruto:
        return ""
    if bruto.lower().startswith("http"):
        return ""
    return limpiar_html(bruto)[: config.MAX_CHARS_DESCRIPCION]


def obtener_descripcion(notice_id: str, opp: dict | None = None) -> str:
    """
    Texto real de la oportunidad. Devuelve "" si falla (no revienta el escaneo).

    Ya devuelve "" cuando no hay descripcion, que es lo que pedia el
    requerimiento: el ciclo de barrido no se frena ni se reintenta. Lo que no
    habia era el atajo de arriba: si la busqueda ya trajo el texto, no hay
    que gastarse una peticion del tope diario en volver a pedirlo.

    NO se anade un segundo endpoint (/opportunityApi/v1/resources) a proposito.
    Ahi no hay descripciones: hay adjuntos, y sus nombres de archivo ("QRA_
    5345B_Contract.pdf") no alcanzan para que Gemini calcule costo por unidad
    ni margen. Medido el 29-sep-2026: el 53% de los avisos responden 404 en
    noticedesc, y anadir una llamada por cada uno multiplicaria ese gasto
    contra un tope diario que ya se agota ("SAM.gov me dijo que me calle"),
    para traer menos informacion que la que ya se tiene. Si algun dia hace
    falta, tiene que ir con un flag y medirse, no por defecto.
    """
    if opp is not None:
        ya_venia = _descripcion_interna(opp)
        if ya_venia:
            return ya_venia
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


def _bloque(desde: datetime, hasta: datetime, ptype: str,
            rdl_desde: str = "", rdl_hasta: str = "",
            ccode: str = "") -> tuple[list[dict], int]:
    """
    Un bloque de la ventana, acotado por fecha de vencimiento y por PSC.

    rdl_desde / rdl_hasta son las fechas MM/DD/YYYY de la ventana de
    vencimiento. Vacias significan "no mandarlas" y dejan el filtrado en el
    cliente, que es el comportamiento anterior.

    ccode es el codigo de 4 digitos de Product Service Code. Es UN valor, no
    una lista: la spec oficial lo declara type: string, mientras que ptype es
    type: array con collectionFormat: multi. Por eso el indice se recorre
    codigo a codigo en vez de mandar los 12 de golpe.

    Devuelve (lote, totalRecords). El total se necesita para avisar del
    truncamiento, y antes se perdia porque solo se devolvia el lote.
    """
    params = {
        "api_key": config.SAM_API_KEY,
        "postedFrom": _fecha(desde),
        "postedTo": _fecha(hasta),
        # offset=0 SIEMPRE: el offset de SAM.gov no pagina de forma confiable.
        "offset": 0,
        "limit": config.LIMITE_POR_CHUNK,
        "ptype": ptype,
    }
    if rdl_desde and rdl_hasta:
        params["rdlfrom"] = rdl_desde
        params["rdlto"] = rdl_hasta
    if ccode:
        params["ccode"] = str(ccode)

    r = _get(params)
    throttle(r)
    datos = r.json()
    lote = datos.get("opportunitiesData") or []

    # Truncamiento. offset esta roto (medido: offset=0,1,10 -> 100 registros;
    # offset=50,99,100,101,500 -> 0), asi que lo que no cabe en limit NO se
    # puede recuperar con otra llamada. Sin esta comprobacion, un bloque que
    # se llenara perderia avisos sin que nadie se enterara.
    #
    # Con los numeros que ya documenta este modulo (ptype=o,a -> 546 al dia) y
    # DIAS_POR_CHUNK=3, un bloque son ~1639 registros contra limit=1000: se
    # perdia un 39% en silencio. El filtro de vencimiento reduce el riesgo,
    # pero la comprobacion se queda.
    total = datos.get("totalRecords")
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = 0
    if total > len(lote):
        log.warning(
            "Bloque %s..%s%s truncado: SAM.gov dice %d registros y devolvio "
            "%d (limit=%d). Los %d que faltan NO se pueden recuperar con "
            "offset.",
            _fecha(desde), _fecha(hasta),
            (" PSC " + str(ccode)) if ccode else "",
            total, len(lote), config.LIMITE_POR_CHUNK, total - len(lote),
        )
    return lote, total


def _rango_vivo(desde: datetime, hasta: datetime, ptype: str,
               rdl_desde: str = "", rdl_hasta: str = "",
               ccode: str = "") -> list[dict]:
    """Pide un bloque a la API y lo cachea."""
    lote, _total = _bloque(desde, hasta, ptype, rdl_desde, rdl_hasta, ccode)
    for opp in lote:
        store.guardar_busqueda(opp)
    return lote


def _codigos_del_turno(presupuesto: int | None = None) -> list[str]:
    """
    Los codigos PSC que le tocan a ESTE barrido, ya rotados.

    Sin rotacion, si el indice tiene mas codigos que el presupuesto, se Asking
    siempre los primeros y los demas no se ven nunca. Eso es starve
    silencioso: no sale en ningun log, no da error, y simplemente una parte del
    indice nunca aparece.

    La rotacion avanza con el numero de codigos servidos, y es global, asi que
    sobrevive entre barridos dentro de la misma ejecucion.
    """
    indice = config.PSC_COMERCIAL_INDICE or {}
    plano: list[str] = []
    for grupo in config.COMERCIAL_PSC_GROUPS + sorted(
            k for k in indice if k not in config.COMERCIAL_PSC_GROUPS):
        for codigo in indice.get(grupo, []):
            plano.append(str(codigo))
    if not plano:
        return []
    tope = int(presupuesto if presupuesto is not None
               else config.MAX_CODIGOS_POR_BARRIDO)
    if tope <= 0 or tope >= len(plano):
        return plano
    global _INDICE_ROTACION
    _INDICE_ROTACION %= len(plano)
    tomados = plano[_INDICE_ROTACION:_INDICE_ROTACION + tope]
    if len(tomados) < tope:
        tomados += plano[:tope - len(tomados)]
    _INDICE_ROTACION = (_INDICE_ROTACION + tope) % len(plano)
    return tomados


def barrer(dias: int | None = None, ptype: str | None = None,
          stats: dict | None = None,
          indice_psc: bool = False) -> Iterator[dict]:
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
                     errores=[], desde_cache=0, psc_codigos=0,
                     psc_avisos=0)

    if not config.SAM_API_KEY:
        raise SamError("SAM_API_KEY no configurada")
    if _proximo_acceso:
        raise SamError(
            f"SAM.gov tiene el tope diario de peticiones alcanzado. "
            f"Vuelve a las {_proximo_acceso}."
        )

    # El tipo de aviso sale de config, con el valor medido. Antes era un
    # literal "o,a" en la firma; ahora se cambia en un sitio y vale para
    # cualquier llamada, incluida la que se hace desde /escaneo.
    ptype = ptype or config.PTYPE_SAM

    # Ventana de VENCIMIENTO, que es distinta de la ventana de PUBLICACION.
    # Se calcula una vez por barrido y se pasa a todos los bloques, para que
    # la API devuelva solo lo que aun se puede ofertar. El filtro es
    # INCLUSIVO: rdldesde=hoy y rdlhasta=hoy+45 no quitan los cercanos, los
    # de 1 a 10 dias siguen entrando. Amplia, no acota.
    horizon = max(0, int(config.HORIZON_VENCIMIENTO_DIAS or 0))
    if horizon:
        rdl_desde = _fecha(fin_de_barrido())
        rdl_hasta = _fecha(fin_de_barrido() + timedelta(days=horizon))
    else:
        rdl_desde = rdl_hasta = ""

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

    # ---- Parte 0: el indice PSC, codigo a codigo ----
    #
    # Se pide uno a uno porque ccode es type: string: un solo valor, no una
    # lista. La spec oficial lo dice claro, y por eso el indice de config son
    # codigos de 4 digitos y no los 8 grupos de 2 que se pidieron (que obligan
    # a 800 llamadas por barrido).
    #
    # Se recorre ADEMAS del recorrido por fecha de abajo, no en vez de el. El
    # indice acota QUE familias se miran; el recorrido por fecha acota el
    # rango temporal. Si solo se usara el indice, los dias de la ventana que no
    # entran en ningun codigo del turno se quedarian sin mirar, y con un
    # presupuesto de 10 codigos por barrido sobre 24, hay bloques enteros que
    # no tocan ningun codigo de ese turno.
    if indice_psc:
        codigos = _codigos_del_turno()
        if codigos:
            log.info("Indice PSC: %d codigos este barrido (de %d en el "
                     "indice)", len(codigos),
                     sum(len(v) for v in (config.PSC_COMERCIAL_INDICE or {}).values()))
            for codigo in codigos:
                try:
                    lote = _rango_vivo(fin - timedelta(days=dias_vivos), fin,
                                       ptype, rdl_desde, rdl_hasta, codigo)
                except SamError as e:
                    # Un 429 agotado en UN codigo no puede tirar el barrido
                    # entero: los otros codigos y el resto de la ventana siguen
                    # siendo validos. Se registra y se sigue.
                    log.warning("Fallo el codigo PSC %s: %s", codigo, str(e)[:120])
                    if stats is not None:
                        stats["bloques_error"] += 1
                        stats["errores"].append(f"ccode {codigo}: {str(e)[:80]}")
                    continue
                if stats is not None:
                    stats["bloques_ok"] += 1
                    stats["psc_codigos"] += 1
                    stats["psc_avisos"] += len(lote)
                for opp in lote:
                    if emitir(opp, False) is not None:
                        yield opp

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
                for opp in _rango_vivo(desde, hasta, ptype, rdl_desde, rdl_hasta):
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
            lote = _rango_vivo(desde, hasta, ptype, rdl_desde, rdl_hasta)
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
