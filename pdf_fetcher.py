# -*- coding: utf-8 -*-
"""
pdf_fetcher.py - Los PDFs adjuntos de SAM.gov, bajados y leidos para el
analisis.

POR QUE EXISTE ESTE MODULO JUNTO A adjuntos.py

adjuntos.py ya baja los adjuntos, y ahi la descarga es BLOQUEANTE a
proposito: si corriera en segundo plano, el PDF del pipeline de /pdf se
compilaria contra un corpus vacio y saldria con QTY 1 sin que nadie se
enterase. Esa decision esta escrita alla y sigue en pie.

Este modulo va por otro camino, con otra mision: enriquecer el PROMPT que
se le manda a Gemini ANTES de analizar. Ahi si conviene el async, porque
son 6 o 7 ficheros por licitacion y bajarlos en serie alarga el analisis
sin motivo. La lectura es la misma idea (pypdf, sin OCR); el transporte y
el contrato son distintos:

    adjuntos -> sincrono, bloqueante, cacheado, para /pdf.
    pdf_fetcher -> asincrono, nunca lanza, para el prompt.

Sobre httpx: NO se anade a requirements.txt, y es deliberado. httpx ya
esta en el entorno como dependencia indirecta de google-genai, y
test_indice_psc.py comprueba que requirements.txt no lo lleve. Meterlo
ahi seria un cambio de despliegue sobre la ruta que baja los datos, que
es exactamente lo que el comentario de sam_api.py dice que no se toca.
Por eso el import es PEREZOSO: si un dia google-genai deja de traerlo,
este modulo avisa y devuelve "", y el analisis sigue con la descripcion
oficial, que es lo que hacia antes. Un extra no puede tumbar lo base.

LO QUE NO HACE

No inventa. Un PDF escaneado (solo imagenes, sin capa de texto) sale
vacio y se queda vacio: aqui NO hay OCR, y decirlo es mejor que
devolver un texto inventado. Un fichero corrupto o protegido con
password se salta en silencio (log, no excepcion): un adjunto ilegible
no puede tumbar el analisis de una licitacion entera.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile

import config

log = logging.getLogger("kyomoto.pdf_fetcher")

# Cuantos documentos de una licitacion se leen como mucho. El prompt se
# paga por caracter, y el anexo con el desglose esta casi siempre en los
# primeros: la mayoria de las licitaciones traen entre 1 y 4 adjuntos, asi
# que 7 cubre lo normal y corta lo excepcional.
MAX_PDFS = 7
# Un adjunto de un gobierno puede pesar 40 MB. Se corta, y se avisa.
MAX_BYTES = 3_000_000
# Caracteres que llegan al modelo. Igual que adjuntos.MAX_CORPUS, para que
# los dos caminos midan lo mismo.
MAX_CORPUS = 24_000
TIMEOUT_SEG = 20
# Descargas a la vez. Suficiente para que 7 ficheros no sean 7 tandas.
CONEXIONES = 4

# Extensiones de las que sale TEXTO legible. Un .dwg, un .zip, un .xlsx o
# una imagen no aportan nada al prompt: bajarlos seria tiempo y cuota
# contra nada. Los indices de SAM.gov no llevan extension y se quedan.
_DOC = (".pdf", ".txt", ".csv", ".htm", ".html", ".xml", ".md", ".json")

# Hasta donde sigue un indice de adjuntos. Un nivel: el indice apunta a
# ficheros, los ficheros no apuntan a nada. Sin esto, un JSON que
# contuviera su propia URL se leeria hasta el infinito.
_PROFUNDIDAD_MAX = 1


def _ext(url: str) -> str:
    """
    La extension del FICHERO de la URL, en minusculas. "" si no tiene.

    Se mira solo el ultimo tramo despues de quitar la query: en
    "https://x/gov/y.pdf?v=1" hay puntos en el host y en la query, y
    ninguno de ellos es una extension.
    """
    ruta = str(url or "").split("?", 1)[0].split("#", 1)[0]
    ultimo = ruta.rstrip("/").rsplit("/", 1)[-1]
    if "." not in ultimo:
        return ""
    return "." + ultimo.rsplit(".", 1)[-1].lower()


def _corte(max_pdfs) -> int:
    """
    Cuantos documentos se bajan como mucho.

    Un None o un "" es "sin especificar" y cae al maximo. Un valor no
    numerico tambien. Y por debajo de uno se pide AL MENOS uno: un 0 o un
    -3 pasados sin querer no pueden traducirse en "no bajar nada", que
    seria el silencio peor de todos, el que no avisa.
    """
    if max_pdfs is None:
        return MAX_PDFS
    try:
        valor = int(max_pdfs)
    except (TypeError, ValueError):
        return MAX_PDFS
    return max(1, valor)


def documentos(resource_links, max_pdfs: int = MAX_PDFS) -> list[str]:
    """
    Los enlaces que pueden llevar a un documento legible, sin repetidos y
    cortados a `max_pdfs`.

    Se queda con las URLs http cuyo nombre de fichero no tiene extension
    (son los indices de adjuntos de SAM.gov, que devuelven JSON con la
    lista) o cuya extension es de algo que se puede LEER.
    """
    limite = _corte(max_pdfs)
    vistas, salida = set(), []
    for x in resource_links or []:
        url = str(x or "").strip()
        if not url.startswith("http") or url in vistas:
            continue
        ext = _ext(url)
        if ext and ext not in _DOC:
            continue
        vistas.add(url)
        salida.append(url)
        if len(salida) >= limite:
            break
    return salida


# --------------------------------------------------------------------------
# Lectura
# --------------------------------------------------------------------------

def _decodificar(bits: bytes) -> str:
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return bits.decode(enc)
        except UnicodeDecodeError:
            continue
    return bits.decode("utf-8", errors="replace")


def _pdf(ruta: str, nombre: str) -> str:
    """
    El texto de TODAS las paginas de un PDF. "" si no hay texto.

    "" aqui significa dos cosas distintas y las dos se dicen en el log:
    que el PDF es un escaneo (solo imagenes) o que esta corrupto o
    protegido con password. En ninguno de los dos casos se inventa nada.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("pdf_fetcher: pypdf no esta instalado; %s no se puede "
                    "leer, solo queda el nombre", nombre)
        return ""
    try:
        lector = PdfReader(ruta)
        partes = []
        for pag in lector.pages:
            t = pag.extract_text() or ""
            if t.strip():
                partes.append(t.strip())
        texto = "\n".join(partes)
        if not texto.strip():
            log.info("pdf_fetcher: %s no tiene capa de texto (escaneo o "
                     "imagen); sin OCR no se puede leer", nombre)
        return texto
    except Exception as e:
        log.info("pdf_fetcher: %s ilegible (corrupto o protegido): %s",
                 nombre, e)
        return ""


