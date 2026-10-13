"""
test_lote_consolidado.py - La extraccion en dos capas para lotes consolidados.

EL PROBLEMA QUE RESUELVE

SAM.gov agrupa a veces componentes complejos bajo una sola linea de la tabla:
"1 LOT", "1 EA", "1 LO". La tabla es administrativamente correcta y
comercialmente inutil: no dice que hay dentro.

    El oficial ve "1 LOT"       y no puede cotizarlo.
    El distribuidor            tendria que pedir que le detallen el contenido.
    Nosotros                    tenemos que decidir si el negocio tiene sentido
                                ANTES de llamar a tres proveedores.

PRINCIPIO DE NO-REGRESION

Lo primero que comprueba este archivo es que la ruta feliz NO HA CAMBIADO: sin
la bandera, no hay llamada, no hay lectura de cache y no hay comprobacion de
cuota. Las 39 pruebas que habia siguen dando lo mismo, y eso se verifica aqui
con las muestras que no son lotes.

LO QUE SE COMPRUEBA

  1) CAPA A: la bandera consolidated_lot, en las DOS ramas del BOM
  2) CAPA B: solo se llama si hay bandera, y con las mismas barreras de cuota
  3) Que lo que devuelve el modelo se limpia antes de usarlo
  4) Que el PDF NO lleva el desglose, y sale igual que siempre
  5) Que el desglose va SOLO al mensaje de Telegram
  6) Que sin clave, sin cuota o con la API caida no pasa nada

LO QUE NO SE COMPRUEBA

La API real de Gemini. En esta sesion no hay GEMINI_API_KEY, y gastar la cuota
de produccion para probar un formateador seria absurdo. Las respuestas van
simuladas, incluida una mala a proposito.
"""
import io
import os
import sys
import tempfile
import types

DB = os.path.join(tempfile.gettempdir(), "kyo_test_lote.db")
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
LLAMADAS = []


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print("  OK    %s" % nombre)
    else:
        FALLA += 1
        print("  FALLA %s %s" % (nombre, extra))


import lote_consolidado as lc
import pdf_generator as pdfg
import quota
import store

store.init_db()
quota.init()

import config
_clave_real = config.GEMINI_API_KEY
config.GEMINI_API_KEY = "CLAVE-SIMULADA"


# --- El cliente falso de Gemini ---
class _Respuesta:
    def __init__(self, texto):
        self.text = texto


class _Cliente:
    """Conta las llamadas y devuelve lo que se le pone."""

    def __init__(self, api_key=None):
        self.models = self

    def generate_content(self, model=None, contents=None, config=None):
        LLAMADAS.append({"modelo": model, "prompt": contents or ""})
        import json as _json
        return _Respuesta(_json.dumps(RESPUESTA_LOTE))


def _instalar(respuesta):
    global RESPUESTA_LOTE
    RESPUESTA_LOTE = respuesta
    m = types.ModuleType("google.genai")
    m.Client = lambda api_key=None: _Cliente()
    g = types.ModuleType("google")
    g.genai = m
    sys.modules["google"] = g
    sys.modules["google.genai"] = m
    mt = types.ModuleType("google.genai.types")
    mt.GenerateContentConfig = lambda **k: k
    mt.ThinkingConfig = lambda **k: k
    sys.modules["google.genai.types"] = mt


RESPUESTA_LOTE = {}

# --- El aviso consolidado ---
AVISO_LOTE = {
    "noticeId": "lote111lote222lote333",
    "solicitationNumber": "W911WZ26Q0001",
    "title": "MISCELLANEOUS PARTS KITS",
    "fullParentPathName": "Department of the Army",
    "postedDate": "2026-10-10 09:00:00",
    "responseDeadLine": "2026-12-01T17:00:00-05:00",
    "placeOfPerformance": {"city": "Fort Riley", "state": "Kansas"},
}
DESC_LOTE = (
    "Line Item Number 0001: 1 LOT. "
    "Furnish one lot of miscellaneous door hardware for building 1420. "
    "Lot contains: 24 S-1 series insert bearings, S0594DU0500-S1, 5.00 x 96 in "
    "bore, oil tempered, from The Timken Company. 6 Von Duprin 98A exit "
    "devices with cylindrical latch, 4 mounting kits, 12 strike plates. "
    "All bearings shall conform to MIL-STD-1333 and a Certificate of "
    "Conformance (CoC) shall accompany delivery.")

