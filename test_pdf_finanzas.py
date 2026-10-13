"""
test_pdf_finanzas.py - Contacto, cantidades, dinero y descripciones limpias.

QUE SE COMPRUEBA

1) Los datos de contacto nuevos en la cabecera y el pie del PDF.
2) El parser de cantidades: 40 UOI, mayusculas y minusculas, y cantidad sin
   unidad. Nada cae al fallback de QTY = 1.
3) Que la cifra del CLIN se refleje en el QTY y en el UNIT del documento.
4) Que UNIT PRICE y AMOUNT no salgan en $0.00 cuando el aviso declara valor, y
   que el estimado salga DECLARADO como estimado.
5) Que cuando el aviso NO declara valor, el precio quede en blanco y no en 0.
6) Que las enmiendas ("AMD 0001 Narrative...") no lleguen a la linea principal
   y que su numero se declare.
7) Que la cabecera de producto sea una frase: modelo + producto + plano.

POR QUE LA CANTIDAD ES LO MÁS DELICADO

Un "1" en la columna QTY de una hoja de cotizacion es un error de cien
unidades, y no se ve: parece un dato. Por eso se prueba con unidades que antes
NO se leian ("BG", que es una de las UOI mas comunes en kits de repuestos
military) y con cantidad sin unidad ("Quantity: 100").
"""
import io
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_finanzas.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
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

SOL = "W519TC27QA012"

# Un aviso de la familia real de esta solicitud: kit de repuestos, una
# enmienda y un valor declarado.
AVISO = {
    "noticeId": "9f8e7d6c5b4a39281706",
    "solicitationNumber": SOL,
    "title": "53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL",
    "fullParentPathName": "Department of the Navy",
    "typeOfSetAsideDescription": "Total Small Business Set-Aside",
    "naicsCode": "332999",
    "classificationCode": "5330",
    "baseType": "Solicitation",
    "postedDate": "2026-10-05 09:00:00",
    "responseDeadLine": "2026-11-20T17:00:00-05:00",
    "uiLink": "https://sam.gov/opp/9f8e7d6c5b4a39281706/view",
    "placeOfPerformance": {"city": "Groton", "state": "Connecticut"},
    "description": "https://x/noticedesc?id=9f8e7d6c5b4a39281706",
}

# Texto realista: la narrativa de la enmienda va PRIMERO, que es como viene de
# SAM.gov, y el material despues. Con el codigo viejo, los primeros 400
# caracteres del BOM eran los cuatro parrafos del papel administrativo.
DESCRIPCION = """AMD 0001 Narrative...
This amendment is being issued to incorporate the solicitation number and to
provide additional time for proposals. Interested parties shall acknowledge
receipt of this amendment in writing. Questions should be directed to the point
of contact. No response is required from this office. Changes are highlighted in
red. See attached document 1. End of amendment 0001.
Line Item Number 0001: NSN 5330-01-586-4931, Mfr Name 12345, CUI 0001XK1234,
Cage 12345. Part Number M7A1-100. Quantity: 100 BG. Unit of Issue BG.
Description: MECHANICAL SEAL KIT, EPDM 70 SHORE A PER ASTM D2000, FOR ROTOR OF
6 INCH PUMP, PER DRAWING 1110700278. Salient characteristics: durometer 70 shore
A, black, temperature range -20 to +120 F.
This contract includes the estimated quantity of 100 each, and the estimated
value of this acquisition is Not to Exceed USD 85,000.00.
"""

# El aviso SIN valor declarado.
#
# Se quita la frase con una REGEX y se COMPRUEBA que ha desaparecido. Con un
# replace() literal no pasaba nada: en el texto de arriba la frase esta partida
# por un salto de linea ("the estimated\nvalue of this acquisition..."), asi
# que la cadena buscada no existia, el replace no sustituyo nada y devolvio el
# texto entero. El test entonces daba por hecho que estaba probando el caso sin
# valor mientras en realidadSeguia con el valor, y la comprobacion de "no se
# inventa un precio" fallaba sin decir por que.
#
# Un replace() que no casa no falla: devuelve el original. En un test, eso es
# medir nada y creer que se ha medido.
import re as _re
DESCRIPCION_SIN_VALOR, _n = _re.subn(
    r"Not\s*to\s*Exceed\s*USD\s*85,000\.00\s*\.?", "", DESCRIPCION)
assert _n == 1, "no se pudo quitar el valor: el caso sin valor no se prueba"
assert "85,000" not in DESCRIPCION_SIN_VALOR
assert "Not to Exceed" not in DESCRIPCION_SIN_VALOR


