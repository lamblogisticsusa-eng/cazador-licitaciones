"""
test_pdf_ingles.py - Que no quede castellano en el texto del PDF.

EL DIAGNOSTICO, MEDIDO

Dumping real del diccionario que entra en generar_po() con un analisis tipico:

    lineas[0].descripcion     'Material de Rodamiento M7A1 (M7A1-100)'
    lineas[0].especificacion  'Segun Plano MS-3305 Rev C. Requiere Certificado
                               de Conformidad (CoC) a la entrega. Cumple plano
                               de inspeccion DLA.'
    instrucciones            'Entrega en: ...' / 'Embalaje apta para ...'
    condiciones              'Pago a 30 dias contra factura. ...'
    notas                    'Documento emitido por ... No es un documento ...'
    bom[0].*                 las mismas dos que lineas[0]

Eran DOS problemas distintos, y la diferencia decide como se arreglan:

  - instrucciones, condiciones, notas y el texto de reserva: los escribe este
    modulo. No hay nada que traducir, hay algo que escribir en ingles.
  - descripcion, especificacion y el BOM: los devuelve Gemini, en castellano,
    y cambian en cada analisis. Ahi si hace falta una capa de traduccion.

LO QUE SE COMPRUEBA

  1. Los cuatro patrones que pidio el usuario, literalmente.
  2. El texto que genera el modulo, en ingles.
  3. Que la capa no toca el ingles de SAM.gov.
  4. Que las colisiones por subcadena no revientan nada.
  5. Que lo que NO se puede traducir se MARCA, y no se inventa.
  6. Que el PDF sale entero: sin errores de maquetacion, con las cabeceras y
     los margenes igual, y con el aviso de pendiente si lo hay.
"""
import io
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_ingles.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "x"
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


import pdf_generator as pdfg
import store

store.init_db()

# El analisis TIPICO de Kyomoto: lo que devuelve Gemini, en castellano.
AN = {
    "solicitation": "W9127N26QA145",
    "title": "53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL",
    "producto": "Material de Rodamiento M7A1",
    "modelo_especifico": "M7A1-100",
    "unidad_medida": "KIT",
    "cantidad_total": 50,
    "especificacion_tecnica_clave": (
        "Segun Plano MS-3305 Rev C. Requiere Certificado de Conformidad (CoC) "
        "a la entrega. Cumple plano de inspeccion DLA."),
    "lugar_entrega": "Albuquerque / New Mexico / UNITED STATES",
    "limite": "2026-11-14T17:00:00-04:00",
    "set_aside": "Total Small Business Set-Aside",
    "precio_unitario_costo": 1200.0,
    "costo_total_usd": 60000.0,
}

# ------------------------------------------------------------------ Gemini
# Ni esto dobla Gemini. /pdf llama a estructurar(), que va a la API de verdad:
# cuatro modelos, cuatro 400 en unos segundos, en cada ejecucion.
#
# Con la clave falsa que estos tests inyectan no se gasta cuota real (la API la
# rechaza antes de procesar nada) y no sale documento alguno, pero la suite
# deja de ser hermetica y depende de que haya red. Un test que necesita
# internet para probar un formateador no es un test.
#
# Se devuelve VACIO a proposito, y no una respuesta buena: es el mismo camino
# que la suite ya ejercita (el parsing estructurado no esta disponible y el
# PDF sale por el respaldo de regex). Poner una respuesta buena reprobaria
# las aserciones contra una rama que estos tests no comprueban.
import types as _types

_STUB = _types.ModuleType("google.genai")


class _RespGemini:
    text = ""


class _Modelos:
    def generate_content(self, *a, **k):
        return _RespGemini()


class _ClienteGemini:
    models = _Modelos()


_STUB.Client = lambda api_key=None: _ClienteGemini()
_google = _types.ModuleType("google")
_google.genai = _STUB
sys.modules["google"] = _google
sys.modules["google.genai"] = _STUB
_tipos = _types.ModuleType("google.genai.types")
_tipos.GenerateContentConfig = lambda **k: k
_tipos.ThinkingConfig = lambda **k: k
sys.modules["google.genai.types"] = _tipos

