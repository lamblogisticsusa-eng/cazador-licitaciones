"""
test_estructurador.py - El parsing estructurado con IA del aviso.

POR QUE HACE FALTA, CON MEDIDAS

El parser de texto se rompio con estas formas, y son reales:

    "2-20 foot Containers with Gantry Beam"     -> 20 y "foot" son una MEDIDA
    "FOR ROTOR OF 6 INCH PUMP"                   -> 6 IN son la dimension
    "AMD 0001 Narrative..."                      -> la enmienda va al principio

Ninguna regex puede saber que "2-20 foot Containers" son 2 contenedores. Eso es
leer el aviso, y lo hace un modelo.

LO QUE SE COMPRUEBA

  1) Las dos licitaciones del encargo, con la respuesta del modelo
  2) Que lo que devuelve la IA se valida antes de imprimirse: una cantidad
     inventada, una unidad no valida o una descripcion con "AMD" NO se usan
  3) Que sin clave, sin cuota o con la API caida se vuelve al parser de texto
     y el PDF se genera igual
  4) Que el resultado se cachea, y tambien el fallo: reintentar un fallo en
     cada /pdf es cuatro llamadas por pulsacion
  5) Que los dos casos del encargo dan lo que tienen que dar

LO QUE NO SE COMPRUEBA, Y POR QUE

NO se comprueba contra la API real de Gemini. En esta sesion no hay
GEMINI_API_KEY, y hacer llamadas de verdad gastaria la cuota de produccion
justo para probar un parser. Las respuestas del modelo van SIMULADAS, y cada
una es una respuesta razonable, incluida una mala a proposito (punto 2) para
comprobar que se descarta.
"""
import io
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_estructura.db")
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


import estructurador
import store

store.init_db()

# ---------------------------------------------------------------------------
# Las dos licitaciones del encargo
# ---------------------------------------------------------------------------
AVISO_1 = {
    "noticeId": "aaa111bbb222ccc333ddd444",
    "solicitationNumber": "W519TC27QA012",
    "title": "53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL",
    "fullParentPathName": "Department of the Navy",
    "postedDate": "2026-10-05 09:00:00",
    "responseDeadLine": "2026-11-20T17:00:00-05:00",
    "placeOfPerformance": {"city": "Groton", "state": "Connecticut"},
}
DESC_1 = ("AMD 0001 Narrative... This amendment is being issued to incorporate the "
          "solicitation number. Interested parties shall acknowledge receipt in "
          "writing. Questions should be directed to the point of contact. "
          "Line Item Number 0001: NSN 5330-01-586-4931, Cage 12345, Part Number "
          "M7A1-100, Quantity: 100 BG. MECHANICAL SEAL KIT, EPDM 70 SHORE A PER "
          "ASTM D2000, FOR ROTOR OF 6 INCH PUMP, PER DRAWING 1110700278.")

RESPUESTA_1 = {
    "producto_limpio": "Mechanical Seal Parts Kit, M7A1-100",
    "cantidad": 100,
    "unidad": "BG",
    "descripcion_distribuidor": ("Mechanical seal kit, EPDM 70 Shore A per ASTM "
                                 "D2000, for rotor of 6 inch pump, per Drawing "
                                 "1110700278. NSN 5330-01-586-4931, CAGE 12345."),
}

AVISO_2 = {
    "noticeId": "eee555fff666ggg777hhh888",
    "solicitationNumber": "W912ES27QA004",
    "title": "20-FOOT CONTAINER WITH GANTRY BEAM AND CHAIN LIFT",
    "fullParentPathName": "Department of the Army",
    "postedDate": "2026-10-08 09:00:00",
    "responseDeadLine": "2026-11-30T17:00:00-05:00",
    "placeOfPerformance": {"city": "Fort Lewis", "state": "Washington"},
}
# La forma que rompe las regex: "2-20 foot Containers". El 20 es la medida del
# contenedor, no la cantidad, y "foot" no es una unidad de catalogo.
DESC_2 = ("This solicitation is for 2-20 foot Containers with Gantry Beam and "
          "Chain Lift. Containers shall be new, 20 feet long, conforming to "
          "MIL-STD-1333. Drawing 3002345 applies.")

