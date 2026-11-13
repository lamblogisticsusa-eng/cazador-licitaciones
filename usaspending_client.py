# -*- coding: utf-8 -*-
"""
usaspending_client.py - Historial de adjudicaciones federales (USAspending).

QUE ES
La base publica de todo lo que el gobierno de EE.UU. ha adjudicado, con
los premios, los montos, las fechas y los contratistas. No exige
autenticacion (medido: los tres endpoints que se usan aqui responden 200
sin clave), asi que no hay cuota que gestionar ni secreto que rotar.

QUE NO PROMETE, Y ESTO ES LO MAS IMPORTANTE DEL MODULO
Devuelve MONTOS DE PREMIO, no precios unitarios. USAspending no publica
precio por unidad: publica cuanto se obligo en cada contrato. Decirle al
cliente "el precio historico del NSN es $127,849" seria mentirle, porque
ese numero es el promedio de contratos completos. Por eso el bloque de la
ficha lo rotula como "Monto medio de adjudicacion" y lleva la nota de que
son montos de contrato. Un dato mal rotulado es peor que no tener dato.

COMO SE BUSCA, Y POR QUE NO ES COMO PEDIA EL ENUNCIADO
Lo ideal seria filtrar por NSN exacto. Medido el 13-nov-2026 contra la
API real, NO SE PUEDE:

  - keywords=['1650-01-672-3405']  -> 0 resultados
  - keywords=['NSN 1650-01-672-3405'] -> 0 resultados
  - keywords=['1650016723405']     -> 0 resultados
  - keywords=['NSN']               -> 3 resultados (control)

O sea: las descripciones SI traen NSNs ("NSN: 1325-01-008-1335", medido),
pero el buscador no tokeniza los guiones y un NSN entero no casa nunca.
Se comprueba y se dice, en vez de devolver 0 resultados haciendolos pasar
por "nadie compra esto".

Lo que SI funciona es el filtro por clase: el NSN esta estructurado como
FFCC-NN-XXX-YYYY, donde FFCC es la Federal Supply Class, que en FPDS es
ademas el PSC de 4 caracteres. Los 4 primeros digitos del NSN son,
literalmente, el codigo por el que hay que buscar.

TRAMPAS MEDIDAS EN LA API REAL (cada una costó una consulta de mas)
  - El filtro se llama psc_codes. La clave "psc" se IGNORA EN SILENCIO:
    con psc=['6135'] la API respondio 200 y devolvio premios con
    psc_code Q201, M181 y M1JZ. Usar "psc" habria dado cifras de otra
    clase presentadas como si fueran de esta.
  - transaction_spending_summary TAMBIEN ignora psc_codes: mismos
    resultados con y sin el filtro. No se usa, ni aunque devuelva la
    suma en una sola llamada.
  - El limite de spending_by_award es 100. 500 responde 422.
  - "Award Date" viene siempre null; sirve de campo pero NO de orden.
    Para ordenar por recencia toca "Base Obligation Date".

LAS CINCO PETICIONES (todas a la vez, asi que cuestan como una)
  1. spending_by_award_count            -> cuantos premios hay
  2. spending_by_category/psc           -> obligaciones totales de la clase
                                           (y el nombre de la clase)
  3. spending_by_award  por monto desc  -> el mayor y los contratistas
  4. spending_by_award  por monto asc   -> el menor
  5. spending_by_award  por fecha desc  -> el ultimo, y la muestra reciente
                                           de la que sale la tendencia
  La media es (2) / (1): obligaciones del periodo entre premios del
  periodo. Son dos niveles distintos de la misma ventana y se rotula
  exactamente asi, sin llamarlo "precio medio unitario".

NUNCA REVIENTA. Timeout, 429, 500, JSON mal formado o un endpoint que
cambia de nombre: todo devuelve el dict vacio con su motivo, y el
analisis sigue. Un servicio de terceros no puede tumbar el escaneo.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import date

import config

log = logging.getLogger("kyomoto.usaspending")

# Rutas relativas: la base se puede mover con USASPENDING_URL por entorno.
RUTA_PREMIOS = "/api/v2/search/spending_by_award/"
RUTA_CUENTA = "/api/v2/search/spending_by_award_count/"
RUTA_CLASE = "/api/v2/search/spending_by_category/psc/"

# A, B, C, D = BPA Call, Purchase Order, Delivery Order y Definitive
# Contract. Es el grupo "Contracts" completo; sin esto la API pide el
# filtro y devuelve 422.
TIPO_CONTRATO = ["A", "B", "C", "D"]

# Solo campos que se han medido respondiendo. "Award Date" entra porque
# a veces trae valor, pero nunca se usa para ordenar.
CAMPOS = ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency",
          "Description", "Start Date", "End Date", "Base Obligation Date",
          "Last Modified Date", "psc_code", "psc_description"]

CONTRATISTAS = 3
MUESTRA = 100
REINTENTOS = 1

# Cuanto se tarda en volver a preguntar por una clase. Los montos de una
# clase no cambian en dias: 7 dias es una eternidad aqui y es lo que
# evita gastar 5 peticiones en cada aviso de un barrido.
TTL_DIAS = 7


def _vacio(nsn: str = "", psc: str = "", motivo: str = "") -> dict:
    """
    La forma completa, con todo a None/[] y sin_datos=True.

    Se devuelve la forma entera aunque no haya datos, y no un {} vacio:
    quien consume (el prompt y la ficha) tiene una sola estructura que
    mirar, y "no hay datos de esta clase" es una respuesta, no un error.
    Los montos van en None y no en 0, porque 0 significaria "cero dolares
    adjudicados", que es un dato falso y ademas muy distinto.
    """
    return {
        "nsn": nsn or "",
        "psc": psc or "",
        "criterio": "",
        "psc_nombre": "",
        "ventana_meses": 0,
        "desde": "",
        "hasta": "",
        "premios": 0,
        "monto_total_usd": None,
        "monto_medio_usd": None,
        "monto_min_usd": None,
        "monto_max_usd": None,
        "ultimo": {},
        "contratistas": [],
        "muestra": 0,
        "tendencia": "SIN_DATOS",
        "ultimo_vs_media_pct": None,
        "fuente": "USAspending.gov",
        "sin_datos": True,
        "motivo": motivo,
    }


def ventana(meses: int) -> tuple[str, str]:
    """(desde, hasta) de los ultimos `meses` calendario, en ISO."""
    meses = max(1, int(meses or 36))
    hoy = date.today()
    total = hoy.year * 12 + (hoy.month - 1) - meses
    anio, mes = divmod(total, 12)
    dia = min(hoy.day, calendar.monthrange(anio, mes + 1)[1])
    return date(anio, mes + 1, dia).isoformat(), hoy.isoformat()


def fsc_de_nsn(nsn: str) -> str:
    """
    Los 4 primeros digitos de un NSN: su Federal Supply Class.

    Un NSN es FFCC-NN-XXX-YYYY. FFCC es la clase, y en FPDS esa clase es
    el PSC de 4 caracteres con el que se puede filtrar. Si el NSN no
    tiene 13 digitos no se toca nada: los 4 primeros digitos de cualquier
    otra cosa no son una clase.
    """
    t = str(nsn or "").strip().upper().replace("-", "")
    if len(t) != 13 or not t.isdigit():
        return ""
    return t[:4]


def limpiar_psc(psc) -> str:
    """El PSC tal cual, o "" si no tiene forma de PSC (2-4 alfanumericos)."""
    t = "".join(ch for ch in str(psc or "").strip().upper() if ch.isalnum())
    return t if 2 <= len(t) <= 4 else ""


def criterio(nsn: str, psc: str) -> tuple[str, str, str]:
    """
    (clave, valor, texto) con el que se va a buscar.

    El NSN manda: su clase es mas especifica que el PSC que trae el
    aviso, que muchas veces es el de la familia y no del item. Si no hay
    NSN se usa el del aviso. Si no hay ninguno, ("", "", "") y no se
    pide nada, que es mejor que buscar en todo.
    """
    clase = fsc_de_nsn(nsn)
    if clase:
        return "psc_codes", clase, "FSC %s (clase del NSN)" % clase
    clase = limpiar_psc(psc)
    if clase:
        return "psc_codes", clase, "PSC %s (del aviso)" % clase
    return "", "", ""


# --------------------------------------------------------------------------
# Red
# --------------------------------------------------------------------------

def _url(ruta: str) -> str:
    base = str(getattr(config, "USASPENDING_URL", "") or "").rstrip("/")
    return (base or "https://api.usaspending.gov") + ruta


def _cuerpo(psc: str, desde: str, hasta: str) -> dict:
    """El filtro comun a los tres endpoints."""
    return {
        "award_type_codes": list(TIPO_CONTRATO),
        "time_period": [{"start_date": desde, "end_date": hasta}],
        "psc_codes": [psc],
    }


async def _post(ruta: str, cuerpo: dict) -> dict:
    """
    POST con un reintento. {} si algo falla, nunca una excepcion.

    El transporte esta en su propia funcion a proposito: los tests lo
    sustituyen y el modulo entero corre sin salir de la maquina, que es
    la unica forma de tener una suite estable.
    """
    try:
        import httpx
    except ImportError:
        log.warning("usaspending: httpx no esta instalado; no se puede "
                    "consultar el historial de precios")
        return {}

    timeout = float(getattr(config, "USASPENDING_TIMEOUT", 12) or 12)
    ultimo_error = ""
    for intento in range(REINTENTOS + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout,
                                         follow_redirects=True,
                                         headers={"Accept": "application/json",
                                                  "User-Agent":
                                                  "Kyomoto/2.0"}) as cliente:
                r = await cliente.post(_url(ruta), json=cuerpo)
            if r.status_code == 200:
                try:
                    datos = r.json()
                except ValueError:
                    return {}
                return datos if isinstance(datos, dict) else {}
            ultimo_error = "HTTP %s" % r.status_code
            # 4xx salvo 429: reintentar lo mismo solo repite el mismo 400.
            if 400 <= r.status_code < 500 and r.status_code != 429:
                break
        except Exception as e:
            ultimo_error = str(e)
        if intento < REINTENTOS:
            await asyncio.sleep(1)
    if ultimo_error:
        log.info("usaspending: %s -> %s", ruta, ultimo_error[:120])
    return {}


def _num(v):
    """float o None. Un monto no numerico es ausencia, no cero."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _premios_de(datos: dict) -> int:
    res = (datos or {}).get("results")
    if isinstance(res, dict):
        for clave in ("contracts", "contract", "total"):
            n = _num(res.get(clave))
            if n is not None:
                return int(n)
    return 0