print("=" * 76)
print("1) LOS CUATRO PATRONES QUE PIDIO EL USUARIO")
print("=" * 76)
CASOS = [
    ("Material de Rodamiento M7A1",
     "Bearing Material M7A1"),
    ("Segun Plano MS-3305 Rev C",
     "Per Drawing MS-3305 Rev C"),
    ("Según Plano MS-3305 Rev C",
     "Per Drawing MS-3305 Rev C"),
    ("Requiere Certificado de Conformidad (CoC) a la entrega",
     "Requires Certificate of Conformance (CoC) upon delivery"),
    ("Cumple plano de inspeccion DLA.",
     "Complies with Inspection Drawing DLA."),
    ("Cumple planos M7A1-100 y MS-3305",
     "Complies with Drawing M7A1-100 y MS-3305"),
]
for es, esperado in CASOS:
    got, pend = pdfg._ingles(es)
    check("%-52s" % es[:52], (not pend) and got == esperado,
          "\n          salio  %r  pendiente=%s" % (got, pend))
    if got != esperado or pend:
        print("          -> %r  pendiente=%s" % (got, pend))

print()
print("=" * 76)
print("2) EL TEXTO QUE GENERA EL MODULO, EN INGLES DE ORIGEN")
print("=" * 76)
d = pdfg.desde_analisis(AN, {"nombre": "Radwell International"})

for k in ("instrucciones", "condiciones", "notas"):
    print("  %s:" % k)
    for l in str(d.get(k)).split("\n"):
        if l.strip():
            print("      %s" % l)
    print()

check("Instrucciones en ingles",
      "Delivery to:" in d["instrucciones"]
      and "Packaging suitable for transportation" in d["instrucciones"]
      and "certificate of conformance (CoC)" in d["instrucciones"])
check("Y con la fecha de entrega en ingles",
      "must be delivered on or before" in d["instrucciones"]
      and "El material" not in d["instrucciones"])
check("Condiciones en ingles",
      "Payment 30 days net from invoice" in d["condiciones"]
      and "factors receivables" in d["condiciones"])
check("Y con la referencia de SAM.gov",
      "W9127N26QA145" in d["condiciones"]
      and "Pago a 30 dias" not in d["condiciones"])
check("Notas en ingles",
      "Document issued by L.A.M.B. Logistics LLC" in d["notas"]
      and "not an official document of the U.S. Government" in d["notas"])
check("Y sin castellano en ninguna de las tres",
      not any(w in str(d[k]).lower() for k in ("instrucciones", "condiciones", "notas")
              for w in ("entrega en", "embalaje", "pago a", "documento emitido",
                        "es un documento oficial")))

print()
print("  La descripcion y la especificacion, que vienen de Gemini:")
print("    descripcion   : %r" % d["lineas"][0]["descripcion"])
print("    especificacion: %r" % d["lineas"][0]["especificacion"])
print("    bom[0] desc   : %r" % d["bom"][0]["descripcion"])
print("    bom[0] esp   : %r" % d["bom"][0]["especificacion"])
print()
check("La descripcion de Gemini queda en ingles",
      d["lineas"][0]["descripcion"] == "Bearing Material M7A1 (M7A1-100)",
      "-> %r" % d["lineas"][0]["descripcion"])
check("La especificacion tambien",
      d["lineas"][0]["especificacion"].startswith("Per Drawing MS-3305")
      and "Requires Certificate of Conformance (CoC) upon delivery"
      in d["lineas"][0]["especificacion"]
      and "Complies with Inspection Drawing DLA"
      in d["lineas"][0]["especificacion"],
      "-> %r" % d["lineas"][0]["especificacion"])
check("Y sale sin marca de pendiente, porque si se pudo todo",
      pdfg.MARCA_PENDIENTE not in d["lineas"][0]["especificacion"])
check("El BOM lleva lo mismo en ingles",
      d["bom"][0]["descripcion"] == "Bearing Material M7A1 (M7A1-100)")
check("Y el numero de modelo NO se traduce",
      d["bom"][0]["parte"] == "M7A1-100",
      "-> %r" % d["bom"][0]["parte"])
check("Ni las notas avisan de pendientes, porque no hay ninguna",
      "not machine-translated" not in d["notas"])

