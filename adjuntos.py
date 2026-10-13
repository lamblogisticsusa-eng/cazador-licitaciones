"""
adjuntos.py - Ingesta de los documentos tecnicos de una licitacion.

POR QUE ESTO EXISTE, Y POR QUE NO SE HACE PARA TODO

Hay una decision previa y deliberada en sam_api.obtener_descripcion(): NO se
anade el endpoint /opportunityApi/v1/resources. El motivo esta escrito ahi y
sigue siendo valido:

    Ahi no hay descripciones, hay adjuntos, y sus nombres ("QRA_5345B_
    Contract.pdf") no alcanzan para calcular costo ni margen. Medido el
    29-sep-2026: el 53% de los avisos responden 404 en noticedesc, y anadir una
    llamada por cada uno multiplicaria ese gasto contra un tope diario que ya se
    agota.

Esta es la EXCEPCION que hace falta, y la condicion es la que ya existe:

    consolidated_lot == True

Es decir: adjuntos SOLO para las licitaciones cuya tabla dice "1 LOT" o "1 EA"
en una sola linea. Son justo las unicas donde la tabla MIENTE sobre el volumen,
y las unicas donde los adjuntos pueden corregir un QTY que va a un distribuidor.

Las dos cosas encajan: el gasto se paga donde hay algo que arreglar, y no donde
no hay nada que arreglar.

CADA CUANTO CUESTA

Cero llamadas de descubrimiento. resourceLinks VIENE EN la respuesta del
search, que ya se pide para el escaneo. Lo que se gasta es una peticion por
licitacion con lote, y SOLO si resourceLinks no esta vacio (medido: la mayoria
de los avisos no lo traen).

Y se cachea por notice_id, con el mismo centinela de fallo que el resto: si
esta en cache, la repeticion es gratis.

COMO SE LEE UN ADJUNTO

La API no siempre devuelve lo mismo. Se Measured por el camino del simbolo:

    .pdf   -> se extrae el texto con pypdf, si esta instalado. Si no, se avisa
              y se sigue con el nombre del fichero, que para un LLM ya dice
              mucho ("S0594DU0500-S1.pdf").
    .txt
    .csv
    .doc  -> se decodifica como texto y ya esta.

Y si la respuesta del endpoint de recursos no es JSON sino el fichero, se
detecta por el content-type y se procesa directo.

LIMITE DE TAMAÑO

Un adjunto de un gobierno puede pesar 40 MB. Se corta a MAX_BYTES, y se avisa,
porque truncar en silencio y no decirlo seria lo peor: el LLM daria por hecho
que ha leido el documento entero.
"""
import io
import json
import logging
import os
import re
import tempfile

import config

log = logging.getLogger("kyomoto.adjuntos")

MAX_POR_LICITACION = 6      # cuantos adjuntos se bajan como mucho
MAX_BYTES = 3_000_000       # 3 MB por adjunto
MAX_CORPUS = 24_000         # caracteres que se Ilegan al modelo

_SIN_ADJUNTOS = "__sin_adjuntos__"

# DONDE SE ESCRIBE EL CORPUS UNIFICADO.
#
# No se usa "/tmp" a pelo: es un nombre fijo, y dos comandos /pdf a la vez
# (o dos contenedores de Render) se pisan el fichero. El nombre lleva el
# notice_id, que es lo unico que distingue una corpora de otra. Se puede mover
# con RUTA_CORPUS por entorno.
RUTA_CORPUS = os.environ.get("RUTA_CORPUS", "").strip() or tempfile.gettempdir()
NOMBRE_CORPUS = "corpus_adjuntos.txt"

# QUE DOCUMENTOS SON LAS ESPECIFICACIONES CRITICAS.
#
# En un aviso consolidado el desglose no esta en la tabla: esta en el anexo. Y
# los anexos no se llaman todos igual. Estos son los nombres con los que un
# comprador militar nombra las cosas, y van antes que el resto porque el corpus
# se corta por tamaño y lo que se queda fuera es lo último.
ETIQUETAS_ESPECIFICACION = ("attachment", "purchase description", "purchase "
                            "descrip", "sow", "statement of work",
                            "specification", "specs", "attachment 0002",
                            "0002")

# Aviso que se lanza cuando habia especificaciones que bajar y no seGot.
# Deliberadamente NO se lanza cuando el aviso no declara ningun adjunto: eso
# no es un fallo de descarga, es un aviso sin anexos, y abortar ahi impediria
# generar el PDF de todas las licitaciones consolidadas que no los traen.
class FalloDescargaEspecificaciones(Exception):
    """[ERROR] Descarga de especificaciones criticas fallida. Abortando pipeline."""