import sam_api
_real_desc = sam_api.obtener_descripcion


def _con_desc(texto):
    sam_api.obtener_descripcion = lambda nid, opp=None: texto
    try:
        return pdfg.desde_aviso(AVISO)
    finally:
        sam_api.obtener_descripcion = _real_desc


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
print("1) LOS DATOS DE CONTACTO")
print("=" * 76)
print("  EMAIL    = %s" % pdfg.EMAIL)
print("  TELEFONO = %s" % pdfg.TELEFONO)
print("  DIRECCION= %s" % ", ".join(pdfg.DIRECCION))
check("El correo es el nuevo", pdfg.EMAIL == "lamblogisticsusa@gmail.com")
check("El telefono es el nuevo", pdfg.TELEFONO == "+1 (702) 516-6086")
check("Y la direccion NO se ha tocado",
      pdfg.DIRECCION == ["1209 Mountain Rd. PL. NE, Ste. H",
                         "Albuquerque, NM 87110"])
check("Y no queda el correo viejo por ningun lado",
      "operations@lamblogistics.com" not in
      io.open("pdf_generator.py", encoding="utf-8").read())
check("Ni el telefono viejo", "555-0142" not in
      io.open("pdf_generator.py", encoding="utf-8").read())

print()
print("=" * 76)
print("2) EL PARSER DE CANTIDADES Y UNIDADES")
print("=" * 76)
CASOS = [
    ("Quantity 100 EA", (100.0, "EA"), "EA, el caso basico"),
    ("Quantity: 100 BG", (100.0, "BG"),
     "BG, que antes no estaba y es de las UOI mas comunes"),
    ("Quantity: 250 BOX", (250.0, "BX"),
     "BOX escrito, sigla BX en la celda"),
    ("Quantity: 30 BAGS", (30.0, "BG"), "BAGS escrito, sigla BG"),
    ("Qty 15 EACH", (15.0, "EA"), "EACH escrito, sigla EA"),
    ("qty 12 ea", (12.0, "EA"), "minusculas"),
    ("QTY: 40 kg", (40.0, "KG"), "minusculas, unidad de peso"),
    ("Quantity 10 KT", (10.0, "KT"), "kit"),
    ("Quantity 4 SET", (4.0, "SET"), "juego"),
    ("Estimated quantity 2,000", (2000.0, ""),
     "cantidad sola, sin unidad: antes no se leia"),
    ("Quantity: 75", (75.0, ""), "Quantity con dos puntos y sin unidad"),
    ("CLIN 0003: QTY 6 HD", (6.0, "HD"), "HD, barrica"),
    ("Quantity 18 PR", (18.0, "PR"), "par"),
    ("QTY: 3 CON", (3.0, "CON"), "CON, contenedor"),
]
for linea, esperado, etiqueta in CASOS:
    got = pdfg._cantidad_de_linea(linea)
    ok = got == esperado
    if not ok:
        FALLA += 1
    print("  %s %-26s -> %-14s %s" % ("OK   " if ok else "FALLA", linea,
                                      str(got), etiqueta))
    check("  %s" % etiqueta, ok)

print()
print("  Y que NO casa con lo que no debe:")
for linea in ("Drawing 1110700278", "Contract N00019-25-C-1234",
              "Revision C of the specification", "20 MILITARY units"):
    got = pdfg._cantidad_de_linea(linea)
    print("    %-28s -> %s" % (linea, got))

print()
print("=" * 76)
print("3) LAS CIFRAS DEL CLIN LLEGAN AL DOCUMENTO")
print("=" * 76)
an = _con_desc(DESCRIPCION)
bom = an["bom_lineas"]
for b in bom:
    print("  CLIN %-5s %-18s %8s %-4s" % (b["clin"], b["parte"][:18],
                                         b["cantidad"], b["unidad"]))
check("El aviso tiene 1 CLIN y sale 1 linea", len(bom) == 1, "-> %d" % len(bom))
# El codigo de clase ("53--") NO va en la linea de cobro, pero SI tiene que
# seguir en la fila SAM.GOV TITLE de la cabecera, que es una referencia literal
# al aviso: el distributor tiene que poder casar ese titulo con el de SAM.gov.
# Por eso no se comprueba "53-- no aparece en el PDF", sino DONDE aparece.
check("Y el codigo de clase no esta en la linea de cobro",
      "53--" not in bom[0]["descripcion"], "-> %r" % bom[0]["descripcion"][:50])