print()
print("=" * 76)
print("3) QUE LA CAPA NO ROMPE EL INGLES QUE YA VENIA")
print("=" * 76)
INTACTO = ("MECHANICAL SEAL KIT, EPDM 70 SHORE A PER ASTM D2000, "
           "NSN 5330-01-586-4931, CAGE 0JLP7, MFR 12345")
r = pdfg._ingles(INTACTO)
print("  entra  : %s" % INTACTO)
print("  sale   : %r" % r[0])
print("  pendien: %s" % r[1])
check("El ingles de SAM.gov sale intacto y sin marcar", r[0] == INTACTO and not r[1])

# El aviso de SAM.gov, tal cual.
AVISO_ESC = ("Este aviso es una solicitud de cotizacion. Se solicita material "
             "de rodamiento segun plano MS-3305. Cantidad: 50 KIT.")
r2 = pdfg._ingles(AVISO_ESC)
check("Y un aviso con castellano parcial NO se traduce a medias", r2[1],
      "-> %r" % r2[0])
check("Y se devuelve el ORIGINAL, no una mezcla de los dos idiomas",
      AVISO_ESC in r2[0] and "Bearing Material" not in r2[0],
      "-> %r" % r2[0])
print("     -> %r" % r2[0][:96])

print()
print("=" * 76)
print("4) LAS COLISIONES POR SUBCADENA")
print("=" * 76)
# El orden por longitud descendente y los \\b son lo unico que evita que la
# regla corta se coma a la larga. Sin esto, "segun plano" sale "perPlandrawing".
COLISION = [
    # "Plano" SI es castellano (planta, floor plan), asi que marcarse es lo
    # correcto. Lo que se queria comprobar es que "TikTok" siga entero: sin
    # limites de palabra, la regla de "plano" se comeria media palabra.
    ("Plano de TikTok", True, "'plan' no puede comerse una palabra mayor"),
    ("COCINA industrial", False, "'coc' no puede comerse 'cocina'"),
    ("Material de clase superior", True,
     "'clase' es castellano y hay que marcarlo"),
]
for txt, debe, nota in COLISION:
    r = pdfg._ingles(txt)
    print("  %-34r -> %-34r pendiente=%-5s %s"
          % (txt, r[0], r[1], nota))
    check("  %-32s pendiente=%s" % (txt[:32], debe), r[1] == debe)
check("Y 'TikTok' no se ha tocado", "TikTok" in pdfg._ingles("Plano de TikTok")[0])
check("Ni 'cocina' se ha tocado", "COCINA" in pdfg._ingles("COCINA industrial")[0])

# Y lo que NO debe marcar el detector: ingles normal de un aviso.
INGLES_OK = [
    "Physical item per the SAM.gov specification",
    "Delivery to: Albuquerque / New Mexico / UNITED STATES",
    "Payment 30 days net from invoice",
    "MECHANICAL SEAL KIT, EPDM 70 SHORE A PER ASTM D2000",
    "Quotation valid for 30 days from the date of issue",
    "Each shipment must include a packing list",
]
for s in INGLES_OK:
    r = pdfg._ingles(s)
    check("El ingles no se marca: %s" % s[:44], not r[1] and r[0] == s,
          "-> %r pendiente=%s" % (r[0], r[1]))

print()
print("=" * 76)
print("5) LO QUE NO SE PUEDE TRADUCIR: SE MARCA, NO SE INVENTA")
print("=" * 76)
# Ni una sola palabra tecnica: se detecta por las palabras de funcion ("de",
# "para", "el"), que estan en toda frase castellana y en ninguna inglesa.
raro = "Pantalones de gamuza para el almirante en dia de lluvia"
r = pdfg._ingles(raro)
print("  entra : %s" % raro)
print("  sale  : %r" % r[0])
check("Queda marcado como pendiente", r[1])
check("Y conserva el texto original en vez de inventar",
      raro in r[0], "-> %r" % r[0])
check("Con la marca visible", pdfg.MARCA_PENDIENTE in r[0])

# Y en el documento completo.
raro_an = dict(AN, producto=raro)
d2 = pdfg.desde_analisis(raro_an)
check("El documento entero lo avisa en las notas",
      "not machine-translated" in d2["notas"])
