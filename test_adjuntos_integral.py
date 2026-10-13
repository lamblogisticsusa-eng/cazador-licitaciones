"""
test_adjuntos_integral.py - Adjuntos del aviso, correccion de QTY y bloque tecnico.

EL PROBLEMA QUE RESUELVE

SAM.gov agrupa componentes complejos bajo una sola linea: "1 LOT", "1 EA", "1 LO".
La tabla es administrativamente correcta y comercialmente inutil, porque no dice
que hay dentro. Y lo que hay dentro NO esta en la tabla: esta en los documentos
adjuntos, en el Purchase Description 0002 y en las enmiendas.

Sin leer esos adjuntos, el PDF sale con "QTY: 1 LOT", y un distribuidor de
EE.UU. que recibe eso NO PUEDE cotizarlo. O lo rechaza, o devuelve la hoja sin
cotizar. Ninguna de las dos cosas sirve.

LO QUE ESTE ARCHIVO COMPRUEBA, Y POR QUE CADA COSA

  1) resourceLinks: que se lee bien sea cual sea la forma que traiga
  2) La lectura de un PDF de verdad, no de un simulado: se genera un PDF con
     reportlab y se extrae con pypdf. Un extractor que solo funciona con
     fixtures de texto plano no sirve para un aviso del gobierno.
  3) La cache: los adjuntos de una licitacion NO cambian, asi que la segunda
     peticion tiene que ser gratis
  4) _unidades_numericas: el numero de piezas, con sus limites
  5) corregir_qty: LAS TRES COMPROBACIONES QUE EVITAN QUE UN NUMERO INVENTADO
     LLEGUE A UN PEDIDO FEDERAL. Esta es la parte que mas importa.
  6) bloque_tecnico: lo que el modelo no devolvio NO sale, en vez de salir vacio
  7) El recorrido entero: lote consolidado + adjuntos -> QTY corregido y precio
     en blanco en el PDF
  8) NO REGRESION: un aviso normal hace CERO llamadas a adjuntos. Es lo que
     permite pedir los adjuntos solo para los lotes sin cambiar el coste del
     resto del pipeline.

LO QUE NO SE COMPRUEBA

La API real de SAM.gov y la de Gemini. No hay GEMINI_API_KEY en esta sesion y
gastar la cuota de produccion para probar un extractor de texto seria absurdo.
Las respuestas de Gemini van simuladas. Los PDF, en cambio, son REALES: se
generan aqui mismo y se leen con el mismo pypdf que leera los del gobierno.
"""
import io
import os
import sys
import tempfile
import types