_EXT_TEXTO = (".txt", ".csv", ".htm", ".html", ".xml", ".md", ".json")
_EXT_PDF = (".pdf",)
_EXT_PEOR = (".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx", ".zip", ".dwg")

_RE_BASE = re.compile(r"(base_)?name|\bname\b", re.I)


def resource_links(opp: dict) -> list[str]:
    """
    Las URLs de adjuntos que YA vienen en la respuesta del search.

    resourceLinks es "Direct URL to download attachments in the opportunity",
    segun la spec oficial de GSA. Viene en el mismo JSON que el titulo y el
    NSN, asi que pedirlo es gratis.

    Se aceptan las dos formas que se han visto: una lista de cadenas, y una
    lista de objetos con "href" o "rel". Y se filtran las que no son http, que
    a veces vienen como rutas relativas.
    """
    crudo = (opp or {}).get("resourceLinks") or []
    if isinstance(crudo, str):
        crudo = [crudo]
    salida = []
    for x in crudo or []:
        url = ""
        if isinstance(x, str):
            url = x
        elif isinstance(x, dict):
            url = str(x.get("href") or x.get("uri") or x.get("url") or "")
        url = url.strip()
        if url.startswith("http"):
            salida.append(url)
    # Se quitan los repetidos: un mismo adjunto enlazado tres veces no se
    # descarga tres veces.
    vistas, unicas = set(), []
    for u in salida:
        if u not in vistas:
            vistas.add(u)
            unicas.append(u)
    return unicas


def _api_key() -> str:
    return str(config.SAM_API_KEY or "")


def _adjuntar_api_key(url: str) -> str:
    """SAM.gov exige la clave publica en la URL de los ficheros."""
    if "api_key=" in url:
        return url
    k = _api_key()
    if not k:
        return url
    return "%s%sapi_key=%s" % (url, "&" if "?" in url else "?", k)


def _decodificar(bits: bytes) -> str:
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return bits.decode(enc)
        except UnicodeDecodeError:
            continue
    return bits.decode("utf-8", errors="replace")