# Son las OCURRENCIAS marcadas en el documento, no los campos logicos: la
# descripcion se imprime dos veces, en la linea de la orden y en la del BOM,
# y el usuario ve dos marcas. Decir "1 field" con dos marcas en el papel haria
# pensar que se ha colado una.
check("Y dice cuantos campos son",
      "2 field(s)" in d2["notas"], "-> %r" % d2["notas"][-140:])
check("Y la descripcion va marcada",
      pdfg.MARCA_PENDIENTE in d2["lineas"][0]["descripcion"])

print()
print("=" * 76)
print("6) QUE EL PDF SALE ENTERO Y CON LOS MARGENES")
print("=" * 76)
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

for etiqueta, an in (("caso bueno", AN), ("con pendientes", raro_an)):
    try:
        datos = pdfg.desde_analisis(an, {"nombre": "Radwell International"})
        pdf = pdfg.generar_po(datos)
        check("  %-16s genera %d bytes sin error de maquetacion"
              % (etiqueta, len(pdf)), len(pdf) > 50000)
    except Exception as e:
        check("  %-16s no revienta" % etiqueta, False,
              "-> %s: %s" % (type(e).__name__, e))

# Un texto larguisimo en una celda es lo que revienta las tablas de reportlab.
# Se prueba con un NSN enorme y una frase sin espacios.
largo = dict(AN, producto="BearingMaterial" * 200,
             especificacion_tecnica_clave="X" * 3000)
try:
    datos_largo = pdfg.desde_analisis(largo)
    pdf = pdfg.generar_po(datos_largo)
    check("Una celda de 3000 caracteres sin espacios no revienta",
          len(pdf) > 50000)
    # No basta con que no reviente: tiene que AVISAR de que se ha recortado.
    desc_larga = datos_largo["lineas"][0]["descripcion"]
    check("Y avisa de cuanto texto se ha quedado fuera",
          "more characters" in desc_larga and "sam.gov" in desc_larga,
          "-> %r" % desc_larga[-90:])
except Exception as e:
    check("Una celda de 3000 caracteres sin espacios no revienta", False,
          "-> %s: %s" % (type(e).__name__, e))

if PdfReader:
    datos = pdfg.desde_analisis(AN, {"nombre": "Radwell International"})
    crudo = "\n".join(p.extract_text() or "" for p in
                       PdfReader(io.BytesIO(pdfg.generar_po(datos))).pages)
    # El documento parte las lineas por anchura de celda, que es lo que tiene
    # que hacer: "Complies with Inspection\nDrawing DLA." se lee bien en el
    # PDF. Comparar la cadena entera mediria el particionado de reportlab.
    texto = " ".join(crudo.split())
    print()
    print("  Lo que tiene que LEERSE en el PDF (el que va al proveedor):")
    for lo in ("Bearing Material M7A1",
               "Per Drawing MS-3305",
               "Requires Certificate of Conformance (CoC) upon delivery",
               "Complies with Inspection Drawing DLA",
               "Delivery to:", "Packaging suitable for transportation",
               "Payment 30 days net from invoice",
               "factors receivables within the United States",
               "Document issued by L.A.M.B. Logistics LLC",
               "not an official document of the U.S. Government",
               "Purchase order issued by L.A.M.B. Logistics LLC"):
        print("    %-58s %s" % (lo[:58], "si" if lo in texto else "NO"))
        check("  El PDF trae: %s" % lo[:56], lo in texto)

    print()
    print("  Y lo que NO debe quedar en castellano:")
    for mal in ("Segun Plano", "Certificado de Conformidad",
                "Material de Rodamiento", "Embalaje apta",
                "Pago a 30 dias", "Documento emitido",
                "No es un documento oficial", "Cumple plano de inspeccion",
                "Orden de compra emitida", "Embalaje apta"):
        presente = mal in texto
        print("    %-58s %s" % (mal[:58], "FALTA" if not presente else "ESTA"))
        check("  NO esta: %s" % mal[:56], not presente)

    # Las cabeceras y la disposicion no se tocan.
    print()
    print("  Y la disposicion sigue igual que antes:")
    for cabecera in ("PURCHASE ORDER", "PURCHASE ORDER NO.", "DATE", "SAM.GOV REFERENCE",
                     "RESPONSE DUE", "SUPPLIER / VENDOR", "ITEMS ORDERED",
                     "SHIPPING INSTRUCTIONS", "TERMS AND CONDITIONS",
                     "BILL OF MATERIALS"):
        check("  Sigue la cabecera: %s" % cabecera, cabecera in texto)