def _clase_de(datos: dict) -> tuple[float | None, str]:
    """(obligaciones totales, nombre de la clase) del agrupado por PSC."""
    res = (datos or {}).get("results")
    if isinstance(res, list):
        for fila in res:
            if isinstance(fila, dict):
                return _num(fila.get("amount")), str(fila.get("name") or "")
    return None, ""


def _filas_de(datos: dict) -> list[dict]:
    res = (datos or {}).get("results")
    return [x for x in (res or []) if isinstance(x, dict)]


async def _consultar(nsn: str, psc: str, meses: int) -> dict:
    """Las cinco peticiones, a la vez. Nunca lanza."""
    clave, valor, texto = criterio(nsn, psc)
    if not valor:
        return _vacio(nsn, psc, "sin codigo NSN ni PSC en el aviso")

    desde, hasta = ventana(meses)
    filtros = _cuerpo(valor, desde, hasta)
    campos = list(CAMPOS)

    try:
        p_cuenta, p_total, p_max, p_min, p_ult = await asyncio.gather(
            _post(RUTA_CUENTA, {"filters": filtros}),
            _post(RUTA_CLASE, {"filters": filtros, "category": "psc",
                               "page": 1, "limit": 5, "subawards": False}),
            _post(RUTA_PREMIOS, {
                "filters": filtros, "fields": campos, "page": 1, "limit": 10,
                "sort": "Award Amount", "order": "desc", "subawards": False}),
            _post(RUTA_PREMIOS, {
                "filters": filtros, "fields": campos, "page": 1, "limit": 1,
                "sort": "Award Amount", "order": "asc", "subawards": False}),
            _post(RUTA_PREMIOS, {
                "filters": filtros, "fields": campos, "page": 1,
                "limit": MUESTRA, "sort": "Base Obligation Date",
                "order": "desc", "subawards": False}),
        )
    except Exception as e:
        log.info("usaspending: consulta fallida para %s: %s", valor, e)
        return _vacio(nsn, psc, "consulta fallida")

    premios = _premios_de(p_cuenta)
    total, nombre_clase = _clase_de(p_total)
    filas_max = _filas_de(p_max)
    filas_min = _filas_de(p_min)
    filas_ult = _filas_de(p_ult)

    if not (premios or total or filas_max or filas_ult):
        return _vacio(nsn, psc, "sin premios registrados para esta clase")

    out = _vacio(nsn, psc, "")
    out.update({
        "criterio": texto,
        "psc_nombre": nombre_clase,
        "ventana_meses": int(meses),
        "desde": desde,
        "hasta": hasta,
        "premios": premios,
        "monto_total_usd": total,
        "monto_medio_usd": (total / premios)
        if (total is not None and premios) else None,
        "monto_max_usd": _num(filas_max[0].get("Award Amount"))
        if filas_max else None,
        "monto_min_usd": _num(filas_min[0].get("Award Amount"))
        if filas_min else None,
        "sin_datos": False,
    })

    # El ultimo premio, por fecha de obligacion (Award Date viene null).
    if filas_ult:
        f = filas_ult[0]
        out["ultimo"] = {
            "premio": str(f.get("Award ID") or ""),
            "monto_usd": _num(f.get("Award Amount")),
            "fecha": str(f.get("Base Obligation Date") or "")[:10],
            "contratista": str(f.get("Recipient Name") or ""),
            "agencia": str(f.get("Awarding Agency") or ""),
        }

    # Principales contratistas: los que se llevan las adjudicaciones
    # mayores de la ventana, en el orden en que salen. Sin inventar
    # ninguno: solo los que la API devolvio.
    vistos, top = set(), []
    for f in filas_max:
        nombre = str(f.get("Recipient Name") or "").strip()
        if not nombre or nombre in vistos:
            continue
        vistos.add(nombre)
        top.append({
            "nombre": nombre,
            "monto_usd": _num(f.get("Award Amount")),
            "premio": str(f.get("Award ID") or ""),
        })
        if len(top) >= CONTRATISTAS:
            break
    out["contratistas"] = top

    # Tendencia: la media de la muestra reciente contra la media de la
    # ventana entera. No es una serie temporal larga (para eso faltarian
    # agregados por periodo) y por eso se llama como es.
    out["muestra"] = len(filas_ult)
    out["tendencia"], out["ultimo_vs_media_pct"] = _tendencia(
        filas_ult, out["monto_medio_usd"])
    return out