@contextlib.contextmanager
def _temporal(bits: bytes, nombre: str, ctype: str = ""):
    """
    Los bytes a un fichero temporal, y el borrado al salir.

    Se borra SIEMPRE, tambien si la lectura falla: un tmpdir que se llena
    de PDFs de 40 MB un dia que alguien no mira es una parada de produccion.
    """
    suf = _ext(nombre) or _ext(nombre.replace("%20", " "))
    if not suf and "pdf" in str(ctype).lower():
        suf = ".pdf"
    ruta = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suf,
                                         prefix="kyomoto_pdf_") as f:
            f.write(bits)
            ruta = f.name
        yield ruta
    finally:
        if ruta:
            with contextlib.suppress(OSError):
                os.unlink(ruta)


def _leer_fichero(ruta: str, nombre: str, ctype: str) -> str:
    """Texto de un fichero ya bajado a disco. Nunca lanza."""
    try:
        if _ext(nombre) == ".pdf" or "pdf" in str(ctype).lower():
            return _pdf(ruta, nombre)
        with open(ruta, "rb") as f:
            return _decodificar(f.read())
    except Exception as e:
        log.info("pdf_fetcher: no se pudo leer %s: %s", nombre, e)
        return ""


# --------------------------------------------------------------------------
# Red
# --------------------------------------------------------------------------

async def _pedir(client, url: str) -> tuple[int, str, bytes]:
    """
    (status, content-type, bytes) de una URL, cortado a MAX_BYTES.

    Un solo sitio decide como se pide. Si anade un header o cambia de
    transporte, se cambia aqui y no en seis sitios. Se va por el flujo
    (stream) y no por .content a pelo: un .content descarga el fichero
    entero para despues tirar el 95%, que es como se acaba la memoria en
    un aviso con un Excel de 40 MB.
    """
    try:
        async with client.stream("GET", url) as r:
            ctype = str(r.headers.get("Content-Type") or "")
            if r.status_code != 200:
                return r.status_code, ctype, b""
            bits, cortado = b"", False
            async for trozo in r.aiter_bytes(65536):
                bits += trozo
                if len(bits) >= MAX_BYTES:
                    cortado = True
                    break
            if cortado:
                log.info("pdf_fetcher: %s truncado a %d bytes",
                         str(url)[:60], MAX_BYTES)
            return r.status_code, ctype, bits
    except Exception as e:
        log.info("pdf_fetcher: no se pudo bajar %s: %s", str(url)[:70], e)
        return 0, "", b""