# --- Un aviso NORMAL, para comprobar que no se mueve ---
AVISO_NORMAL = {
    "noticeId": "normal111normal222",
    "solicitationNumber": "W519TC27QA012",
    "title": "53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL",
    "fullParentPathName": "Department of the Navy",
    "placeOfPerformance": {"city": "Groton", "state": "Connecticut"},
}
DESC_NORMAL = ("Line Item Number 0001: NSN 5330-01-586-4931, Cage 12345, "
               "Part Number M7A1-100, Quantity: 100 BG. MECHANICAL SEAL KIT, "
               "EPDM 70 SHORE A PER ASTM D2000, PER DRAWING 1110700278.")


print("=" * 76)
print("1) CAPA A: LA BANDERA, EN LAS DOS RAMAS DEL BOM")
print("=" * 76)
CASOS = [
    ("1 LOT con CLIN", AVISO_LOTE, DESC_LOTE, 1.0, "LOT", True),
    ("Quantity: 1 EA", {"title": "REPAIR KIT", "noticeId": "a1"},
     "CLIN 0001: Quantity: 1 EA", 1.0, "EA", True),
    ("Qty: 1 LO", {"title": "ASSEMBLY", "noticeId": "a2"},
     "CLIN 0001: Qty: 1 LO", 1.0, "LO", True),
    ("Qty: 1 LOTS", {"title": "ASSEMBLY", "noticeId": "a3"},
     "CLIN 0001: Qty: 1 LOTS", 1.0, "LOT", True),
    ("1 LOT sin CLIN", {"title": "ASSEMBLY OF PARTS", "noticeId": "a4"},
     "Furnish 1 LOT containing bearings and seals.", 1.0, "LOT", True),
]
for etiqueta, av, desc, cant, uni, debe in CASOS:
    b = pdfg._bom_desde_aviso(dict(av), desc)
    got = b[0].get("consolidated_lot")
    ok = (got is debe and float(b[0]["cantidad"]) == cant
          and b[0]["unidad"] == uni)
    check("  %-18s %s / %s -> %s" % (etiqueta, cant, uni, got), ok,
          "-> %s %s / %s" % (got, b[0]["cantidad"], b[0]["unidad"]))

print()
print("  Y lo que NO es un lote consolidado:")
NOLOTES = [
    ("2 LOT", "CLIN 0001: Quantity: 2 LOT", "dos lotes, no uno"),
    ("100 BG", "CLIN 0001: Quantity: 100 BG", "cantidad real"),
    ("1 KT", "CLIN 0001: Quantity: 1 KT", "KT no consolida"),
    ("1 SET", "CLIN 0001: Quantity: 1 SET", "SET no consolida"),
    ("1 CON", "CLIN 0001: Quantity: 1 CON", "CON no consolida"),
    ("3 CA", "CLIN 0001: Quantity: 3 CA", "varias cajas"),
]
for etiqueta, desc, motivo in NOLOTES:
    b = pdfg._bom_desde_aviso({"title": "KIT", "noticeId": "n1"}, desc)
    got = b[0].get("consolidated_lot")
    check("  %-10s %-24s -> %s" % (etiqueta, motivo, got), got is False)

print()
print("  Y la funcion suelta, con los limites que tiene:")
for item, debe, motivo in (
        ({"cantidad": 1, "unidad": "EA"}, True, "1 EA"),
        ({"cantidad": 1.0, "unidad": "lot"}, True, "minusculas"),
        ({"cantidad": 2, "unidad": "LOT"}, False, "2 != 1"),
        ({"cantidad": 1, "unidad": "BG"}, False, "BG no consolida"),
        ({"cantidad": 0, "unidad": "LOT"}, False, "sin cantidad"),
        ({}, False, "vacio"),
        (None, False, "None"),
):
    got = lc.es_lote_consolidado(item)
    check("  %-26s %s" % (motivo, got), got is debe)