def _tendencia(filas: list[dict], media_ventana):
    """
    (etiqueta, % de la media) comparando la muestra reciente con la media.

    "SIN_DATOS" cuando no hay con que comparar. Se prefiere no decir nada
    a pintar una flecha que no viene de ninguna parte.
    """
    montos = [_num(f.get("Award Amount")) for f in filas]
    montos = [m for m in montos if m is not None and m > 0]
    if len(montos) < 5 or not media_ventana or media_ventana <= 0:
        return "SIN_DATOS", None
    media_reciente = sum(montos) / len(montos)
    pct = (media_reciente - media_ventana) / media_ventana * 100
    if pct > 10:
        etiqueta = "ALZA"
    elif pct < -10:
        etiqueta = "BAJA"
    else:
        etiqueta = "ESTABLE"
    return etiqueta, round(pct, 1)


# --------------------------------------------------------------------------
# API publica del modulo
# --------------------------------------------------------------------------

async def consultar_precios_historicos(nsn: str, psc: str = None) -> dict:
    """
    El historial de adjudicaciones para un NSN y/o un codigo PSC/FSC.

    Nunca lanza. Si no hay ninguno de los dos, devuelve el dict vacio sin
    hacer ni una peticion. El NSN se convierte en su clase (ver
    `criterio`), que es la unica forma en que esta base lo admite.
    """
    try:
        meses = int(getattr(config, "USASPENDING_MESES", 36) or 36)
    except (TypeError, ValueError):
        meses = 36
    return await _consultar(str(nsn or ""), str(psc or ""), meses)