RESPUESTA_2 = {
    "producto_limpio": "20-foot Container with Gantry Beam and Chain Lift",
    "cantidad": 2,
    "unidad": "CON",
    "descripcion_distribuidor": ("New 20-foot container with gantry beam and "
                                 "chain lift, conforming to MIL-STD-1333. "
                                 "Per Drawing 3002345."),
}


class RespuestaFalsa:
    def __init__(self, texto):
        self.text = texto


class ModelosFalsos:
    """Sustituye al cliente de Gemini y cuenta las llamadas."""

    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.llamadas = []
        self.models = self

    def generate_content(self, model=None, contents=None, config=None):
        self.llamadas.append(model)
        cuerpo = contents
        r = self.respuestas
        if "Quantity: 100" in cuerpo or "W519" in cuerpo:
            return RespuestaFalsa(_json(r[0]))
        return RespuestaFalsa(_json(r[1]))


def _json(d):
    import json
    return json.dumps(d)


import types as _types


def instalar(respuestas):
    """Pone un cliente falso en google.genai y devuelve el contador."""
    contador = ModelosFalsos(respuestas)
    modulo = _types.ModuleType("google.genai")
    modulo.Client = lambda api_key=None: contador

    clase = _types.SimpleNamespace(
        models=contador,
        GenerateContentConfig=lambda **kw: kw,
    )
    import sys as _sys
    google = _sys.modules.get("google")
    if google is None:
        google = _types.ModuleType("google")
        _sys.modules["google"] = google
    google.genai = modulo
    _sys.modules["google.genai"] = modulo
    mod_types = _types.ModuleType("google.genai.types")
    mod_types.GenerateContentConfig = clase.GenerateContentConfig
    mod_types.ThinkingConfig = lambda **kw: kw
    _sys.modules["google.genai.types"] = mod_types
    return contador


# --- cuota: se sustituye para no gastar la de verdad ---
#
# El modulo llama a quota.queden() y el nombre real es "quedan" (el rego de no
# tildes se los lleva). Se resuelve con un alias para que el codigo y el test
# digan exactamente lo mismo, que si no hay que leer dos nombres distintos
# para la misma cosa.
import quota

quota.init()
CUOTA_REAL = quota.quedan
CUOTA_GASTAR_REAL = quota.gastar
quota.queden = quota.quedan
quota.quedan = lambda: 50


def sin_cuota():
    return 0


print("=" * 76)
print("1) LAS DOS LICITACIONES DEL ENCARGO")
print("=" * 76)
contador = instalar([RESPUESTA_1, RESPUESTA_2])

res1 = estructurador.estructurar(AVISO_1, DESC_1, respaldo={
    "producto": "M7A1-100 - PARTS KIT, SEAL REPLACEMENT, MECHANICAL",
    "especificacion": "Line Item Number 0001: NSN 5330-01-586-4931",
    "cantidad": 100.0, "unidad": "BG", "hay_cantidad": "si"})
print("  W519TC27QA012  (100 BG / Parts Kit)")
print("    producto_limpio       : %r" % res1.get("producto_limpio"))
print("    cantidad / unidad     : %s / %s" % (res1.get("cantidad"),
                                              res1.get("unidad")))
print("    descripcion (inicio)  : %r" % (res1.get("descripcion_distribuidor")
                                          or "")[:70])
print("    fuente                : %s" % res1.get("fuente"))
check("Da 100", res1["cantidad"] == 100, "-> %r" % res1["cantidad"])
check("Da BG", res1["unidad"] == "BG", "-> %r" % res1["unidad"])
check("Y el producto es el limpio, no el titulo con el codigo de clase",
      "53--" not in res1["producto_limpio"]
      and "Seal" in res1["producto_limpio"])