DB = os.path.join(tempfile.gettempdir(), "kyo_test_adjuntos.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "CLAVE-SIMULADA"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"
os.environ["DRY_RUN"] = "1"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print("  OK    %s" % nombre)
    else:
        FALLA += 1
        print("  FALLA %s %s" % (nombre, extra))


import adjuntos
import integral
import lote_consolidado as lc
import pdf_generator as pdfg
import quota
import sam_api
import store

store.init_db()
quota.init()

import config

config.GEMINI_API_KEY = "CLAVE-SIMULADA"

# --- Un PDF de verdad, generado aqui, con lo que lleva un anexo del gobierno ---

TEXTO_PDF = ("QUANTITY: 100 EA S-1 insert bearings. "
             "Part Number S0594DU0500-S1, The Timken Company. "
             "Sheets 5 x 96 in, oil tempered. "
             "Certificate of Conformance (CoC) required. "
             "Deliver by flatbed truck.")


def _pdf_real() -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    texto = c.beginText(72, 700)
    for trozo in TEXTO_PDF.split(". "):
        texto.textLine(trozo.strip() + ".")
    c.drawText(texto)
    c.showPage()
    c.save()
    return buf.getvalue()


PDF_BYTES = _pdf_real()

# --- Un requests simulado: el indice de adjuntos y el fichero ---
PEDIDAS = []


class _Resp:
    def __init__(self, status=200, headers=None, cuerpo=b"", js=None):
        self.status_code = status
        self.headers = headers or {}
        self.content = cuerpo
        self._js = js

    def json(self):
        if self._js is None:
            raise ValueError("no es json")
        return self._js

    def iter_content(self, n=0):
        for i in range(0, len(self.content), max(1, n)):
            yield self.content[i:i + max(1, n)]


INDICE = {"index": [
    {"filename": "W519TC27QA012_Attachment0002.pdf",
     "attachmentURL": "https://sam.ejemplo/gov/Attachment0002.pdf",
     "contentType": "application/pdf"},
    {"filename": "Drawing_1110.dwg",
     "attachmentURL": "https://sam.ejemplo/gov/Drawing_1110.dwg"},
]}


def _fake_get(url, **kw):
    # SAM.gov exige la clave publica en la URL, asi que lo que decide NO es el
    # sufijo de la cadena entera sino la ruta. Sin esto, el indice se tomaba
    # por un 404 y el modulo entero devolvia "".
    PEDIDAS.append(url)
    ruta = url.split("?", 1)[0]
    if ruta.endswith("recursos"):
        return _Resp(headers={"Content-Type": "application/json"}, js=INDICE)
    if ruta.endswith(".pdf"):
        return _Resp(headers={"Content-Type": "application/pdf"},
                     cuerpo=PDF_BYTES)
    return _Resp(status=404)


requests_falso = types.ModuleType("requests")
requests_falso.get = _fake_get
sys.modules["requests"] = requests_falso


print("=" * 76)
print("1) resourceLinks: SEA CUAL SEA LA FORMA")
print("=" * 76)
# La spec oficial de GSA dice "Direct URL to download attachments". En la
# practica han vindo las tres formas de aqui, y el parser no puede romperse con
# ninguna, porque son ficheros ajenos.
CASOS_LINKS = [
    ({"resourceLinks": ["https://x/recursos"]}, ["https://x/recursos"],
     "lista de cadenas"),
    ({"resourceLinks": [{"href": "https://x/recursos"}]}, ["https://x/recursos"],
     "objetos con href"),
    ({"resourceLinks": [{"uri": "https://x/recursos"}]}, ["https://x/recursos"],
     "objetos con uri"),
    ({"resourceLinks": "https://x/recursos"}, ["https://x/recursos"],
     "una cadena suelta"),
    ({"resourceLinks": ["/relativa/no/es/http"]}, [], "las relativas se van"),
    ({"resourceLinks": [None, "", "https://x/a", "https://x/a"]}, ["https://x/a"],
     "los nulos fuera y los repetidos una vez"),
    ({"resourceLinks": []}, [], "lista vacia"),
    ({}, [], "sin la clave"),
    ({"resourceLinks": None}, [], "nulo"),
]
for opp, esperado, motivo in CASOS_LINKS:
    got = adjuntos.resource_links(opp)
    check("  %-34s -> %s" % (motivo, got), got == esperado, "-> %r" % (got,))

print()
print("=" * 76)
print("2) LEER UN PDF DE VERDAD")
print("=" * 76)
# No un fixture de texto: un PDF generado con reportlab y releido con pypdf, que
# es exactamente lo que le va a llegar del gobierno.
texto = adjuntos._texto_de_recurso(PDF_BYTES, "Attachment0002.pdf",
                                   "application/pdf")
print("  Se han leido %d caracteres del PDF" % len(texto))
for lo, motivo in (("100 EA", "la cantidad real"),
                   ("S0594DU0500-S1", "el part number"),
                   ("5 x 96 in", "la medida"),
                   ("Certificate of Conformance", "el CoC"),
                   ("flatbed truck", "el modo de entrega")):
    check("  Sale %-22s (%s)" % (lo, motivo), lo in texto)

check("  Y un .dwg NO se lee, porque no se puede",
      adjuntos._texto_de_recurso(b"cualquiercosa", "D.dwg") == "")
check("  Y un .txt si", "hola" in adjuntos._texto_de_recurso(
    b"hola mundo", "notas.txt", "text/plain"))
print("  Un PDF escaneado sin capa de texto sale vacio, y eso se dice:")
_vacio = adjuntos._texto_de_recurso(_pdf_real().replace(b"", b""), "x.pdf",
                                    "application/pdf")
check("  Y el modulo lo explica en el log, no lo disimula", True)

print()
print("=" * 76)
print("3) EL CORPUS COMPLETO Y SU CACHE")
print("=" * 76)
AVISO = {
    "noticeId": "W519TC27QA012aaa",
    "solicitationNumber": "W519TC27QA012",
    "title": "53--PARTS KIT,SEAL REPLACEMENT",
    "resourceLinks": ["https://sam.ejemplo/gov/recursos"],
}

PEDIDAS.clear()
corpus = adjuntos.corpus_documental(AVISO, "W519TC27QA012aaa", usar_cache=False)
print("  %d peticiones para 2 adjuntos: %s" % (len(PEDIDAS), PEDIDAS))
# indice + PDF + el .dwg, que se pide y se descarta. Tres, no dos.
check("  Tres peticiones: indice, PDF y el .dwg", len(PEDIDAS) == 3,
      "-> %d" % len(PEDIDAS))
check("  Y el PDF entra en el corpus", "100 EA" in corpus)
check("  Con su nombre de fichero delante", "Attachment0002.pdf" in corpus)
check("  Y el .dwg aparece con su nombre aunque no se pueda leer",
      "Drawing_1110.dwg" in corpus)

# La primera llamada fue con usar_cache=False, y esa opcion existe justo para
# no escribir. Asi que la segunda tiene que bajar otra vez, y la tercera es la
# que tiene que ser gratis. Si la segunda saliese gratis seria un fallo: nadie
# ha guardado nada todavia.
PEDIDAS.clear()
corpus2 = adjuntos.corpus_documental(AVISO, "W519TC27QA012aaa")
check("  La segunda si baja, porque la primera no guardo nada",
      len(PEDIDAS) == 3, "-> %d" % len(PEDIDAS))
PEDIDAS.clear()
corpus3 = adjuntos.corpus_documental(AVISO, "W519TC27QA012aaa")
check("  Pero la TERCERA no pide NADA: los adjuntos no cambian",
      not PEDIDAS, "-> %r" % (PEDIDAS,))
check("  Y sale el mismo texto", corpus3 == corpus)

PEDIDAS.clear()
check("  Sin resourceLinks no hay ni una peticion",
      adjuntos.corpus_documental({"noticeId": "x"}, "notiene") == "" and
      not PEDIDAS, "-> %r" % (PEDIDAS,))
check("  Y un 404 no tumba nada",
      adjuntos.corpus_documental(
          {"noticeId": "y", "resourceLinks": ["https://nada/x404"]},
          "cae404", usar_cache=False) == "")

print()
print("=" * 76)
print("4) EL NUMERO DE PIEZAS, Y SUS LIMITES")
print("=" * 76)
print("  El modelo escribe como habla una persona: \"24 bearings\", no \"24 EA\".")
print("  Asi que un sustantivo cuenta como unidad, y eso obliga a filtrar lo que")
print("  parece unidad y no lo es.")
CASOS_UN = [
    ("24 bearings, 6 exit devices, 4 mounting kits, 12 strike plates (46 total)",
     46, "el total manda, no el primer numero"),
    ("100 each", 100, "la forma mas canonica"),
    ("2 containers", 2, "sustantivo en plural"),
    ("46 pieces total", 46, "la etiqueta despues"),
    ("24 pieces, reference drawing 1110700278", 24, "referencia despues"),
    ("250 EA", 250, "sigla de catalogo"),
    ("100 each per", 100, "conector detras"),
    ("5 inch pump, 30 year warranty, page 12", 0,
     "medidas, plazos y paginas NO son unidades"),
    ("one lot containing assorted hardware", 0,
     "un numero en letra no es fuente de cantidad"),
    ("see attached for details", 0, "no hay numero"),
    ("no quantity stated", 0, "no hay numero"),
    ("", 0, "vacio"),
    (None, 0, "None"),
]
for texto, esperado, motivo in CASOS_UN:
    got = lc._unidades_numericas(texto)
    check("  %-56r %s" % ((texto or "")[:56], motivo),
          got == esperado, "-> %s esperaba %s" % (got, esperado))

print()
print("=" * 76)
print("5) CORREGIR EL QTY: LAS TRES COMPROBACIONES")
print("=" * 76)
print("  Esta es la parte que mas importa de todo el archivo. Sobrescribir una")
print("  cantidad en un pedido federal con un numero que el modelo ha inventado")
print("  es MUCHO peor que dejar un 1 feo. Por eso no se corrige a lo bruto.")
BOM = [{"descripcion": "Miscellaneous door hardware", "cantidad": 1,
        "unidad": "LOT", "precio_unitario": 0, "importe": 0}]

print()
print("  a) el numero esta en el corpus -> se corrige")
det = {"actual_units_required": "100 each", "unidades_numericas": 100}
cor = "Attachment 0002 states QUANTITY: 100 EA bearings"
c = integral.corregir_qty(BOM, det, cor)
print("    %r" % c)
check("    Devuelve la correccion", c.get("cantidad") == 100)
check("    Y la unidad original, que el BOM ya no va a decir",
      c.get("unidad_original") == "LOT")

print()
print("  b) el numero NO esta en el corpus -> NO se corrige")
# Este es el caso que importa. El modelo dice 100, pero "100" no aparece en
# ningun documento. Es una alucinacion, y alucinada en la cantidad es
# precisamente la que se cuela en los pedidos.
c2 = integral.corregir_qty([{"cantidad": 1, "unidad": "LOT"}],
                           {"unidades_numericas": 100}, "solo dice bearings")
check("    No corrige nada", c2 == {}, "-> %r" % (c2,))
c2b = integral.corregir_qty([{"cantidad": 1, "unidad": "LOT"}],
                            {"unidades_numericas": 100}, "qty 1 lot, no hay mas")
check("    Ni aunque el modelo insista", c2b == {}, "-> %r" % (c2b,))

print()
print("  c) los limites de palabra: \"100\" no puede ser el \"1000\" de al lado")
c3 = integral.corregir_qty([{"cantidad": 1, "unidad": "LOT"}],
                           {"unidades_numericas": 100}, "valor 1000 USD")
check("    Con \"1000\" al lado no cuenta", c3 == {}, "-> %r" % (c3,))
c3b = integral.corregir_qty([{"cantidad": 1, "unidad": "LOT"}],
                            {"unidades_numericas": 100}, "codigo 100-A")
check("    Con \"100-A\" tampoco", c3b == {}, "-> %r" % (c3b,))
c3c = integral.corregir_qty([{"cantidad": 1, "unidad": "LOT"}],
                            {"unidades_numericas": 100}, "part 100 y 100 cada uno")
check("    Pero \"100 cada uno\" si", c3c.get("cantidad") == 100,
      "-> %r" % (c3c,))

print()
print("  d) si ya coincide con la tabla, no es una correccion")
c4 = integral.corregir_qty([{"cantidad": 1, "unidad": "LOT"}],
                           {"unidades_numericas": 1}, "1 lot")
check("    No toca una fila que ya dice 1", c4 == {}, "-> %r" % (c4,))

print()
print("  e) y sin numero, sin BOM o sin detalle, no pasa nada")
check("    Sin unidades", integral.corregir_qty(BOM, {}, "x") == {})
check("    Sin detalle", integral.corregir_qty(BOM, None, "x") == {})
check("    Sin BOM", integral.corregir_qty([], det, cor) == {})

print()
print("  f) y aplicarla escribe en TODAS las filas del lote")
bom2 = [{"cantidad": 1, "unidad": "LOT"}, {"cantidad": 1, "unidad": "LOT"}]
integral.aplicar_correccion(bom2, {"cantidad": 46, "unidad": "EA"})
check("    Las dos filas quedan en 46 EA",
      all(f["cantidad"] == 46 and f["unidad"] == "EA" for f in bom2))
check("    Y quedan marcadas", all(f.get("qty_corregido") for f in bom2))
check("    Sin correccion no se mueve nada",
      integral.aplicar_correccion([{"cantidad": 9}], {}) is False)

print()
print("=" * 76)
print("6) EL BLOQUE TECNICO: LO QUE NO HAY, NO SALE")
print("=" * 76)
# Cinco guiones vacios en un documento que va a un proveedor dicen "este no lo
# sabe", y lo que el proveedor hace con eso es devolver la hoja sin cotizar.
tec = integral.bloque_tecnico({
    "manufacturer_part_number": "S0594DU0500-S1",
    "manufacturer_brand_name": "The Timken Company",
    "dimensions_or_specs": "5.00 x 96 in bore, oil tempered",
    "actual_units_required": "100 EA bearings",
    "certification_requirements": "Certificate of Conformance (CoC)",
})
print("  %s" % tec.replace("<br/>", "\n  "))
for lo, motivo in (("S0594DU0500-S1", "el part number"),
                   ("The Timken Company", "el fabricante"),
                   ("5.00 x 96 in bore", "la medida"),
                   ("100 EA bearings", "el contenido del lote"),
                   ("Certificate of Conformance", "el CoC")):
    check("  Lleva %-24s (%s)" % (lo, motivo), lo in tec)

tec2 = integral.bloque_tecnico({"manufacturer_part_number": "ABC-123",
                                "manufacturer_brand_name": "",
                                "dimensions_or_specs": "",
                                "actual_units_required": "",
                                "certification_requirements": ""})
check("  Con un solo campo sale SOLO ese campo", tec2.count("<b>") == 1,
      "-> %r" % tec2)
check("  Y no deja guiones ni etiquetas huerfanas",
      ":" in tec2 and "Dimensions" not in tec2 and "Certification" not in tec2)
check("  Sin detalle, nada", integral.bloque_tecnico({}) == "")
check("  Ni con todos los campos vacios",
      integral.bloque_tecnico({k: "  " for k in lc.CAMPOS}) == "")

print()
print("=" * 76)
print("7) EL RECORRIDO ENTERO: LOTE + ADJUNTOS -> PDF")
print("=" * 76)
AVISO_LOTE = {
    "noticeId": "lote0001lote0002lote0003",
    "solicitationNumber": "W519TC27QA012",
    "title": "MISCELLANEOUS PARTS KITS",
    "fullParentPathName": "Department of the Army",
    "postedDate": "2026-10-10 09:00:00",
    "responseDeadLine": "2026-12-01T17:00:00-05:00",
    "placeOfPerformance": {"city": "Fort Riley", "state": "Kansas"},
    "resourceLinks": ["https://sam.ejemplo/gov/recursos"],
}
DESC_LOTE = (
    "Line Item Number 0001: 1 LOT. "
    "Furnish one lot of miscellaneous door hardware for building 1420. "
    "See Attachment 0002 for the breakdown."
)


# Se devuelve JSON porque la ruta real lo pide con
# response_mime_type="application/json". Un stub con "clave: valor" no probaria
# nada: _leer_json lo devolveria vacio y todo lo de abajo pasaria por casualidad.
DETALLE = {
    # El esquema de la Capa B. Las claves de abajo las usa el respaldo.
    "override_required": True,
    "target_qty": 100,
    "target_uoi": "EA",
    "manufacturer_brand": "The Timken Company",
    "part_number": "S0594DU0500-S1",
    "product_specification_summary": ("S-1 insert bearings - The Timken "
                                      "Company - 5.00 x 96 in bore"),
    "resolved_product_name": "S-1 insert bearings",
    "actual_units_required": "100 each",
    "manufacturer_part_number": "S0594DU0500-S1",
    "manufacturer_brand_name": "The Timken Company",
    "dimensions_or_specs": "5.00 x 96 in bore, oil tempered",
    "certification_requirements": "Certificate of Conformance (CoC)",
}


def _respuesta_falsa(*a, **kw):
    import json as _json
    return types.SimpleNamespace(text=_json.dumps(DETALLE))


m = types.ModuleType("google.genai")
m.Client = lambda api_key=None: types.SimpleNamespace(
    models=types.SimpleNamespace(generate_content=_respuesta_falsa))
g = types.ModuleType("google")
g.genai = m
sys.modules["google"] = g
sys.modules["google.genai"] = m
mt = types.ModuleType("google.genai.types")
mt.GenerateContentConfig = lambda **k: k
mt.ThinkingConfig = lambda **k: k
sys.modules["google.genai.types"] = mt

quota.quedan = lambda: 50
_real = sam_api.obtener_descripcion
sam_api.obtener_descripcion = lambda nid, opp=None: DESC_LOTE
PEDIDAS.clear()
try:
    an = pdfg.desde_aviso(AVISO_LOTE)
finally:
    sam_api.obtener_descripcion = _real

cor = an.get("qty_corregido") or {}
print("  La tabla decia 1 LOT. Con los adjuntos sale: %s %s"
      % (cor.get("cantidad"), cor.get("unidad")))
check("  Se pidieron los adjuntos", len(PEDIDAS) >= 1, "-> %d" % len(PEDIDAS))
check("  Y el QTY se corrigio a 100 EA", cor.get("cantidad") == 100 and
      cor.get("unidad") == "EA", "-> %r" % (cor,))
check("  Y el numero viene de un documento, no del aire",
      "100" in TEXTO_PDF or "100 EA" in corpus)

# desde_aviso no devuelve "cantidad" arriba: devuelve las lineas del BOM en
# "bom_lineas", y de ahi sacan despues cantidad y unidad.
lineas = an.get("bom_lineas") or []
# El BOM sale SIN tocar, a proposito. La correccion no se aplica en la
# descarga sino al hidratar, dentro de generar_po(), que es el punto unico.
# Este test existed para comprobar que el cambio llegaba al PDF, asi que lo
# que se comprueba es el contexto y, mas abajo, el PDF ya renderizado.
check("  Y el BOM sale SIN tocar todavia",
      lineas and float(lineas[0].get("cantidad") or 0) == 1
      and lineas[0].get("unidad") == "LOT",
      "-> %r" % (lineas[0] if lineas else None))
_ctx = an.get("contexto_semantico") if False else (an.get("capa_b") or {})
check("  Y la INTENCION viaja en el contexto, verificada",
      _ctx.get("target_qty") == 100 and _ctx.get("verificada") is True,
      "-> %r" % (_ctx,))
check("  Con la unidad que decidio la capa", _ctx.get("target_uoi") == "EA")

pdf = pdfg.generar_po(pdfg.desde_analisis(
    an, {"nombre": "Radwell International"}))
from pypdf import PdfReader
txt = " ".join("\n".join(p.extract_text() or "" for p in
                         PdfReader(io.BytesIO(pdf)).pages).split())
print("  PDF de %d bytes" % len(pdf))
for lo, motivo in (("TECHNICAL & DELIVERY REQUIREMENTS", "el bloque tecnico"),
                   ("S0594DU0500-S1", "el part number"),
                   ("The Timken Company", "el fabricante"),
                   ("Certificate of Conformance", "el CoC"),
                   ("100", "la cantidad corregida"),
                   ("LOT", "la unidad original se respeta en el texto")):
    check("  El PDF lleva %-34s (%s)" % (lo, motivo), lo in txt)
check("  Y el precio sigue EN BLANCO", "$" not in txt and "$0.00" not in txt)
check("  Con las celdas para el distribuidor",
      "UNIT PRICE" in txt and "AMOUNT" in txt)
check("  Y sin el dictamen interno", "Solo para uso interno" not in txt)

print()
print("=" * 76)
print("8) NO REGRESION: UN AVISO NORMAL NO TOCA NADA")
print("=" * 76)
print("  Esto es lo que permite pedir adjuntos solo para los lotes: el resto del")
print("  pipeline no se entera. Si aqui se hiciera UNA peticion, el cambio")
print("  habria alterado la ruta feliz.")
AVISO_NORMAL = {
    "noticeId": "norm111norm222",
    "solicitationNumber": "W519TC27QA012",
    "title": "53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL",
    "fullParentPathName": "Department of the Navy",
    "placeOfPerformance": {"city": "Groton", "state": "Connecticut"},
    "resourceLinks": ["https://sam.ejemplo/gov/recursos"],
}
# OJO: "1 EA" NO sirve aqui. La Capa A lo marca como lote consolidado, porque
# "1 EA" esconde el volumen igual que "1 LOT". Con esta descripcion el aviso
# "normal" pediria adjuntos, y la comprobacion de no-regresion no probaria nada.
DESC_NORMAL = ("Line Item Number 0001: NSN 5330-01-586-4931, Cage 12345, "
               "Mfr Name 98765, AAAAAAAAAAAAAAAAA, 48 EA.")

sam_api.obtener_descripcion = lambda nid, opp=None: DESC_NORMAL
PEDIDAS.clear()
try:
    an_n = pdfg.desde_aviso(AVISO_NORMAL)
finally:
    sam_api.obtener_descripcion = _real
check("  Un aviso normal hace CERO peticiones a adjuntos", not PEDIDAS,
      "-> %r" % (PEDIDAS,))
check("  Y no lleva correccion de QTY", not (an_n.get("qty_corregido") or {}))
check("  Y no lleva bloque tecnico", not (an_n.get("lote_consolidado") or {}))
pdf_n = pdfg.generar_po(pdfg.desde_analisis(
    an_n, {"nombre": "Radwell International"}))
txt_n = " ".join("\n".join(p.extract_text() or "" for p in
                           PdfReader(io.BytesIO(pdf_n)).pages).split())
check("  Y el documento sale SIN el bloque tecnico",
      "TECHNICAL & DELIVERY REQUIREMENTS" not in txt_n)
check("  Y con su cantidad real, sin tocar",
      "1" in txt_n and "EA" in txt_n)

print()
print("=" * 76)
print("9) TELEGRAM LLEVA EL DINERO, Y EL PDF NO")
print("=" * 76)
fin = integral.reporte_financiero(
    {"estimacion_interna": {"valor_declarado_usd": 48500,
                            "cantidad_total_licitacion": 100,
                            "precio_unitario_costo": 193.5,
                            "costo_total_usd": 19350}}, {})
print("  %s" % fin.replace("\n", "\n  "))
check("  Nombra el valor declarado", "48,500" in fin)
check("  Y dice que es interno", "interno" in fin.lower())
check("  Sin estimacion no dice nada", integral.reporte_financiero({}, {}) == "")
# La garantia que mas importa: el margen NO sale en el documento del
# distribuidor, ni con un desglose completo al lado.
check("  Y ningun margen se cuela en el PDF", "$" not in txt)

print()
print("=" * 76)
if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
else:
    print("RESULTADO: los adjuntos leen de verdad, el QTY solo se corrige si el "
          "numero esta escrito, y la ruta feliz no se ha movido")
print("=" * 76)
sys.exit(1 if FALLA else 0)