check("La cantidad es 100, no el 1 del fallback", bom[0]["cantidad"] == 100.0,
      "-> %r" % bom[0]["cantidad"])
check("Y la unidad es BG, no el EA de por defecto", bom[0]["unidad"] == "BG",
      "-> %r" % bom[0]["unidad"])
check("El analisis lleva la misma cantidad",
      float(an["cantidad_total"]) == 100.0)
check("Y la misma unidad", an["unidad_medida"] == "BG")

print()
print("=" * 76)
print("4) EL DINERO NO SALE EN $0.00")
print("=" * 76)
print("  valor declarado por el aviso : USD 85,000.00")
print("  cantidad total               : %s %s" % (an["cantidad_total"],
                                                  an["unidad_medida"]))
print("  precio unitario estimado    : USD %s"
      % an.get("precio_unitario_costo"))
print("  total estimado               : USD %s" % an.get("costo_total_usd"))
# El documento NO lleva precio. El estimado se calcula aparte, pero no se
# imprime: ver la comprobacion del PDF de mas abajo.
check("El aviso declara su valor, y eso se recoge",
      abs((an.get("estimacion_interna") or {}).get("valor_declarado_usd", 0)
          - 85000) < 1,
      "-> %r" % (an.get("estimacion_interna") or {}))
check("Y el estimado sale de ahi: 85.000 / 100 = 850",
      abs(an["estimacion_interna"]["precio_unitario_costo"] - 850.0) < 0.01,
      "-> %r" % an["estimacion_interna"]["precio_unitario_costo"])
check("Pero NO se lleva al documento",
      not an.get("precio_unitario_costo"),
      "-> %r" % an.get("precio_unitario_costo"))

print()
print("  Y en el PDF:")
datos = pdfg.desde_analisis(an, {"nombre": "Radwell International"})
pdf = pdfg.generar_po(datos)
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None
if PdfReader:
    txt = " ".join("\n".join(p.extract_text() or "" for p in
                             PdfReader(io.BytesIO(pdf)).pages).split())
    for lo in ("lamblogisticsusa@gmail.com", "+1 (702) 516-6086",
               "100", "BG", "UNIT PRICE", "AMOUNT"):
        check("  El PDF trae: %-34s" % lo[:34], lo in txt)
    # Y lo que NO debe traer. El estimado propio NO se imprime: el
    # proveedor lo leeria como un precio cotizado y cotizaria sobre nuestro
    # numero en vez de sobre el material.
    for lo in ("850.00", "85,000.00", "$0.00", "ESTIMATES derived from",
               "not quotes and not binding"):
        check("  El PDF NO trae: %-26s" % lo[:26], lo not in txt,
              "-> %r aparece en el PDF" % lo)
    check("  Y explica que el precio lo pone el proveedor",
          "request for quotation" in txt
          and "supplier enters its own" in txt)

print()
print("=" * 76)
print("5) CUANDO EL AVISO NO DECLARA VALOR: BLANCO, NO CERO")
print("=" * 76)
an_sin = _con_desc(DESCRIPCION_SIN_VALOR)
print("  estimado interno: %r" % an_sin.get("estimacion_interna"))
check("Sin valor declarado no hay ni estimado", not an_sin.get("estimacion_interna"),
      "-> %r" % an_sin.get("estimacion_interna"))
datos_sin = pdfg.desde_analisis(an_sin)
pdf_sin = pdfg.generar_po(datos_sin)
check("Y el PDF se genera igualmente", len(pdf_sin) > 50000)
if PdfReader:
    txt_sin = " ".join("\n".join(p.extract_text() or "" for p in
                                 PdfReader(io.BytesIO(pdf_sin)).pages).split())
    check("Y NO pone ningun precio", "$0.00" not in txt_sin
          and "85,000" not in txt_sin, "-> hay un precio en el PDF")
    check("Y el hueco se explica igual", "request for quotation" in txt_sin)