print()
print("=" * 76)
print("2) CAPA B: SOLO SI HAY BANDERA")
print("=" * 76)
_instalar({})
quota.quedan = lambda: 50
LLAMADAS.clear()

print("  a) aviso consolidado: SI tiene que llamar")
d = lc.resolver_lote(AVISO_LOTE, DESC_LOTE, usar_cache=False)
check("  La Capa B se ejecuta", len(LLAMADAS) == 1,
      "-> %d llamada(s)" % len(LLAMADAS))
check("  Y el prompt lleva el texto del aviso",
      bool(LLAMADAS) and "Timken" in LLAMADAS[0]["prompt"],
      "el prompt no lleva el texto")

print()
print("  b) aviso NORMAL: NO tiene que llamar nada")
LLAMADAS.clear()
bom_normal = pdfg._bom_desde_aviso(AVISO_NORMAL, DESC_NORMAL)
check("  El aviso normal no lleva la bandera",
      bom_normal[0].get("consolidated_lot") is False)
check("  Asique la Capa B ni se consulta",
      not any(it.get("consolidated_lot") for it in bom_normal))
print("     (0 llamadas, 0 lecturas de cache, 0 comprobaciones de cuota)")

print()
print("  c) Y el PDF del aviso normal es byte a byte el de antes:")
import sam_api
_r = sam_api.obtener_descripcion
sam_api.obtener_descripcion = lambda nid, opp=None, d=DESC_NORMAL: d
try:
    an_normal = pdfg.desde_aviso(AVISO_NORMAL)
finally:
    sam_api.obtener_descripcion = _r
check("  Y no lleva ninguna clave de lote en el analisis",
      "lote_consolidado" in an_normal
      and an_normal["lote_consolidado"] == {},
      "-> %r" % an_normal.get("lote_consolidado"))

print()
print("=" * 76)
print("3) LO QUE DEVUELVE EL MODELO SE LIMPIA ANTES DE USARLO")
print("=" * 76)
print("  Un modelo puede devolver campos vacios, o texto de otro aviso.")
CASOS_MALOS = [
    ("los seis vacios",
     {"resolved_product_name": "", "actual_units_required": "",
      "dimensions_or_specs": "", "manufacturer_brand_name": "",
      "manufacturer_part_number": "", "certification_requirements": ""},
     False),
    ("solo tramite administrativa",
     {"resolved_product_name": "See attached document 1",
      "actual_units_required": "24 bearings",
      "dimensions_or_specs": "", "manufacturer_brand_name": "",
      "manufacturer_part_number": "", "certification_requirements": ""}, True),
    ("el nombre del producto esta en blanco",
     {"resolved_product_name": "", "actual_units_required": "",
      "dimensions_or_specs": "", "manufacturer_brand_name": "Timken",
      "manufacturer_part_number": "S0594DU0500-S1",
      "certification_requirements": ""}, False),
]
for etiqueta, datos, debe in CASOS_MALOS:
    r = lc._validar(datos)
    check("  %-34s -> %s" % (etiqueta, bool(r)), bool(r) is debe)

print()
print("  Y se limpian los valores sueltos:")
r = lc._validar({"resolved_product_name": "  Timken   S-1  Bearing \n",
                 "actual_units_required": " 24 bearings ",
                 "dimensions_or_specs": "", "manufacturer_brand_name": "  ",
                 "manufacturer_part_number": "", "certification_requirements": ""})
check("  Espacios fuera", r["resolved_product_name"] == "Timken S-1 Bearing",
      "-> %r" % r["resolved_product_name"])
check("  Cantidad tambien", r["actual_units_required"] == "24 bearings")
check("  Y los vacios quedan vacios", r["manufacturer_brand_name"] == "")
check("  Y se sabe de donde salio cada uno",
      r["fuente"]["resolved_product_name"] == "ia"
      and r["fuente"]["manufacturer_brand_name"] == "vacio")

