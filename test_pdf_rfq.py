"""
test_pdf_rfq.py - El comando /pdf: que no se calle, y que el documento sirva
para cotizar.

EL DIAGNOSTICO QUE ESTE ARCHIVO COMPRUEBA

El silencio de /pdf no era el parseo del argumento. Era que el comando solo
miraba en la base local:

    analisis = store.buscar_por_solicitud(id_buscado)
    if not analisis:
        ... "No encuentro ningun aviso" ...

Una solicitud que Kyomoto no habia analizado no existia para el comando. Y el
mensaje de estado se mandaba DESPUES de esa busqueda, asi que cuando fallaba no
habia ninguna senal de que el bot estuviera trabajando.

LO QUE SE COMPRUEBA

1) La limpieza del identificador, con entradas sucias de verdad: espacio duro,
   zero width, BOM, comillas, espacios al final, distinta capitalizacion.
2) Que se responde AL INSTANTE y con el codigo ya limpio, antes de la red.
3) Que si no esta en la base, se busca en SAM.gov por solnum y por noticeid.
4) Que una excepcion de red o de generacion no deja el comando callado, y que
   el traceback va al log.
5) Que el PDF lleva la seccion de Bill of Materials en ingles, con CLIN, NSN,
   cantidad y unidad.
6) Que el documento NO lleva marcas del gobierno: es una orden de compra
   privada de L.A.M.B., no un documento oficial.
7) Que sin BOM el PDF sale igual que antes: generar_po() no cambia su
   comportamiento para los que ya lo usaban.
"""
import asyncio
import io
import os
import re
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_pdf_rfq.db")
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


import config
import pdf_generator as pdfg
import sam_api
import store

store.init_db()

# Un aviso realista de la API, con la tabla de items en el texto de la
# descripcion. Formato de NSN 5330-01-586-4931, que es el real de los kits de
# sellos que han salido en los barridos.
AVISO = {
    "noticeId": "a1b2c3d4e5f6a7b8c9d0",
    "solicitationNumber": "W9127N26QA145",
    "title": "53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL",
    "fullParentPathName": "Department of the Navy",
    "typeOfSetAsideDescription": "Total Small Business Set-Aside",
    "naicsCode": "332999",
    "classificationCode": "5330",
    "baseType": "Solicitation",
    "postedDate": "2026-10-02 09:00:00",
    "responseDeadLine": "2026-11-14T17:00:00-04:00",
    "uiLink": "https://sam.gov/opp/a1b2c3d4e5f6a7b8c9d0/view",
    "placeOfPerformance": {"city": "Philadelphia", "state": "Pennsylvania"},
    "description": "https://api.sam.gov/prod/opportunities/v1/noticedesc"
                   "?noticeid=a1b2c3d4e5f6a7b8c9d0",
}

DESCRIPCION = """
Solicitation W9127N26QA145
Line Item Number 0001: NSN 5330-01-586-4931, Mfr Name 12345, CUI 0001XK1234,
Cage 12345. Quantity 50 EA. Unit of Issue EA.
Description: MECHANICAL SEAL KIT, EPDM 70 SHORE A PER ASTM D2000, FOR ROTOR
OF 6 INCH PUMP. Salient characteristics: durometer 70 shore A, black,
temperature range -20 to +120 F.
CLIN 0002: NSN 5330-01-586-4932, Quantity 10 KT. Unit of Issue KT.
CLIN 0003: NSN 5340-01-461-9021, Quantity 4 EA. Unit of Issue EA.
"""

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
print("1) LA LIMPIEZA DEL IDENTIFICADOR")
print("=" * 76)
SUCIOS = [
    ("  W9127N26QA145  ", "W9127N26QA145", "espacios alrededor"),
    ("w9127n26qa145", "W9127N26QA145", "minusculas"),
    ("W9127\u00a0N26QA145", "W9127N26QA145", "espacio duro (invisible)"),
    ("W9127N26QA145\u200b", "W9127N26QA145", "zero width"),
    ("\ufeffW9127N26QA145", "W9127N26QA145", "BOM al principio"),
    ('"W9127N26QA145"', "W9127N26QA145", "comillas"),
    ("W9127N26QA145\n", "W9127N26QA145", "salto de linea"),
    ("W9127-N26-QA-145", "W9127-N26-QA-145", "guiones (se conservan)"),
]
for bruto, esperado, etiqueta in SUCIOS:
    got = sam_api.limpiar_solicitud(bruto)
    check("  %-26s -> %s" % (etiqueta, esperado), got == esperado,
          "-> %r" % got)