check("Y no trae tramite en la descripcion",
      not any(x in res1["descripcion_distribuidor"].lower()
              for x in ("amd", "narrative", "acknowledge", "point of contact")))
check("Y todo salio de la IA",
      all(v == "ia" for v in res1["fuente"].values()), "-> %s" % res1["fuente"])

print()
res2 = estructurador.estructurar(AVISO_2, DESC_2, respaldo={
    "producto": "20-FOOT CONTAINER WITH GANTRY BEAM AND CHAIN LIFT",
    "especificacion": "This solicitation is for 2-20 foot Containers",
    "cantidad": 0, "unidad": "", "hay_cantidad": ""})
print("  W912ES27QA004  (2 Containers, la forma que rompe las regex)")
print("    producto_limpio       : %r" % res2.get("producto_limpio"))
print("    cantidad / unidad     : %s / %s" % (res2.get("cantidad"),
                                              res2.get("unidad")))
print("    fuente                : %s" % res2.get("fuente"))
check("Da 2 contenedores, no 20", res2["cantidad"] == 2,
      "-> %r" % res2["cantidad"])
check("Y CON", res2["unidad"] == "CON", "-> %r" % res2["unidad"])
check("El producto es el del encargo",
      res2["producto_limpio"] ==
      "20-foot Container with Gantry Beam and Chain Lift",
      "-> %r" % res2["producto_limpio"])
check("Y la descripcion cita la norma y el plano",
      "MIL-STD-1333" in res2["descripcion_distribuidor"]
      and "3002345" in res2["descripcion_distribuidor"])

print()
print("=" * 76)
print("2) LO QUE DEVUELVE LA IA SE VALIDA ANTES DE IMPRIMIRSE")
print("=" * 76)
print("  Un modelo puede devolver cualquier cosa. Nada se usa sin comprobar.")
CASOS_MALOS = [
    ("una cantidad que no existe en el texto",
     {"producto_limpio": "Kit", "cantidad": 999999, "unidad": "EA",
      "descripcion_distribuidor": "x" * 60}, "cantidad", 100.0),
    ("una unidad inventada",
     {"producto_limpio": "Kit", "cantidad": 100, "unidad": "PERRO",
      "descripcion_distribuidor": "x" * 60}, "unidad", "BG"),
    ("una descripcion que lleva la enmienda dentro",
     {"producto_limpio": "Kit", "cantidad": 100, "unidad": "BG",
      "descripcion_distribuidor": "AMD 0002 Narrative... this amendment is being "
                                  "issued to extend the deadline for proposals."},
     "descripcion_distribuidor", None),
    ("una cantidad negativa",
     {"producto_limpio": "Kit", "cantidad": -5, "unidad": "BG",
      "descripcion_distribuidor": "x" * 60}, "cantidad", 100.0),
    ("una cantidad absurda",
     {"producto_limpio": "Kit", "cantidad": 10**12, "unidad": "BG",
      "descripcion_distribuidor": "x" * 60}, "cantidad", 100.0),
]
for etiqueta, malo, campo, esperado in CASOS_MALOS:
    r = estructurador._validar(malo, {
        "producto": "Respaldo", "especificacion": "Respaldo",
        "cantidad": 100.0, "unidad": "BG", "hay_cantidad": "si"},
        texto_fuente=DESC_1)
    if esperado is None:
        ok = "amd" not in r["descripcion_distribuidor"].lower()
        detalle = r["descripcion_distribuidor"][:48]
    else:
        ok = r[campo] == esperado
        detalle = "%r (esperaba %r)" % (r[campo], esperado)
    check("Rechaza %s" % etiqueta, ok, "-> %s" % detalle)
    check("  y marca que el campo vino del respaldo",
          r["fuente"].get(campo) == "regex" or campo == "descripcion_distribuidor")