def correr(factory) -> dict:
    """El mismo puente sincrono que pdf_fetcher.correr, para el escaner."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as tp:
        return tp.submit(asyncio.run, factory()).result(timeout=120)


def _clave_cache(codigo: str) -> str:
    return "precios:" + str(codigo or "").strip().upper()


def _de_cache(clave: str) -> tuple[dict, float]:
    """
    (payload, edad_en_minutos) de la cache. ({}, -1.0) si no hay fila.

    Se devuelve la EDAD, y no solo si hay datos, porque el fracaso
    tambien se cachea: una clase sin premios, o una API que contesto 500
    a las tres de la manana, no se vuelve a preguntar el minuto
    siguiente. Sin la edad no se puede distinguir "nunca se pregunto" de
    "se pregunto y no salio", y las dos devuelven un dict vacio.
    """
    if not clave:
        return {}, -1.0
    try:
        import store
        guardados, _m, edad = store.leer_estructura(clave)
        return (guardados or {}), float(edad)
    except Exception:
        return {}, -1.0


def _a_cache(clave: str, datos: dict) -> None:
    """
    Guarda el acierto o el fracaso, con la misma forma: {"data": ...}.

    El fracaso se guarda con su motivo para poder decirlo despues en la
    ficha en vez de imprimir "sin datos" como si nunca se hubiera
    preguntado. Un "" ademas guardaria el centinela y la edad se
    perderia, que es justo lo que _de_cache necesita.
    """
    if not clave:
        return
    try:
        import store
        if datos and not datos.get("sin_datos"):
            store.guardar_estructura(clave, {"data": datos}, "usaspending")
        else:
            store.guardar_estructura(
                clave,
                {"data": {},
                 "motivo": str((datos or {}).get("motivo") or "")},
                "usaspending")
    except Exception:
        pass


def precios_del_aviso(opp: dict, *textos) -> dict:
    """
    El historial para ESTE aviso, cacheado por la clase.

    `textos` es por donde se mira el NSN: la descripcion oficial y el
    corpus de los PDFs de la Fase 7. Se separa del cliente a proposito:
    este modulo no sabe leer un aviso, solo sabe preguntar por un codigo,
    y quien sabe donde esta el NSN es quien ya lo estaba extrayendo.

    Nunca lanza. Sin codigo, con la consulta apagada o con la API caida
    devuelve el dict vacio con su motivo, y el analisis sigue.
    """
    if not getattr(config, "PRECIOS_HISTORICOS", True):
        return _vacio(motivo="consulta desactivada")

    nsn = ""
    for t in (textos or ()):
        nsn = _nsn_de(t)
        if nsn:
            break
    clave, valor, _texto = criterio(nsn, str((opp or {}).get(
        "classificationCode") or ""))
    if not valor:
        return _vacio(nsn, motivo="sin codigo NSN ni PSC en el aviso")

    fila, edad = _de_cache(_clave_cache(valor))
    datos_cache = fila.get("data") if isinstance(fila, dict) else None
    if isinstance(datos_cache, dict) and datos_cache:
        log.info("usaspending: %s desde cache", _clave_cache(valor))
        fuera = dict(datos_cache)
        fuera["nsn"] = nsn or fuera.get("nsn", "")
        return fuera
    if 0 <= edad < TTL_DIAS * 24 * 60:
        return _vacio(nsn, valor,
                      str(fila.get("motivo") or "")
                      or "sin premios registrados para esta clase")

    datos = correr(lambda: consultar_precios_historicos(
        nsn, str((opp or {}).get("classificationCode") or "")))
    _a_cache(_clave_cache(valor), datos)
    return datos


def _nsn_de(texto) -> str:
    """El NSN dentro de un texto, con el MISMO criterio que el analizador.

    Se duplica la regex aqui y no se importa gemini_analyzer: ese modulo
    importa este, y un circulo entre los dos dejaria el analisis sin
    poder arrancar. Son 4 lineas y estan escritas igual.
    """
    import re
    t = str(texto or "").strip().upper()
    if t.startswith("NSN"):
        t = t[3:].strip(" :")
    m = re.search(r"\b\d{4}-\d{2}-\d{3}-\d{4}\b", t)
    if m:
        return m.group(0)
    m = re.search(r"\b\d{13}\b", t)
    if m:
        d = m.group(0)
        return "%s-%s-%s-%s" % (d[:4], d[4:6], d[6:9], d[9:])
    return ""