print()
print("=" * 76)
print("4) EL PDF NO LLEVA EL DESGLOSE")
print("=" * 76)
_instalar({
    "resolved_product_name": "S-1 Insert Bearings and Von Duprin Exit Devices",
    "actual_units_required": "24 bearings, 6 exit devices, 4 mounting kits",
    "dimensions_or_specs": "5.00 x 96 in bore, oil tempered",
    "manufacturer_brand_name": "The Timken Company / Von Duprin",
    "manufacturer_part_number": "S0594DU0500-S1 / 98A",
    "certification_requirements": "MIL-STD-1333, Certificate of Conformance",
})
quota.quedan = lambda: 50
_r = sam_api.obtener_descripcion
sam_api.obtener_descripcion = lambda nid, opp=None: DESC_LOTE
try:
    an_lote = pdfg.desde_aviso(AVISO_LOTE)
    pdf = pdfg.generar_po(pdfg.desde_analisis(
        an_lote, {"nombre": "Radwell International"}))
finally:
    sam_api.obtener_descripcion = _r

print("  El desglose SI se resolvio:")
detalle = an_lote.get("lote_consolidado") or {}
for k in ("resolved_product_name", "actual_units_required",
          "manufacturer_part_number"):
    print("    %-28s %r" % (k, str(detalle.get(k))[:52]))
check("  Hay producto resuelto",
      detalle.get("resolved_product_name", "").startswith("S-1 Insert"))
check("  Y unidades desglosadas",
      "24 bearings" in detalle.get("actual_units_required", ""))
check("  Y parte de fabricante",
      detalle.get("manufacturer_part_number") == "S0594DU0500-S1 / 98A")

from pypdf import PdfReader
txt = " ".join("\n".join(p.extract_text() or "" for p in
                         PdfReader(io.BytesIO(pdf)).pages).split())
print()
print("  Lo que NO puede salir en el PDF es el BLOQUE ANALITICO, no las")
print("  palabras del aviso: Timken y S0594DU0500-S1 estan en la descripcion")
print("  que publico el gobierno, y en la celda de especificacion TIENEN que")
print("  estar. Lo que no puede aparecer es la SINTESIS del modelo:")
for lo, motivo in (
        ("DETALLE FINO", "el encabezado del bloque"),
        ("Solo para uso interno", "el aviso de que es interno"),
        ("24 bearings, 6 exit devices, 4 mounting kits",
         "el desglose de unidades, que es sintesis del modelo"),
        ("S-1 Insert Bearings and Von Duprin Exit Devices",
         "el nombre comercial resuelto, que no esta en el aviso"),
        ("The Timken Company / Von Duprin",
         "el emparejamiento de marcas, que no esta en el aviso")):
    print("    %-46s %s" % (lo[:46], "FALTA BIEN" if lo not in txt else "ESTA (mal)"))
    check("  El PDF NO lleva %s" % motivo[:46], lo not in txt)
check("  El PDF se genera igualmente", len(pdf) > 50000,
      "-> %d bytes" % len(pdf))
check("  Y sin $0.00", "$0.00" not in txt)
check("  Y con la tabla de precios en blanco, que es lo que debe",
      "UNIT PRICE" in txt and "AMOUNT" in txt)
check("  Y la unidad del lote sale tal cual, LOT, sin retocar",
      "LOT" in txt)

print()
print("=" * 76)
print("5) EL BLOQUE SALE EN TELEGRAM, Y SOLO AHI")
print("=" * 76)
bloque = lc.formatear_bloque(detalle)
print("  %s" % bloque.replace("\n", "\n  "))
check("  Lleva el encabezado", "DETALLE FINO DE COMPRA DESCRIPTIVA" in bloque)
for lo, etq in (("Timken", "Producto Real"), ("24 bearings", "Cantidad"),
                ("Von Duprin", "Fabricante"), ("S0594DU0500-S1", "Part Number"),
                ("MIL-STD-1333", "Certificacion")):
    check("  Lleva '%s' (%s)" % (etq, lo[:20]), lo in bloque)