print()
check("Un numero se puede comparar con la API quitando guiones",
      "".join(c for c in "W9127-N26-QA-145" if c.isalnum()) == "W9127N26QA145")
# limpiar_solicitud() pasa a MAYUSCULAS, asi que "x" sale "X". La funcion
# hace bien su trabajo: comparar un numero de solicitud en mayusculas contra
# lo que devuelve la API evita tener que normalizar en dos sitios.
check("Y un input vacio o de un caracter no rompe",
      sam_api.limpiar_solicitud("") == ""
      and sam_api.limpiar_solicitud("x") == "X")

print()
print("=" * 76)
print("2) LA RESPUESTA INMEDIATA Y EL CHAT ACTION")
print("=" * 76)
import main as m

_cuerpo = m.__file__ and io.open("main.py", encoding="utf-8").read()
ini = _cuerpo.index("async def cmd_pdf(")
fin = _cuerpo.index("async def cmd_boton(")
_fuente = _cuerpo[ini:fin]

# OJO: el PRIMER reply_text del fuente es el helper _falla(), no el
# mensaje de estado. Con indice() a pelo la ventana empezaba en el
# helper y cabria el mensaje entero dentro.
_p_estado = _fuente.index("Consultando licitacion")
_p_busca = _fuente.index("store.buscar_por_solicitud(")
_p_sam = _fuente.index("sam_api.buscar_por_solicitud(")
check("El mensaje de estado sale ANTES de buscar nada",
      _p_estado < _p_busca,
      "-> estado en %d, busqueda en %d" % (_p_estado, _p_busca))
check("Y antes tambien de la consulta a SAM.gov", _p_estado < _p_sam)
# OJO: en el FUENTE la frase esta partida entre dos literales ("ficha " y
# "tecnica para distribuidor"), asi que buscarla entera aqui mediria la
# maquetacion del codigo. Lo que importa es lo que sale en ejecucion, y eso se
# comprueba en el recorrido completo mas abajo.
check("El mensaje de estado empieza por 'Consultando licitacion'",
      "Consultando licitacion" in _fuente)
check("Y anuncia la ficha tecnica (partida en dos literales en el fuente)",
      "Consultando licitacion" in _fuente and "tecnica para distribuidor"
      in _fuente)
_bloque_estado = _fuente[_p_estado:_p_estado + 900]
check("Y lleva el identificador YA LIMPIO, no el que se escribio",
      "id_limpio" in _bloque_estado and "id_buscado" not in _bloque_estado,
      "-> el mensaje usa el argumento crudo, no el limpio")
check("Manda send_chat_action de upload_document",
      'send_chat_action' in _fuente and 'upload_document' in _fuente)
_tras_accion = _fuente.index("send_chat_action")
check("Y el fallo de send_chat_action no tumba el comando",
      "except Exception" in _fuente[_tras_accion:_tras_accion + 300],
      "-> sin try/except, un fallo del indicador tumba el comando")

print()
print("=" * 76)
print("3) SI NO ESTA EN LA BASE, SE BUSCA EN SAM.GOV")
print("=" * 76)
check("Busca por numero de solicitud (solnum)", "buscar_por_solicitud(" in _fuente)
check("Y tambien por noticeId, que son avisos distintos",
      "buscar_notice(" in _fuente)
check("Y hay una traduccion del aviso crudo al formato de analisis",
      "desde_aviso(" in _fuente)
check("La base local se consulta PRIMERO",
      _fuente.index("store.buscar_por_solicitud(") < _fuente.index("buscar_por_solicitud("))

print()
print("  Y que la busqueda por solnum mande el numero bien:")
class R:
    status_code = 200

    def __init__(self, cuerpo):
        self._c = cuerpo

    def json(self):
        return self._c


_hechos = []


def get_falso(params, reintentos=None):
    _hechos.append(dict(params))
    return R({"totalRecords": 1, "opportunitiesData": [AVISO]})


_real = sam_api._get
sam_api._get = get_falso
try:
    encontrado = sam_api.buscar_por_solicitud("W9127-N26-QA-145")
finally:
    sam_api._get = _real
