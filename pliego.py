# -*- coding: utf-8 -*-
"""
pliego.py - El pliego largo: bajarlo, limpiarlo y pegarlo.

LA FASE 9 nacio de una constatacion: el motor que decide si una licitacion
merece la pena no puede juzgar 3.000 caracteres. La descripcion que lee el
prompt viene cortada en config.MAX_CHARS_DESCRIPCION, y las clausulas que
deciden el expediente (SUBSAFE, Nivel I, radiografia, "repair welding is
not permitted") estan casi siempre mas abajo, en el HTML del pliego o en
un adjunto .docx. Se juzgaba con el resumen y se fallaba con el pliego.

Este modulo tiene dos mitades y estan separadas a proposito:

    LIMPIEZA (pura, sin imports del repositorio ni de red)
        de_html_a_texto, de_docx_a_texto, de_archivo, normalizar,
        texto_pegado. Todo es texto -> texto y se prueba con datos
        puestos a mano, sin mock de nada.

    INGESTA (texto_largo)
        baja la descripcion COMPLETA de SAM.gov y la cachea en SQLite
        por noticeId + fecha de actualizacion, para no pagar dos veces
        la misma peticion al tope diario. Los imports de sam_api y de
        store son perezosos DENTRO de la funcion: el modulo entero sigue
        pudiendose importar sin red, que es lo que necesitan los tests y
        lo que permite que sam_api importe de aqui la limpieza sin crear
    un ciclo.

LAS TRES REGLAS DE LA LIMPIEZA, que son lo que hace distinto a este
codigo de un `re.sub(r"<[^>]+>", "", html)`:

  1. Los saltos de linea se GUARDAN. `<p>`, `<li>`, `<br>`, `<tr>` y los
     encabezados se convierten en saltos, no se tiran a la basura: la
     numeracion de clausulas, las viñetas de los CDRL y la narrativa de
     las enmiendas se pierden si no, y sin numeracion no se puede citar
     una clausula en una pregunta al comprador.
  2. Los espacios repetidos se normalizan UNA sola vez y SOLO en
     horizontal. Es lo que hace legible un HTML con el fuente pegado a
     120 columnas sin tocar nada mas.
  3. Los identificadores NO se rompen. `N0010426QFF16`, `MIL-STD-2073`,
     `DI-MISC-80678` y `ASTM-B505` tienen que salir enteros. El unico
     peligro real es el corte de linea de MITAD del token que trae el
     fuente HTML (`MIL-STD-` en una linea y `2073` en la siguiente), y
     ese se coser de vuelta antes de normalizar. Lo que NO se hace es
     juntar lineas sin mas, porque eso borra los parrafos y con ellos
     la estructura del pliego.
"""
from __future__ import annotations

import html as _html
import io
import logging
import os
import re
import zipfile
import xml.etree.ElementTree as ET

import config

log = logging.getLogger("kyomoto.pliego")

# Extensiones que son texto de verdad y se limpian con este modulo.
EXT_RICO = (".docx", ".htm", ".html")

# Tope de caracteres del pliego largo que se guardan y se analizan. 60k
# cubre un pliego tipico con creces; mas alla solo se consume memoria y
# cuota de prompt para informacion que no se va a leer.
MAX_CHARS = 60000


# ---------------------------------------------------------------------------
# Normalizacion
# ---------------------------------------------------------------------------
# Etiquetas que abren o cierran un bloque: su contenido va en su propia
# linea. Los `<li>` se tratan aparte para poder poner la viñeta.
_BLOQUES = (
    "p", "div", "ul", "ol", "table", "tr", "section", "article",
    "blockquote", "pre", "hr", "thead", "tbody", "tfoot", "caption",
    "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "footer", "main", "aside", "form", "address",
)

# "MIL-STD-" \n "2073" -> hay que coserlo. El guion a mitad de linea es la
# unica forma en que un identificador se parte dentro de un fuente HTML;
# juntar lineas a ciegas en cambio borraria los parrafos, y eso si es
# destructivo.
_RE_COSTURA = re.compile(r"(?<=[\-–—/])[ \t]*\n[ \t]*")

_RE_ESPACIOS = re.compile(r"[^\S\n]{2,}")
_RE_SALTOS = re.compile(r"\n{3,}")


def normalizar(texto) -> str:
    """
    Espacios de la fuente sin tocar ni un identificador.

    Solo se juntan espacios HORIZONTALES repetidos: un espacio entre dos
    palabras pasa a ser uno, y `MIL-STD-2073` no lleva espacios, asi que
    lo que no se puede es, ademas, romper. Los saltos de linea se
    conservan y las lineas dobles se recortan a dos.
    """
    t = str(texto or "")
    if not t:
        return ""
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = _RE_COSTURA.sub("", t)
    t = t.replace("\u00a0", " ").replace("\u200b", "").replace("\t", " ")
    t = _RE_ESPACIOS.sub(" ", t)
    t = "\n".join(l.rstrip() for l in t.split("\n"))
    t = _RE_SALTOS.sub("\n\n", t)
    return t.strip()