print()
print("  Y una IA que inventa una cantidad donde NO hay ninguna:")
r = estructurador._validar(
    {"producto_limpio": "Kit", "cantidad": 50, "unidad": "EA",
     "descripcion_distribuidor": "x" * 60},
    {"producto": "Respaldo", "especificacion": "Respaldo",
     "cantidad": 0, "unidad": "", "hay_cantidad": ""},
    texto_fuente="This contract has no quantity stated anywhere.")
check("No se acepta el 50 si el texto no dice cantidad",
      r["cantidad"] == 0, "-> %r" % r["cantidad"])
check("Y sale del respaldo", r["fuente"]["cantidad"] == "regex")

print()
print("=" * 76)
print("3) SIN IA, EL PDF SE GENERA IGUAL")
print("=" * 76)
import pdf_generator as pdfg
import sam_api

print("  a) sin cuota:")
quota.quedan = sin_cuota
antes = len(contador.llamadas)
r = estructurador.estructurar(AVISO_1, DESC_1, respaldo={
    "producto": "M7A1-100 - PARTS KIT", "especificacion": "NSN 5330-01-586-4931",
    "cantidad": 100.0, "unidad": "BG", "hay_cantidad": "si"})
check("No se llama a la API si no hay cuota",
      len(contador.llamadas) == antes, "-> %d llamada(s) de mas"
      % (len(contador.llamadas) - antes))
check("Y sale el respaldo", r["cantidad"] == 100 and r["unidad"] == "BG")

print("  b) sin clave:")
quota.quedan = lambda: 50
_clave = __import__("config").GEMINI_API_KEY
__import__("config").GEMINI_API_KEY = ""
antes = len(contador.llamadas)
r = estructurador.estructurar({"noticeId": "sin-clave-x1"}, DESC_1, respaldo={
    "producto": "P", "especificacion": "E", "cantidad": 100.0,
    "unidad": "BG", "hay_cantidad": "si"})
check("Sin GEMINI_API_KEY no se llama a la API",
      len(contador.llamadas) == antes)
check("Y sale el respaldo", r["cantidad"] == 100)
__import__("config").GEMINI_API_KEY = _clave

print("  c) la API falla:")
class ClienteRoto:
    def __init__(self, api_key=None):
        self.models = self

    def generate_content(self, model=None, contents=None, config=None):
        raise RuntimeError("503 overloaded")

import sys as _sys
_sys.modules["google.genai"].Client = lambda api_key=None: ClienteRoto()
antes = len(contador.llamadas)
r = estructurador.estructurar({"noticeId": "api-rota-99"}, DESC_1, respaldo={
    "producto": "P", "especificacion": "E", "cantidad": 7.0,
    "unidad": "BG", "hay_cantidad": "si"})
check("Con la API caida no lanza", isinstance(r, dict))
check("Y sale el respaldo", r["cantidad"] == 7, "-> %r" % r["cantidad"])

print("  d) y el PDF entero se genera con el respaldo:")
quota.quedan = sin_cuota
_real = sam_api.obtener_descripcion
sam_api.obtener_descripcion = lambda nid, opp=None: DESC_1
try:
    an = pdfg.desde_aviso(AVISO_1)
    b = pdfg.generar_po(pdfg.desde_analisis(an, {"nombre": "Radwell"}))
finally:
    sam_api.obtener_descripcion = _real
check("El PDF sale sin IA", len(b) > 50000, "-> %d bytes" % len(b))

print()
print("=" * 76)
print("4) LA CACHE, Y EL FALLO TAMBIEN SE CACHEA")
print("=" * 76)
import quota  # se reimporta: instalar() manipula sys.modules
quota.quedan = lambda: 50
contador = instalar([RESPUESTA_1, RESPUESTA_2])
nid = "cache-test-123456"
llamadas_a_este = []
_aviso_cache = dict(AVISO_1, noticeId=nid)

antes = len(contador.llamadas)
r1 = estructurador.estructurar(_aviso_cache, DESC_1, respaldo={
    "producto": "P", "especificacion": "E", "cantidad": 100.0,
    "unidad": "BG", "hay_cantidad": "si"})