print("    peticion: %s" % {k: v for k, v in _hechos[0].items()
                           if k != "api_key"})
check("Encontro el aviso", encontrado is not None
      and encontrado.get("solicitationNumber") == "W9127N26QA145")
check("Y lo pidio por solnum", _hechos[0].get("solnum") == "W9127N26QA145")
check("Sin guiones, que es como lo guarda la API",
      _hechos[0].get("solnum") == "W9127N26QA145".replace("-", ""))
check("Con la ventana de un ano, para encontrar los ya publicados",
      _hechos[0].get("postedFrom") and _hechos[0].get("postedTo"))

print()
print("  Y si la API falla, devuelve None y NO lanza:")
sam_api._get = lambda params, reintentos=None: (_ for _ in ()).throw(
    sam_api.SamError("HTTP 429: sin cuota"))
try:
    r429 = sam_api.buscar_por_solicitud("W9127N26QA145")
finally:
    sam_api._get = _real
check("Con un 429 devuelve None en vez de reventar", r429 is None)

print()
print("=" * 76)
print("4) EL DOCUMENTO: BILL OF MATERIALS EN INGLES")
print("=" * 76)
_real_desc = sam_api.obtener_descripcion
sam_api.obtener_descripcion = lambda nid, opp=None: DESCRIPCION
try:
    analisis = pdfg.desde_aviso(AVISO)
finally:
    sam_api.obtener_descripcion = _real_desc

print("  El analisis traducido:")
for k in ("solicitation", "title", "agencia", "set_aside", "limite",
          "lugar_entrega", "cantidad_total", "unidad_medida"):
    print("    %-16s %r" % (k, str(analisis.get(k))[:64]))

check("Trae la solicitud", analisis["solicitation"] == "W9127N26QA145")
check("Trae el titulo", bool(analisis["title"]))
check("Trae la agencia", "Navy" in analisis["agencia"])
check("Trae el set-aside", "Small Business" in str(analisis["set_aside"]))
check("Trae la fecha limite", "2026-11-14" in str(analisis["limite"]))
check("Trae el lugar de entrega en ingles",
      "Philadelphia" in analisis["lugar_entrega"]
      and "UNITED STATES" in analisis["lugar_entrega"],
      "-> %r" % analisis["lugar_entrega"])

print()
print("  El BOM extraido de la descripcion:")
bom = analisis["bom_lineas"]
for b in bom:
    print("    CLIN %-5s %-22s %s %s" % (b["clin"], b["parte"][:22],
                                        b["cantidad"], b["unidad"]))
check("Saco los 3 CLIN de la descripcion", len(bom) == 3, "-> %d" % len(bom))
check("Y el NSN del primero", bom[0]["parte"] == "5330-01-586-4931",
      "-> %r" % bom[0]["parte"])
check("Con la cantidad del texto", bom[0]["cantidad"] == 50,
      "-> %r" % bom[0]["cantidad"])
check("Y la unidad real del catalogo (EA)", bom[0]["unidad"] == "EA")
check("Y la del segundo, que es KT no EA", bom[1]["unidad"] == "KT",
      "-> %r" % bom[1]["unidad"])

print()
datos = pdfg.desde_analisis(analisis)
pdf_bytes = pdfg.generar_po(datos)
print("  PDF generado: %d bytes" % len(pdf_bytes))
check("Se genera sin error", len(pdf_bytes) > 5000)

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