def _es_indice(ctype: str, bits: bytes) -> bool:
    """La respuesta es la LISTA de adjuntos, no un adjunto."""
    if "json" in str(ctype).lower():
        return True
    return bits[:1].strip() in (b"{", b"[")


def _urls_de_indice(bits: bytes, tope: int = 20) -> list[str]:
    """
    Las URLs de descarga que trae un indice de adjuntos de SAM.gov.

    La API no promete forma: a veces es una lista de cadenas y a veces un
    objeto con "attachmentURL" dentro de tres niveles. Se barre entero
    buscando strings http, y van primero las que parecen documento, que
    son las que aportan texto.
    """
    try:
        datos = json.loads(bits.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return []
    encontradas: list[str] = []

    def barrer(x):
        if isinstance(x, str):
            if x.startswith("http") and x not in encontradas:
                encontradas.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                barrer(v)
        elif isinstance(x, list):
            for v in x:
                barrer(v)

    barrer(datos)
    encontradas.sort(key=lambda u: 0 if _ext(u) in _DOC else 1)
    return encontradas[:tope]


def _nombre_de(url: str, ctype: str) -> str:
    """El nombre del fichero, para el bloque del corpus y para el LLM."""
    base = str(url or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    base = base or "documento"
    if not _ext(base) and "pdf" in str(ctype).lower():
        base += ".pdf"
    return base[:80]


async def _leer(client, url: str, nivel: int = 0,
                tope: int = MAX_PDFS) -> list[tuple[str, str]]:
    """
    [(nombre, texto)] de una URL. Lista vacia si no hay nada legible.

    Si la respuesta es un INDICE (SAM.gov devuelve JSON con la lista de
    ficheros), se sigue hasta los ficheros de dentro, una sola vez. Y si
    el indice no trae ninguna URL, se trata como documento: asumir que
    "es un indice" y tirar el texto seria perder informacion por no
    comprobarlo.
    """
    status, ctype, bits = await _pedir(client, url)
    if status != 200 or not bits:
        return []
    nombre = _nombre_de(url, ctype)

    if nivel < _PROFUNDIDAD_MAX and _es_indice(ctype, bits):
        # Los hijos pasan por el MISMO filtro que los enlaces de entrada:
        # un indice puede traer un .xlsx o una imagen, y esos no aportan
        # texto al prompt. Bajarlos seria tiempo y cuota contra nada.
        hijos = documentos(_urls_de_indice(bits), tope)
        if hijos:
            respuestas = await asyncio.gather(
                *[_leer(client, h, nivel + 1, tope) for h in hijos],
                return_exceptions=True)
            salida: list[tuple[str, str]] = []
            for r in respuestas:
                if isinstance(r, Exception) or not r:
                    continue
                salida.extend(r)
                if len(salida) >= tope:
                    break
            return salida[:tope]

    with _temporal(bits, nombre, ctype) as ruta:
        texto = _leer_fichero(ruta, nombre, ctype)
    if texto.strip():
        return [(nombre, texto.strip())]
    # El nombre solo. Para un LLM "S0594DU0500-S1.pdf" ya dice mucho, y
    # es informacion de compra: el documento EXISTE y se llama asi.
    return [(nombre, "")]


async def descargar_y_extraer_pdfs(resource_links: list[str],
                                   max_pdfs: int = 7) -> str:
    """
    El texto de los PDFs adjuntos de SAM.gov, todo junto, en un solo str.

    Nunca lanza. Un fichero corrupto, una URL caida, una respuesta que no
    es un documento o un httpx que no esta: todo se salta y se sigue con
    lo que haya. Devuelve "" si no hay nada legible.
    """
    try:
        import httpx
    except ImportError:
        log.warning("pdf_fetcher: httpx no esta instalado; los adjuntos no "
                    "se pueden leer para el analisis, se sigue solo con la "
                    "descripcion oficial")
        return ""

    limite = _corte(max_pdfs)
    urls = documentos(resource_links, limite)
    if not urls:
        return ""

    try:
        semaforo = asyncio.Semaphore(max(1, CONEXIONES))

        async def con_tope(u):
            async with semaforo:
                return await _leer(client, u, 0, limite)

        async with httpx.AsyncClient(timeout=TIMEOUT_SEG,
                                     follow_redirects=True,
                                     headers={"User-Agent": "Kyomoto/2.0",
                                              "Accept": "*/*"}) as client:
            respuestas = await asyncio.gather(
                *[con_tope(u) for u in urls], return_exceptions=True)
    except Exception as e:
        log.warning("pdf_fetcher: descarga abortada: %s", e)
        return ""

    bloques, vistos = [], set()
    for r in respuestas:
        if isinstance(r, BaseException) or not r:
            continue
        for nombre, texto in r:
            if nombre in vistos:
                continue
            vistos.add(nombre)
            if texto:
                bloques.append("--- %s ---\n%s" % (nombre, texto))
            else:
                bloques.append("--- %s (texto no disponible) ---" % nombre)
            if len(bloques) >= limite:
                break
        if len(bloques) >= limite:
            break

    cuerpo = "\n\n".join(bloques).strip()
    if not cuerpo:
        return ""
    if len(cuerpo) > MAX_CORPUS:
        log.info("pdf_fetcher: corpus de %d caracteres cortado a %d",
                 len(cuerpo), MAX_CORPUS)
        cuerpo = cuerpo[:MAX_CORPUS]
    log.info("pdf_fetcher: %d documento(s), %d caracteres",
             len(bloques), len(cuerpo))
    return cuerpo


# --------------------------------------------------------------------------
# Puente sincrono y cache
# --------------------------------------------------------------------------

def correr(factory) -> str:
    """
    Ejecuta la descarga asincrona desde codigo sincrono.

    `factory` es una lambda que CREA la corutina, no la corutina: si el
    bucle no se llega a levantar, se cierra sin llegar a ejecutarse y no
    queda el aviso "coroutine never awaited".

    analizar() es sincrono y se llama desde un hilo (main.py lo mete en
    run_in_executor), asi que normalmente no hay bucle y asyncio.run
    basta. Si lo hubiera, se le da un bucle propio en otro hilo: meter
    asyncio.run encima de un bucle vivo lanza RuntimeError, y eso
    tumbaria el analisis por algo que no es el dato.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as tp:
        return tp.submit(asyncio.run, factory()).result(timeout=120)


def _links_de(opp: dict) -> list[str]:
    """Las URLs de adjuntos, con el parser que ya esta testeado."""
    try:
        import adjuntos
        return adjuntos.resource_links(opp or {})
    except Exception:
        return []


def corpus_para_analisis(opp: dict, max_pdfs: int = MAX_PDFS) -> str:
    """
    El texto de los adjuntos de ESTE aviso, cacheado por noticeId.

    Devuelve "" si no hay nada que bajar o si algo falla. NUNCA lanza:
    los adjuntos son un EXTRA del prompt, y un extra no puede tumbar un
    analisis que saldria bien solo con la descripcion oficial.

    La cache es por el mismo motivo que en adjuntos: los adjuntos de una
    licitacion no cambian, y el barrido vuelve a pasar por el mismo aviso
    cada dos horas. Se guarda tambien el fracaso, para no reintentar el
    minuto siguiente algo que ya se probo y no salio.
    """
    if not str(getattr(config, "SAM_API_KEY", "") or "").strip():
        return ""
    links = _links_de(opp)
    if not links:
        return ""

    nid = str((opp or {}).get("noticeId") or "")
    clave = (nid + ":pdf") if nid else ""
    if clave:
        try:
            import store
            guardados, _m, edad = store.leer_estructura(clave)
            if guardados.get("texto"):
                log.info("pdf_fetcher: '%s' desde cache (%d caracteres)",
                         nid[:24], len(guardados.get("texto", "")))
                return guardados.get("texto", "")
            # Mismo criterio que adjuntos: el fallo reciente no se
            # reintenta, porque los adjuntos no van a cambiar en 30 min.
            if 0 <= edad < 30:
                return ""
        except Exception:
            pass

    texto = correr(lambda: descargar_y_extraer_pdfs(links, max_pdfs))

    if clave:
        try:
            store.guardar_estructura(clave, {"texto": texto} if texto else {},
                                     "pdf")
        except Exception:
            pass
    return texto