print()
print("=" * 76)
print("6) LAS ENMIENDAS NO LLEGAN A LA LINEA PRINCIPAL")
print("=" * 76)
cab = bom[0]["descripcion"]
esp = bom[0]["especificacion"]
print("  descripcion    : %r" % cab)
print("  especificacion : %r" % esp[:150])
# OJO, Y ESTO ES LO IMPORTANTE DE ESTA SECCION:
#
# Que la especificacion NO CONTENGA tramite se cumple trivialmente si la
# especificacion esta VACIA. Una cadena vacia no contiene "AMD", ni "Narrative",
# ni ninguna otra cosa, asi que todas las comprobaciones de este estilo pasan
# aunque el documento se genere sin especificacion tecnica, que es exactamente
# lo que pasaba: el respaldo de _sin_enmiendas unia el texto entero con
# " ".join() y el filtro por linea lo rechazaba entero, dejando cero lineas.
#
# Por eso lo primero que se comprueba es que la especificacion CONSISTA EN
# ALGO. Las comprobaciones de ausencia solo significan algo si antes se ha
# comprobado que hay contenido.
check("La especificacion NO esta vacia", bool(esp.strip()),
      "-> %r: el filtro se ha comido la especificacion entera" % esp[:60])
check("Y tiene longitud de una especificacion de verdad", len(esp) > 120,
      "-> %d caracteres" % len(esp))
check("Y conserva la especificacion tecnica del material",
      "MECHANICAL SEAL KIT" in esp or "ASTM D2000" in esp
      or "SALIENT" in esp.upper(),
      "-> %r" % esp[:120])

for malo in ("AMD", "Narrative", "amendment", "acknowledge", "highlighted",
             "Interested parties", "point of contact"):
    check("La cabecera no lleva '%s'" % malo, malo not in cab)
    check("La especificacion no lleva '%s'" % malo, malo not in esp)

if PdfReader:
    check("Y el PDF tampoco lleva la narrativa de la enmienda",
          "AMD 0001" not in txt and "Narrative" not in txt
          and "acknowledge" not in txt.lower())
    # Y el codigo de clase sigue donde tiene que estar: en la referencia
    # literal al titulo del aviso, no en la celda del material.
    check("Y el titulo de SAM.gov se conserva literal en la cabecera",
          "SAM.GOV TITLE 53--PARTS KIT" in txt,
          "-> la referencia al aviso ha cambiado y no debe")
    _celda = txt[txt.index("ITEMS ORDERED"):txt.index("SUPPLIER")
                 if "SUPPLIER" in txt[txt.index("ITEMS ORDERED"):]
                 else txt.index("BILL OF MATERIALS")]
    check("Pero no en la celda del material", "53--" not in _celda,
          "-> el codigo de clase se ha colado en la linea de cobro")
check("Y el aviso tenia 1 enmienda y se cuenta",
      an.get("enmiendas") == 1, "-> %r" % an.get("enmiendas"))
if PdfReader:
    check("Y el PDF lo declara", "carries 1 amendment" in txt)

print()
print("=" * 76)
print("7) LA CABECERA ES UNA FRASE: MODELO + PRODUCTO + PLANO")
print("=" * 76)
print("  %s" % cab)
check("Empieza por el numero de parte", cab.startswith("M7A1-100"),
      "-> %r" % cab[:40])
check("Trae el nombre del producto", "SEAL" in cab.upper())
check("Trae el plano, y como referencia al final",
      cab.endswith("Per Drawing 1110700278"), "-> %r" % cab[-40:])
check("Y el codigo de clase de SAM.gov no ensucia la cabecera",
      not cab.startswith("53--"), "-> %r" % cab[:20])
check("La cabecera cabe en una linea corta", len(cab) <= 110,
      "-> %d caracteres: %r" % (len(cab), cab))
check("Y el plano NO se cuela en medio del nombre",
      "SEAL, Per Drawing" not in cab and "Per Drawing 1110700278," not in cab)

print()
print("  Y el _ref_plano no inventa numeros:")
for texto, esperado in (
        ("PER DRAWING 1110700278", "1110700278"),
        ("Per Drawing MS-3305", "MS-3305"),
        ("Drawing No. 12345-A", "12345-A"),
        ("This is a planning document", ""),
        ("per plan de entrega", ""),
        ("", "")):
    got = pdfg._ref_plano(texto)
    check("  %-30r -> %r" % (texto[:30], got), got == esperado)