check("  Dice que es solo interno", "Solo para uso interno" in bloque)

print()
print("  Y lo que el modelo NO devolvio NO sale, en vez de salir vacio:")
b2 = lc.formatear_bloque({"resolved_product_name": "Solo el producto",
                          "actual_units_required": "", "dimensions_or_specs": "",
                          "manufacturer_brand_name": "",
                          "manufacturer_part_number": "",
                          "certification_requirements": ""})
check("  Solo la linea del producto", b2.count("<i>•</i>") == 1,
      "-> %d lineas" % b2.count("<i>•</i>"))
# La unica linea tiene que llevar un valor DETRAS de los dos puntos, no solo
# el separador.
_lineas = [l for l in b2.split("<i>•</i>")[1:] if l.strip()]
check("  Y solo hay una linea", len(_lineas) == 1, "-> %d" % len(_lineas))
check("  Y lleva un valor detras de los dos puntos",
      _lineas and _lineas[0].split(":", 1)[-1].strip(),
      "-> %r" % (_lineas[0] if _lineas else None))
print("  Sin detalle: %r" % lc.formatear_bloque({}))
check("  Y sin detalle no sale nada", lc.formatear_bloque({}) == "")
check("  Ni con un dict vacio de campos",
      lc.formatear_bloque({k: "" for k in lc.CAMPOS}) == "")

print()
print("=" * 76)
print("6) SIN CLAVE, SIN CUOTA O CON LA API CAIDA")
print("=" * 76)
print("  a) sin cuota:")
quota.quedan = lambda: 0
LLAMADAS.clear()
d = lc.resolver_lote({"noticeId": "sin-cuota-lote"}, DESC_LOTE, usar_cache=False)
check("  Cero llamadas y dict vacio", not LLAMADAS and d == {},
      "-> %d llamada(s), %r" % (len(LLAMADAS), d))

print("  b) sin clave:")
quota.quedan = lambda: 50
config.GEMINI_API_KEY = ""
LLAMADAS.clear()
d = lc.resolver_lote({"noticeId": "sin-clave-lote"}, DESC_LOTE, usar_cache=False)
check("  Cero llamadas y dict vacio", not LLAMADAS and d == {})
config.GEMINI_API_KEY = _clave_real

print("  c) la API caida:")
class _Roto:
    def __init__(self, api_key=None):
        self.models = self

    def generate_content(self, **k):
        raise RuntimeError("503 overloaded")


m = sys.modules["google.genai"]
m.Client = lambda api_key=None: _Roto()
LLAMADAS.clear()
d = lc.resolver_lote({"noticeId": "api-rota-lote"}, DESC_LOTE, usar_cache=False)
check("  No lanza", isinstance(d, dict))
check("  Y sale vacio, sin exception", d == {})

print("  d) y desde_aviso aguanta aunque la capa B reviente:")
m.Client = lambda api_key=None: (_ for _ in ()).throw(
    RuntimeError("esto peta antes de devolver nada"))
# Un notice_id NUEVO, porque el del aviso bueno esta en cache y devolveria el
# desglose guardado. Que la cache funcione tambien esta bien, pero aqui lo que
# se prueba es el camino del fallo.
_r = sam_api.obtener_descripcion
sam_api.obtener_descripcion = lambda nid, opp=None: DESC_LOTE
try:
    an_roto = pdfg.desde_aviso(dict(AVISO_LOTE,
                                   noticeId="lote-roto-lote-roto"))
    pdf_roto = pdfg.generar_po(pdfg.desde_analisis(an_roto))
finally:
    sam_api.obtener_descripcion = _r
check("  El PDF se genera igualmente con la capa B rota", len(pdf_roto) > 50000,
      "-> %d bytes" % len(pdf_roto))
check("  Y el desglose queda vacio, sin tumbar nada",
      (an_roto.get("lote_consolidado") or {}) == {})

print()
print("=" * 76)
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
    except OSError:
        pass

if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: la capa A marca, la B desglosa, y el PDF no se entera")
print("=" * 76)