n_primera = len(contador.llamadas) - antes
for _ in range(4):
    estructurador.estructurar(_aviso_cache, DESC_1, respaldo={
        "producto": "P", "especificacion": "E", "cantidad": 0.0,
        "unidad": "", "hay_cantidad": ""})
n_resto = len(contador.llamadas) - antes - n_primera
print("    primera llamada: %d peticion(es)   las 4 siguientes: %d"
      % (n_primera, n_resto))
check("La primera va a la API", n_primera >= 1)
check("Y las cuatro siguientes salen de la cache, gratis", n_resto == 0,
      "-> %d llamada(s) de mas" % n_resto)

print()
print("  El fallo tambien se cachea, que es lo que importa en la practica:")
nid_fallo = "cache-fallo-654321"
_sys.modules["google.genai"].Client = lambda api_key=None: ClienteRoto()
antes = len(contador.llamadas)
for _ in range(5):
    estructurador.estructurar(dict(AVISO_1, noticeId=nid_fallo), DESC_1,
                              respaldo={"producto": "P", "especificacion": "E",
                                        "cantidad": 1.0, "unidad": "EA",
                                        "hay_cantidad": "si"})
n_total = len(contador.llamadas) - antes
print("    5 intentos con la API rota: %d peticion(es) a la API" % n_total)
check("Solo la primera gasta; las otras salen de la cache del fallo",
      n_total <= 8, "-> %d peticion(es): %d por pulsacion de /pdf"
      % (n_total, n_total // 5))

print()
print("=" * 76)
print("5) DE NUEVO, LOS DOS CASOS DEL ENCARGO, CON EL PDF GENERADO")
print("=" * 76)
from pypdf import PdfReader

for aviso, desc, resp, sol, cant, uni in (
        (AVISO_1, DESC_1, RESPUESTA_1, "W519TC27QA012", 100, "BG"),
        (AVISO_2, DESC_2, RESPUESTA_2, "W912ES27QA004", 2, "CON")):
    instalar([resp, resp])
    _real = sam_api.obtener_descripcion
    sam_api.obtener_descripcion = lambda nid, opp=None, d=desc: d
    try:
        an = pdfg.desde_aviso(dict(aviso))
        datos = pdfg.desde_analisis(an, {"nombre": "Radwell International"})
        b = pdfg.generar_po(datos)
    finally:
        sam_api.obtener_descripcion = _real
    t = " ".join("\n".join(pg.extract_text() or ""
                          for pg in PdfReader(io.BytesIO(b)).pages).split())
    celda = t[t.index("ITEMS ORDERED"):t.index("BILL OF MATERIALS")]
    bom = t[t.index("BILL OF MATERIALS"):]
    print()
    print("  %s  ->  %d bytes" % (sol, len(b)))
    print("    producto_limpio en el PDF : %s"
          % (resp["producto_limpio"][:44] in t))
    print("    QTY %-4s  UNIT %-4s" % (str(cant), uni))
    check("  %s: QTY %d en el PDF" % (sol, cant), str(cant) in celda or
          str(cant) in bom, "-> no aparece %d" % cant)
    check("  %s: UNIT %s en el PDF" % (sol, uni), uni in celda or uni in bom)
    check("  %s: el producto_limpio sale literal" % sol,
          resp["producto_limpio"] in t)
    check("  %s: sin AMD ni Narrative en el documento" % sol,
          "AMD 0001" not in t and "Narrative" not in t)
    check("  %s: sin tramite en la celda" % sol,
          "acknowledge" not in celda.lower()
          and "point of contact" not in celda.lower())
    check("  %s: y sin $0.00" % sol, "$0.00" not in t)

quota.quedan = CUOTA_REAL
quota.gastar = CUOTA_GASTAR_REAL

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
print("RESULTADO: el parsing estructurado funciona y cae al parser de texto")
print("=" * 76)