print()
print("=" * 76)
print("8) LA FORMA QUE PRODUCE LA API: UN SOLO BLOQUE")
print("=" * 76)
# ESTA SECCION ES LA QUE HABRIA EVITADO EL FALLO.
#
# El aviso de arriba es MULTILINEA, y con lineas el filtro puede descartar la de
# la enmienda y conservar la del material. La API NO devuelve multilinea:
# noticedesc llega como un bloque unico, sin saltos de linea. Con un bloque
# unico, un filtro por lineas descarta el AVISO ENTERO, la especificacion sale
# vacia, y el respaldo de desde_aviso mete el texto crudo. Que es exactamente
# lo que aparecia en el PDF.
#
# Se prueban las DOS formas con el mismo contenido, porque la diferencia entre
# ellas es justo lo que no se estaba probando.
BLOB = ("AMD 0001 Narrative... This amendment is being issued to incorporate the "
        "solicitation number and to provide additional time for proposals. Interested "
        "parties shall acknowledge receipt in writing. Questions should be directed to "
        "the point of contact. Line Item Number 0001: NSN 5330-01-586-4931, Cage 12345, "
        "Part Number M7A1-100, Quantity: 100 Units. MECHANICAL SEAL KIT, EPDM 70 SHORE A "
        "PER ASTM D2000, FOR ROTOR OF 6 INCH PUMP, PER DRAWING 1110700278. Salient "
        "characteristics: durometer 70 shore A, black. This contract includes the "
        "estimated quantity of 100 each, and the estimated value of this acquisition is "
        "Not to Exceed USD 85,000.00.")

for etiqueta, texto in (("multilinea", DESCRIPCION), ("BLOQUE UNICO", BLOB)):
    print()
    print("  %s:" % etiqueta)
    _a = _con_desc(texto)
    _bom = _a["bom_lineas"][0]
    print("    cantidad=%-8s unidad=%-5s" % (_bom["cantidad"], _bom["unidad"]))
    print("    desc     : %s" % _bom["descripcion"][:82])
    print("    spec     : %s" % (_bom["especificacion"][:82].replace("\n", " ") or "(VACIA)"))

    check("  %s: la cantidad es 100, no el 1 del fallback" % etiqueta,
          _bom["cantidad"] == 100.0, "-> %r" % _bom["cantidad"])
    check("  %s: la unidad no esta vacia" % etiqueta,
          _bom["unidad"] in ("EA", "BG"), "-> %r" % _bom["unidad"])
    check("  %s: la cabecera lleva modelo, producto y plano" % etiqueta,
          _bom["descripcion"].startswith("M7A1-100")
          and _bom["descripcion"].endswith("Per Drawing 1110700278"),
          "-> %r" % _bom["descripcion"])
    check("  %s: la especificacion NO esta vacia" % etiqueta,
          len(_bom["especificacion"].strip()) > 80,
          "-> %r" % _bom["especificacion"][:70])
    for malo in ("AMD", "Narrative", "amendment", "acknowledge",
                 "point of contact", "additional time"):
        check("  %s: nada de '%s' en la celda" % (etiqueta, malo),
              malo not in _bom["descripcion"]
              and malo not in _bom["especificacion"])

    _d = pdfg.desde_analisis(_a)
    _b = pdfg.generar_po(_d)
    check("  %s: el PDF se genera" % etiqueta, len(_b) > 50000)
    if PdfReader:
        _t = " ".join("\n".join(pg.extract_text() or ""
                                for pg in PdfReader(io.BytesIO(_b)).pages).split())
        _celda = _t[_t.index("ITEMS ORDERED"):_t.index("BILL OF MATERIALS")]
        check("  %s: y la celda del PDF sale limpia" % etiqueta,
              "AMD" not in _celda and "Narrative" not in _celda
              and "acknowledge" not in _celda.lower(),
              "-> la enmienda ha vuelto a la celda")
        check("  %s: y lleva QTY 100" % etiqueta, "100" in _celda)
        # Ni $0.00 ni NUESTRO estimado. El valor que declara el aviso
        # ("Not to Exceed USD 85,000.00") si puede salir, y DEBE: va dentro de
        # la especificacion que el propio gobierno publico, y le dice al
        # distribuidor el tamano de la compra, que le interesa. Lo que no puede
        # salir es un precio nuestro, que se leeria como cotizacion.
        check("  %s: ni $0.00 ni nuestro estimado" % etiqueta,
              "$0.00" not in _t and "850.00" not in _t,
              "-> hay un precio nuestro en el PDF")

print()
print("  Y la diferencia entre las dos formas, que es lo que se ROMPIA:")
_cuerpo_bloque = pdfg._sin_tramitacion(pdfg._sin_enmiendas(BLOB)[0])
check("El filtro saca contenido de un bloque unico",
      len(_cuerpo_bloque) > 100,
      "-> %r: el filtro por lineas se come el aviso entero" % _cuerpo_bloque[:60])
check("Y no saca ninguna Oracion de tramite",
      not any(x in _cuerpo_bloque for x in
              ("amendment", "acknowledge", "point of contact")))

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
print("RESULTADO: contacto, cantidades, dinero y descripciones, correctos")
print("=" * 76)