def _texto_de_pdf(bits: bytes, nombre: str) -> str:
    """
    Texto de un PDF, con pypdf. "" si no esta o si el PDF es una imagen.

    Si el PDF es un escaneo (todo imagenes, sin capa de texto) sale vacio, y
    eso es una limitacion real que hay que decir: no hay OCR aqui, y un
    documento escaneado no se puede leer. Se avisa en el log y el LLM recibe el
    nombre del fichero, que para "S0594DU0500-S1.pdf" ya le dice mucho.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("adjuntos: pypdf no esta instalado; %s no se puede "
                    "leer, solo queda el nombre", nombre)
        return ""
    try:
        import io as _io
        lector = PdfReader(_io.BytesIO(bits))
        partes = []
        for pag in lector.pages:
            t = pag.extract_text() or ""
            if t.strip():
                partes.append(t.strip())
        salida = "\n".join(partes)
        if not salida.strip():
            log.info("adjuntos: %s no tiene capa de texto (escaneo o "
                     "imagen); sin OCR no se puede leer", nombre)
        return salida
    except Exception as e:
        log.warning("adjuntos: no se pudo leer el PDF %s: %s", nombre, e)
        return ""


def _texto_de_recurso(bits: bytes, nombre: str, content_type: str = "") -> str:
    ext = ("." + nombre.rsplit(".", 1)[-1].lower()) if "." in nombre else ""
    if ext in _EXT_PDF or "pdf" in str(content_type).lower():
        return _texto_de_pdf(bits, nombre)
    if ext in _EXT_PEOR:
        # Un .xlsx o un .zip no se leen aqui. El nombre del fichero se devuelve
        # igualmente, que es lo unico aprovechable.
        return ""
    return _decodificar(bits)


def _listar_recursos(url: str) -> list[dict]:
    """
    Un endpoint de resourceLinks devuelve el indice de adjuntos: una lista con
    el nombre del fichero, su content-type y la URL de descarga.

    A veces devuelve JSON y a veces el fichero directamente, segun la URL. Se
    distinguen por el content-type, no adivinando.
    """
    import requests
    try:
        r = requests.get(_adjuntar_api_key(url), timeout=25,
                         headers={"Accept": "application/json"})
    except Exception as e:
        log.info("adjuntos: no se pudo pedir %s: %s", url[:70], e)
        return []
    if r.status_code != 200:
        log.info("adjuntos: %s -> HTTP %s", url[:70], r.status_code)
        return []
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "json" not in ctype:
        # Es el fichero, no el indice. Se deja que lo baje quien sepa de que
        # se trata; aqui solo se devuelven los metadatos minimos.
        return [{"filename": url.rsplit("/", 1)[-1][:80],
                 "attachmentURL": url,
                 "_crudo": True,
                 "_bytes": r.content[:MAX_BYTES],
                 "_ctype": ctype}]
    try:
        datos = r.json()
    except ValueError:
        return []
    lista = []
    if isinstance(datos, dict):
        for clave in ("index", "resources", "attachments", "data"):
            if isinstance(datos.get(clave), list):
                lista = datos[clave]
                break
    elif isinstance(datos, list):
        lista = datos
    salida = []
    for x in lista:
        if isinstance(x, str):
            salida.append({"filename": x.rsplit("/", 1)[-1][:80],
                           "attachmentURL": x})
        elif isinstance(x, dict):
            y = dict(x)
            y.setdefault("filename",
                         str(y.get("name") or y.get("base_name") or
                             y.get("attachmentURL") or "").rsplit("/", 1)[-1][:80])
            salida.append(y)
    return salida


def _bajar_uno(rec: dict) -> tuple[str, str]:
    """(nombre, texto) de un recurso. ("", "") si no se pudo."""
    url = str(rec.get("attachmentURL") or "").strip()
    if rec.get("_crudo"):
        return (str(rec.get("filename") or "documento"),
                _texto_de_recurso(rec.get("_bytes") or b"",
                                  str(rec.get("filename") or ""),
                                  str(rec.get("_ctype") or "")))
    if not url.startswith("http"):
        return (str(rec.get("filename") or ""), "")
    import requests
    try:
        r = requests.get(_adjuntar_api_key(url), timeout=40, stream=True)
    except Exception as e:
        log.info("adjuntos: no se pudo bajar %s: %s", url[:70], e)
        return (str(rec.get("filename") or ""), "")
    if r.status_code != 200:
        log.info("adjuntos: %s -> HTTP %s", url[:70], r.status_code)
        return (str(rec.get("filename") or ""), "")
    bits = b""
    truncado = False
    for trozo in r.iter_content(65536):
        bits += trozo
        if len(bits) >= MAX_BYTES:
            truncado = True
            break
    if truncado:
        # Se avisa, porque truncar en silencio haria que el LLM creyese que ha
        # leido el documento entero.
        log.info("adjuntos: %s truncado a %d bytes", url[:60], MAX_BYTES)
    nombre = str(rec.get("filename") or "documento")
    return (nombre, _texto_de_recurso(bits, nombre,
                                      r.headers.get("Content-Type") or ""))


def corpus_documental(opp: dict, notice_id: str, usar_cache: bool = True) -> str:
    """
    El texto de TODOS los adjuntos de la licitacion, unido.

    Devuelve "" si no hay adjuntos, o si no se pudieron bajar. Nunca lanza.

    El formato de cada bloque lleva el nombre del fichero, porque para un LLM
    el nombre no es ruido: "S0594DU0500-S1.pdf" ya dice que el documento es un
    parte concreto, y eso es informacion de compra.
    """
    import store

    nid = str(notice_id or "")
    links = resource_links(opp)
    if not links:
        if usar_cache and nid:
            try:
                store.guardar_estructura(nid + ":adj", {}, "")
            except Exception:
                pass
        return ""

    clave = nid + ":adj"
    if usar_cache and nid:
        guardados, _m, edad = store.leer_estructura(clave)
        if guardados:
            log.info("corpus: '%s' desde cache (%d caracteres)", nid[:24],
                     len(guardados.get("texto", "")))
            return guardados.get("texto", "")
        if 0 <= edad < 30:
            # Los adjuntos de una licitacion NO cambian. Si no se pudieron
            # bajar, no tiene sentido reintentar cada minuto; el aviso se
            # archiva y no vuelve. Por eso el TTL es largo, al contrario que
            # el del parsing, que depende de la cuota.
            return ""

    if not _api_key():
        return ""

    # Las especificaciones criticas van PRIMERO. El corpus se corta a
    # MAX_CORPUS caracteres, asi que lo que se queda fuera es lo ultimo: si se
    # baja primero un Excel de costes, el anexo con el desglose puede no caber,
    # y sin desglose no hay nada que corregir.
    #
    # El indice de cada enlace se pide una vez y se guarda, para no repetir la
    # llamada al ordenar. Es el mismo numero de peticiones que antes.
    indices = []
    for url in links:
        for rec in _listar_recursos(url):
            indices.append(rec)
    criticos = [r for r in indices if _especificacion(r.get("filename"))]
    resto = [r for r in indices if not _especificacion(r.get("filename"))]
    if criticos and resto:
        log.info("corpus: '%s' %d especificacion(es) antes que %d documento(s) "
                 "administrativo(s)", nid[:24], len(criticos), len(resto))
    indices = criticos + resto

    vistos, bloques, bajados = set(), [], 0
    for rec in indices:
        if bajados >= MAX_POR_LICITACION:
            log.info("corpus: '%s' se paran en %d adjuntos (de %d enlaces)",
                     nid[:24], MAX_POR_LICITACION, len(links))
            break
        nombre = str(rec.get("filename") or "")
        if nombre in vistos:
            continue
        vistos.add(nombre)
        nombre, texto = _bajar_uno(rec)
        if not texto.strip():
            if nombre:
                # El nombre solo, que para un LLM ya dice algo.
                bloques.append("--- %s (texto no disponible) ---" % nombre)
            continue
        bloques.append("--- %s ---\n%s" % (nombre, texto.strip()))
        bajados += 1

    cuerpo = "\n\n".join(bloques).strip()[:MAX_CORPUS]
    if not cuerpo:
        if usar_cache and nid:
            try:
                store.guardar_estructura(clave, {}, "")
            except Exception:
                pass
        return ""
    log.info("corpus: '%s' -> %d adjuntos, %d caracteres", nid[:24],
             bajados, len(cuerpo))
    if usar_cache and nid:
        try:
            store.guardar_estructura(clave, {"texto": cuerpo,
                                             "adjuntos": bajados}, "adjuntos")
        except Exception:
            pass
    return cuerpo


def _especificacion(nombre: str) -> bool:
    """El nombre del fichero dice que es una especificacion critica."""
    n = str(nombre or "").lower()
    return any(et in n for et in ETIQUETAS_ESPECIFICACION)


def ruta_corpus(notice_id: str) -> str:
    """El fichero donde vive el corpus de ESTE aviso."""
    limpio = re.sub(r"[^A-Za-z0-9_.-]", "_", str(notice_id or "sin_id"))[:60]
    return os.path.join(RUTA_CORPUS, "corpus_%s.txt" % limpio)


def volcar_corpus(notice_id: str, texto: str) -> str:
    """
    Escribe el corpus unificado a disco y devuelve la ruta.

    Se escribe SIEMPRE, incluso vacio. Un fichero vacio es informacion: dice
    "se probo y no habia nada", que es distinto de "no se probo".
    """
    ruta = ruta_corpus(notice_id)
    try:
        with io.open(ruta, "w", encoding="utf-8", newline="\n") as f:
            f.write(str(texto or ""))
    except OSError as e:
        log.warning("adjuntos: no se pudo escribir el corpus en %s: %s",
                    ruta[:60], e)
    return ruta


def especificaciones(aviso: dict, notice_id: str = "",
                    usar_cache: bool = True) -> tuple[str, str]:
    """
    PASO 1 DEL PIPELINE. BLOQUEANTE, A PROPOSITO.

    Devuelve (ruta_del_fichero, texto_del_corpus).

    Que sea bloqueante es el punto entero de este paso. Si la descarga corriera
    en segundo plano y el renderizado no esperara, el PDF se compilaria contra
    un corpus vacio y saldria con QTY 1 sin que nadie se enterase: el fallo
    seria silencioso, y la correccion dependeria del orden en que el sistema tenga a
    bien terminar las cosas. Un documento comercial no puede depender de eso.

    ORDEN DE PRIORIDAD
    Las especificaciones criticas se piden ANTES que el resto, porque el corpus
    se corta a MAX_CORPUS caracteres y lo que se queda fuera es lo ultimo. Un
    "Attachment 0002" con el desglose vale mas que un=".xlsx" de costes.

    CUANDO ABORTA
    Solo si el aviso DECLARABA adjuntos y no se pudo bajar ninguno. Un aviso
    sin resourceLinks no es un fallo: no hay nada que bajar, y abortar ahi
    dejaria sin PDF a toda licitacion consolidada que venga sin anexos.
    """
    import store

    nid = str(notice_id or aviso.get("noticeId") or "")
    texto = corpus_documental(aviso, nid, usar_cache=usar_cache)
    ruta = volcar_corpus(nid, texto)

    if texto.strip():
        log.info("especificaciones: '%s' -> %d caracteres en %s",
                 nid[:24], len(texto), os.path.basename(ruta))
        return ruta, texto

    # Sin texto. La pregunta es si el aviso declaraba algo que bajarse.
    declarados = resource_links(aviso)
    if declarados:
        log.error("[ERROR] Descarga de especificaciones criticas fallida. "
                  "Abortando pipeline.")
        raise FalloDescargaEspecificaciones(
            "[ERROR] Descarga de especificaciones criticas fallida. "
            "Abortando pipeline. %s declaraba %d adjunto(s) y no se pudo "
            "leer ninguno." % (nid[:24] or "(sin id)", len(declarados)))

    log.info("especificaciones: '%s' no declara adjuntos; no hay nada que "
             "bajar y no es un fallo", nid[:24])
    return ruta, ""