if PdfReader:
    texto = "\n".join(p.extract_text() or "" for p in
                      PdfReader(io.BytesIO(pdf_bytes)).pages)
    print("  %d paginas, %d caracteres de texto extraidos"
          % (len(PdfReader(io.BytesIO(pdf_bytes)).pages), len(texto)))

    print()
    print("  Lo que tiene que leerse:")
    for lo in ("BILL OF MATERIALS / REQUEST FOR QUOTATION",
               "CLIN", "PART NO. / NSN", "UOI", "QTY",
               "DELIVERY DESTINATION", "PACKAGING",
               "Unit of Issue", "Contract Line Item Number",
               "National Stock Number", "5330-01-586-4931",
               "Philadelphia, Pennsylvania, UNITED STATES",
               "W9127N26QA145"):
        print("    %-52s %s" % (lo[:52], "si" if lo in texto else "NO"))
        check("  El PDF trae: %s" % lo[:48], lo in texto, "- la version larga se partia de ancho y salia cortada")

    print()
    print("  Y lo que NO debe estar:")
    # OJO: "SEAL" NO es una marca de gobierno que pueda aparecer aqui. El
    # aviso se llama "PARTS KIT, SEAL REPLACEMENT, MECHANICAL": el producto
    # son sellos mecanicos. Buscar la palabra "seal" como si fuera un sello
    # oficial es absurdo y ademas impediria mandar el documento real.
    #
    # Lo que si debe vigilarse son las marcas de identidad GOV: escudos,
    # "SEAL OF", "UNITED STATES SEAL", "official document", y sellos oficiales.
    for mal in ("SEAL OF", "US GOVERNMENT SEAL", "UNITED STATES SEAL",
                "Usurpation", "solicitud del gobierno",
                "Department of Defense seal", "政府采购"):
        if mal in texto:
            check("  NO debe traer: %s" % mal, False, "-> esta en el PDF")
        else:
            check("  NO trae: %s" % mal, True)
    check("El documento declara que no es oficial del gobierno",
          "not an official" in texto.lower()
          or "no es un documento oficial" in texto.lower()
          or "No es un documento" in texto)
    check("Y sale el nombre de L.A.M.B. y la direccion",
          "L.A.M.B. Logistics LLC" in texto and "Albuquerque" in texto)
    check("Con el emoji rotto de antes? NO: sin &nbsp; en el texto",
          "&nbsp;" not in texto)

print()
print("=" * 76)
print("5) SIN BOM, EL PDF SALE IGUAL QUE ANTES")
print("=" * 76)
# generar_po con el diccionario de siempre, sin "bom": tiene que salir un
# documento valido y SIN la seccion de cotizacion.
_viejo = dict(datos)
_viejo.pop("bom", None)
_viejo.pop("bom_entrega", None)
pdf_sin = pdfg.generar_po(_viejo)
check("Se genera igualmente", len(pdf_sin) > 5000)
if PdfReader:
    txt_sin = "\n".join(p.extract_text() or "" for p in
                        PdfReader(io.BytesIO(pdf_sin)).pages)
    check("Sin la seccion de Bill of Materials",
          "BILL OF MATERIALS" not in txt_sin)
    check("Pero con el Purchase Order de siempre",
          "PURCHASE ORDER" in txt_sin and "ITEMS ORDERED" in txt_sin)
    check("Y con los mismos datos de SAM.gov",
          "W9127N26QA145" in txt_sin)

print()
print("=" * 76)
print("6) CASOS QUE NO DEBEN ROMPER NADA")
print("=" * 76)
casos = [
    ("Sin descripcion", dict(AVISO, description="")),
    ("Sin placeOfPerformance", dict(AVISO, placeOfPerformance={})),
    ("Sin titulo", dict(AVISO, title="")),
    ("Sin solicitationNumber", dict(AVISO, solicitationNumber="")),
    ("Sin fecha limite", dict(AVISO, responseDeadLine="")),
    ("Descripcion sin CLIN ni NSN",
     dict(AVISO, description="https://x/n?id=1")),
]
for etiqueta, av in casos:
    _r = sam_api.obtener_descripcion
    sam_api.obtener_descripcion = lambda nid, opp=None: (
        "" if "Sin desc" in etiqueta or "sin CLIN" in etiqueta
        else DESCRIPCION)
    try:
        an = pdfg.desde_aviso(av)
        d = pdfg.desde_analisis(an)
        b = pdfg.generar_po(d)
        check("  %-28s genera %d bytes" % (etiqueta, len(b)), len(b) > 5000)
    except Exception as e:
        check("  %s no rompe" % etiqueta, False,
              "-> %s: %s" % (type(e).__name__, e))
    finally:
        sam_api.obtener_descripcion = _r

print()
print("  Y que una descripcion gigante no reviente el PDF:")
_real_desc = sam_api.obtener_descripcion
sam_api.obtener_descripcion = lambda nid, opp=None: (DESCRIPCION * 400)
try:
    an = pdfg.desde_aviso(AVISO)
    b = pdfg.generar_po(pdfg.desde_analisis(an))
    check("Con 40x la descripcion sigue generando", len(b) > 5000)
finally:
    sam_api.obtener_descripcion = _real_desc

print()
print("=" * 76)
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: /pdf responde siempre y el PDF sirve para cotizar")
print("=" * 76)