print()
print("=" * 76)
print("7) EL AVISO DE PENDIENTES EN EL MENSAJE DE TELEGRAM")
print("=" * 76)
# El aviso tiene que salir ANTES de que el usuario descargue el fichero. Si
# solo estuviera dentro del PDF, habria que abrirlo para enterarse, y para
# entonces ya esta en el movil y a un clic de mandarlo al proveedor.
import asyncio
import main as m
import sam_api

_real_desc = sam_api.obtener_descripcion
sam_api.obtener_descripcion = lambda nid, opp=None: (
    "Segun Plano MS-3305 Rev C. Requiere Certificado de Conformidad (CoC) "
    "a la entrega. Cumple plano de inspeccion DLA.")


class _Estado:
    def __init__(self, con, txt):
        self._con = con
        self._txt = txt

    async def edit_text(self, txt, parse_mode=None, **k):
        self._con.append(("estado", txt))


class _Bot:
    async def send_chat_action(self, chat_id, action):
        pass


class _Msg:
    def __init__(self, con):
        self._con = con
        self.text = ""
        self.chat = type("C", (), {"id": 1})()

    async def reply_text(self, txt, parse_mode=None, **k):
        self._con.append(("texto", txt))
        return _Estado(self._con, txt)

    async def reply_document(self, document=None, filename=None, caption=None,
                             parse_mode=None, **k):
        self._con.append(("doc", filename))

    def get_bot(self):
        return _Bot()


AVISO_TEST = {
    "noticeId": "a1b2c3d4e5f6a7b8c9d0",
    "solicitationNumber": "W9127N26QA145",
    "title": "53--PARTS KIT,SEAL REPLACEMENT",
    "fullParentPathName": "Department of the Navy",
    "postedDate": "2026-10-02 09:00:00",
    "responseDeadLine": "2026-11-14T17:00:00-04:00",
    "placeOfPerformance": {"city": "Philadelphia", "state": "Pennsylvania"},
    "description": "https://x/noticedesc?id=1",
}
_sam_get = sam_api._get


class _R:
    status_code = 200

    def __init__(self, c):
        self._c = c

    def json(self):
        return self._c


sam_api._get = lambda params, reintentos=None: _R(
    {"totalRecords": 1, "opportunitiesData": [AVISO_TEST]})

m.ParseMode = type("PM", (), {"HTML": "HTML"})


def _mensajes(solnum):
    con = []
    up = type("U", (), {})()
    up.message = _Msg(con)
    # OJO: el texto del mensaje tiene que traer el comando entero. _argtexto()
    # hace text.split()[1:], asi que con el texto vacio el handler no recibe
    # ningun argumento y se limita a listar lo que hay. Es lo que paso la
    # primera vez: el doble no escribia el comando y la prueba no reciba nada.
    up.message.text = "/pdf %s" % solnum
    up.effective_chat = type("C", (), {"id": 1})()
    asyncio.run(m.cmd_pdf(up, type("X", (), {})()))
    return con


try:
    con = _mensajes(AVISO_TEST["solicitationNumber"])
finally:
    sam_api._get = _sam_get
    sam_api.obtener_descripcion = _real_desc

final = [txt for k, txt in con if k == "estado"]
print("  %s" % (final[-1].replace("\n", " ")[:230] if final else "SIN RESPUESTA"))
check("Hay mensaje final", bool(final))
check("Dice que todo el texto va en ingles tecnico",
      final and "ingles tecnico" in final[-1])
# Con este aviso la especificacion SI se traduce entera, asi que no hay marca.
check("Y no avisa de pendientes cuando no los hay",
      final and "SEE NOTE" not in final[-1],
      "-> %r" % (final[-1][-120:] if final else None))
check("Y el PDF llega igualmente", any(k == "doc" for k, _ in con))

print()
print('=' * 76)
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: el texto del PDF es ingles tecnico de punta a punta")
print("=" * 76)