def de_html_a_texto(bruto) -> str:
    """
    HTML a texto plano conservando la estructura del pliego.

    El orden de las operaciones importa y esta pensado:

        1. script, style y comentarios -> SE VAN CON SU CONTENIDO, no se
           sustituyen por espacio: un <style> de 400 lineas metiendo
           selectores CSS en medio del pliego solo ensucia el prompt.
        2. <br> y etiquetas de lista -> salto con viñeta.
        3. etiquetas de bloque -> salto de linea. De aqui salen los
           parrafos y la numeracion de clausulas.
        4. cualquier otra etiqueta -> espacio.
        5. entidades HTML -> su caracter (`&nbsp;`, `&#8226;`, `&amp;`).
        6. normalizar, que es el paso que si cuida los identificadores.
    """
    t = str(bruto or "")
    if not t.strip():
        return ""
    # Solo actua si de verdad parece HTML: aplicado a texto plano, limpiar
    # etiquetas se comeria comparaciones como "temp < 40 y > 20".
    if not re.search(r"<!doctype[^>]*>|</?\s*[a-z][\w-]*\s*/?>", t,
                     re.IGNORECASE):
        return normalizar(t)

    t = re.sub(r"<(script|style|noscript|head)\b[^>]*>.*?</\1>", "\n",
               t, flags=re.IGNORECASE | re.DOTALL)
    t = re.sub(r"<!--.*?-->", "\n", t, flags=re.DOTALL)
    t = re.sub(r"<![^>]*>", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"<\s*br\s*/?\s*>", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"<\s*li\b[^>]*>", "\n- ", t, flags=re.IGNORECASE)
    for et in _BLOQUES:
        t = re.sub(rf"</\s*{et}\s*>", "\n", t, flags=re.IGNORECASE)
        t = re.sub(rf"<\s*{et}\b[^>]*>", "\n", t, flags=re.IGNORECASE)
    # El resto de etiquetas. Se exige que tras `<` venga una LETRA: si no,
    # una comparacion pegada al HTML (`temp < 40 y > 20`) se comeria el
    # trozo entero entre `<` y el siguiente `>`.
    t = re.sub(r"<\s*/?\s*[a-z][^>]*>", " ", t)
    t = _html.unescape(t)
    # Las viñetas salen con una linea en blanco en medio: entre </li> y
    # <li> el propio fuente HTML deja un salto, y a la lista eso le pone
    # un hueco por elemento. Se junta SOLO delante de un guion, que es el
    # unico caso en que la linea en blanco no aporta nada.
    t = re.sub(r"\n[ \t]*\n[ \t]*(?=[-•]\s)", "\n", t)
    return normalizar(t)


def de_docx_a_texto(bits) -> str:
    """
    El texto de un .docx, con la stdlib y sin python-docx.

    Un .docx es un zip con `word/document.xml` dentro. No se anade la
    libreria a requirements.txt a proposito: ya estuvo retirada, y
    `zipfile` + `xml.etree` son parte de la biblioteca estandar. Los
    `<w:p>` de los parrafos y los `<w:br>` de los saltos se traducen a
    salto de linea; los `<w:t>` son el texto.

    Devuelve "" con cualquier fichero que no sea un docx valido (zip
    truncado, formato heredado .doc, PDF con extension equivocada). Nunca
    lanza: quien llama decide si "" es un problema.
    """
    if not bits:
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(bits)) as z:
            xml = z.read("word/document.xml")
    except Exception:
        return ""
    try:
        raiz = ET.fromstring(xml)
    except Exception:
        return ""
    partes: list[str] = []
    # iter() recorre en pre-orden: el <w:p> se visita ANTES que sus hijos,
    # asi que el salto sale antes que el texto del parrafo, que es el
    # orden correcto.
    for nodo in raiz.iter():
        tag = nodo.tag.rsplit("}", 1)[-1].lower() if isinstance(nodo.tag, str) else ""
        if tag == "t" and nodo.text:
            partes.append(nodo.text)
        elif tag in ("br", "cr"):
            partes.append("\n")
        elif tag == "tab":
            partes.append(" ")
        elif tag == "p":
            partes.append("\n")
    return normalizar("".join(partes))


def _decodificar(bits: bytes) -> str:
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return bits.decode(enc)
        except UnicodeDecodeError:
            continue
    return bits.decode("utf-8", errors="replace")


def de_archivo(bits, nombre: str, tipo_mime: str = "") -> str:
    """
    Texto de un adjunto segun lo que sea por dentro, no por su extension.

    Los .docx se abren, los .html se limpian y todo lo demas se decodifica.
    El content-type se mira porque a veces el portal no pone extension al
    final de la URL y solo se sabe del tipo de la cabecera.
    """
    if not bits:
        return ""
    n = str(nombre or "").lower()
    ext = os.path.splitext(n.split("?")[0])[1]
    tipo = str(tipo_mime or "").lower()
    if ext == ".docx":
        return de_docx_a_texto(bits)
    if ext in (".htm", ".html") or "html" in tipo:
        return de_html_a_texto(_decodificar(bits))
    return _decodificar(bits)


_RE_PARECE_HTML = re.compile(
    r"<\s*(?:!doctype|html|head|body|p|div|br|li|ul|ol|table|tr|td|th|"
    r"h[1-6]|span|b|i|strong|em|font|center|pre)\b",
    re.IGNORECASE,
)


def texto_pegado(bruto) -> str:
    """
    La entrada interna para bloques largos de especificaciones PEGADOS.

    Un correo del comprador, el CDRL copiado del portal, el cuerpo de la
    enmienda 003: texto suelto que no viene de SAM.gov ni de un adjunto y
    que se quiere juzgar con las mismas reglas que cualquier aviso.

    Se adivina si es HTML por las etiquetas que trae (si lo es, se limpia
    igual que un pliego descargado; si no, solo se normaliza) para que la
    misma comparacion de "temp < 40" no acabe convertida en "temp 40".

        friction_engine.evaluar_pegado(pliego.texto_pegado(texto))
    """
    t = str(bruto or "")
    if not t.strip():
        return ""
    if _RE_PARECE_HTML.search(t):
        return de_html_a_texto(t)
    return normalizar(t)


# ---------------------------------------------------------------------------
# Ingesta: la descripcion COMPLETA, cacheada por noticeId + actualizacion.
# ---------------------------------------------------------------------------
def fecha_actualizacion(opp: dict | None) -> str:
    """
    La marca de actualizacion del aviso, en texto.

    Va en la clave del cache por un motivo concreto: una licitacion
    enmendada cambia de texto, y la descripcion cacheada de la semana
    pasada ya no describe el mismo pliego. Sin esta marca, el cache seria
    eterno y el analisis se haria sobre un pliego que el comprador ya
    retoco.
    """
    if not isinstance(opp, dict):
        return ""
    for clave in ("updateDate", "postedDate", "date", "lastModified"):
        v = opp.get(clave)
        if v:
            return str(v).strip()
    return ""


def texto_largo(notice_id, opp: dict | None = None) -> str:
    """
    La descripcion COMPLETA de SAM.gov, sin el corte de 3.000 caracteres.

    Cacheada en SQLite por (noticeId, fecha de actualizacion), asi que un
    aviso ya analizado no vuelve a costar una peticion del tope diario,
    y un aviso ENMENDADO si la cuesta (porque el texto es otro).

    Porque se baja aqui y no en el escaner: solo la baja quien la usa.
    El escaner analiza MAX_A_GEMINI avisos por barrido, y bajar el pliego
    largo de los 40 que mira multiplicaria el gasto de descripcion por
    dos. Bajado solo para el que se va a juzgar son 4 peticiones por
    barrido, todas cacheadas despues.

    NUNCA lanza y nunca aporta basura: si la lectura esta apagada
    (config.PLIEGO_LARGO_EN_ANALISIS), no hay noticeId, no hay clave, la
    API esta caida o responde raro, devuelve "" y el juicio se hace con
    la descripcion oficial + los adjuntos, que es lo que habia antes.
    """
    if not getattr(config, "PLIEGO_LARGO_EN_ANALISIS", True):
        return ""
    nid = str(notice_id or "").strip()
    if not nid:
        return ""
    actualizado = fecha_actualizacion(opp)

    try:
        import store
        guardado = store.leer_pliego(nid, actualizado)
        if guardado is not None:
            return guardado
    except Exception:
        guardado = None  # sin cache local: se pregunta

    try:
        import sam_api
        bruto = sam_api.obtener_descripcion_larga(nid, opp)
    except Exception as e:
        log.warning("pliego: no se pudo bajar la descripcion larga de %s: %s",
                    nid[:12], e)
        return ""

    texto = normalizar(bruto)[:MAX_CHARS]
    try:
        import store
        store.cache_pliego(nid, actualizado, texto)
    except Exception:
        pass
    return texto
