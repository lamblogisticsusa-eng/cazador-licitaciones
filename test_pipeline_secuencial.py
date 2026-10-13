"""
test_pipeline_secuencial.py - El pipeline en orden, y el punto unico de escritura.

QUE COMPRUEBA ESTE ARCHIVO, Y POR QUE CADA COSA

  1) HIDRATACION: la regla del encargo, y las tres guardas que la hacen segura
  2) Que la sobreescritura NO ocurre en la descarga, solo al renderizar. Esa es
     la diferencia entre este diseno y el anterior, y es justo el fallo que
     hacia que un analisis sacado de la base saliera con QTY 1.
  3) Que el renderizador no toca SAM.gov. Ni una peticion, ni una lectura.
  4) CAPA B: el esquema exacto, y que se normalicen los tipos
  5) CAPA C: la red de seguridad por expresiones regulares
  6) El log exacto que se pidio cuando la API cae
  7) La excepcion de descarga, y CUANDO NO se lanza

LO QUE NO SE COMPRUEBA

La API real. No hay GEMINI_API_KEY y el dossier pide no gastar cuota de
produccion. Las respuestas de Gemini van simuladas, incluida una que falla con
un 400 de clave invalida, que es el caso que dispara la Capa C.
"""
import io
import json
import os
import sys
import tempfile
import types

DB = os.path.join(tempfile.gettempdir(), "kyo_test_secuencial.db")
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
import capa_b
import config
import integral
import pdf_generator as pdfg
import quota
import store

store.init_db()
quota.init()
config.GEMINI_API_KEY = "CLAVE-SIMULADA"


def _datos(cantidad=1, unidad="LOT", desc="Miscellaneous door hardware"):
    return {"lineas": [{"sku": "1", "descripcion": desc,
                        "especificacion": "", "cantidad": cantidad,
                        "unidad": unidad, "precio_unitario": 0.0,
                        "plazo": ""}]}


def _sem(qty=100, verificada=True, **kw):
    s = {"override_required": True, "target_qty": qty, "target_uoi": "EA",
         "manufacturer_brand": "The Timken Company",
         "part_number": "S0594DU0500-S1",
         "product_specification_summary": "M7A1 Bearing - 5in x 96in sheets",
         "verificada": verificada}
    s.update(kw)
    return s


print("=" * 74)
print("1) HIDRATACION: LA REGLA Y SUS TRES GUARDAS")
print("=" * 74)
print("  La regla, tal cual la pidio el encargo:")
print("      if qty == 1 and override_required:")
print("          qty = target_qty")
print("          description = f'{brand} P/N: {part_number} - {summary}'")

d = integral.hidratar(_datos(1, "LOT"), _sem(100))
lin = d["lineas"][0]
check("  qty 1 + override -> la cantidad se corrige", lin["cantidad"] == 100,
      "-> %r" % lin.get("cantidad"))
check("  Y la unidad pasa a la del esquema", lin["unidad"] == "EA")
check("  Y queda marcada", lin.get("qty_corregido") is True)
check("  Y la marca aparece en la descripcion",
      "The Timken Company" in lin["descripcion"])
check("  Y el parte va a la especificacion",
      "S0594DU0500-S1" in lin["especificacion"])
check("  Y el resumen tambien",
      "5in x 96in sheets" in lin["especificacion"])
print("    descripcion  : %s" % lin["descripcion"][:70])
print("    especificacion: %s" % lin["especificacion"][:70])

print()
print("  Y las tres guardas, que es lo que hace que esto no sea peligroso:")
d2 = integral.hidratar(_datos(1, "LOT"), _sem(100, verificada=False))
check("  Sin verificar contra el corpus NO se aplica",
      d2["lineas"][0]["cantidad"] == 1, "-> %r" % d2["lineas"][0]["cantidad"])
check("  Y deja constancia de por que no", bool(d2.get("avisos_hidratacion")))

d3 = integral.hidratar(_datos(1, "LOT"), _sem(0))
check("  Un numero no positivo NO se aplica",
      d3["lineas"][0]["cantidad"] == 1)

d4 = integral.hidratar(_datos(46, "EA"), _sem(100))
check("  Una tabla que ya dice 46 NO se toca",
      d4["lineas"][0]["cantidad"] == 46, "-> %r" % d4["lineas"][0]["cantidad"])
check("  Y dice por que no ha tocado nada",
      "46" in (d4.get("motivo_sin_correccion") or ""))

d5 = integral.hidratar(_datos(1, "LOT"), _sem(100, override_required=False))
check("  Sin override_required no pasa nada", d5["lineas"][0]["cantidad"] == 1)
check("  Sin contexto tampoco", integral.hidratar(_datos(1, "LOT"))[
    "lineas"][0]["cantidad"] == 1)
check("  Y una sobrescrituraBest effort con parte pero sin numero no se aplica",
      integral.hidratar(_datos(1, "LOT"), _sem(0))["lineas"][0][
          "cantidad"] == 1)

print()
print("=" * 74)
print("2) LA CORRECCION NO OCURRE EN LA DESCARGA")
print("=" * 74)
print("  Este es EL cambio de arquitectura. Antes la sobreescritura vivia en")
print("  desde_aviso(), que es el paso de descarga. Un analisis que venia de la")
print("  base no pasaba por ahi, y salia con QTY 1 sin que el papel delatasse")
print("  nada: un PDF 'desactualizado' asi es indistinguible de uno correcto.")

# La funcion de descarga tiene que devolver la INTENCION, sin tocar las lineas.
import inspect
_fuente = inspect.getsource(pdfg.desde_aviso)
check("  desde_aviso() ya no llama a aplicar_correccion",
      "aplicar_correccion" not in _fuente)
check("  Y guarda el contexto para que lo use el renderizador",
      '"capa_b"' in _fuente)
check("  Y generar_po() SI hidrata",
      "hidratar" in inspect.getsource(pdfg.generar_po))
check("  Y el contexto viaja con el documento, no se aplica al construirlo",
      '"contexto_semantico"' in inspect.getsource(pdfg.desde_analisis))

print()
print("=" * 74)
print("3) EL RENDERIZADOR NO TOCA SAM.GOV")
print("=" * 74)
print("  La regla de oro del encargo: el motor grafico lee un diccionario local")
print("  y ni una cosa mas. Se comprueba de la forma mas directa que hay: si")
print("  durante el renderizado se intenta abrir una conexion, revienta.")


class _RedProhibida(Exception):
    pass


def _no_conectar(*a, **k):
    raise _RedProhibida("el renderizador intento salir a la red")


_real_req = sys.modules.get("requests")
sys.modules["requests"] = types.SimpleNamespace(get=_no_conectar)
try:
    pdf = pdfg.generar_po(integral.hidratar(_datos(1, "LOT"), _sem(100)))
    sin_red = True
except _RedProhibida:
    sin_red = False
finally:
    if _real_req is not None:
        sys.modules["requests"] = _real_req
    else:
        sys.modules.pop("requests", None)
check("  generar_po() no abre ni una conexion", sin_red)
check("  Y aun asi produce un documento", len(pdf) > 40000,
      "-> %d bytes" % len(pdf))

from pypdf import PdfReader
txt = " ".join("\n".join(p.extract_text() or "" for p in
                         PdfReader(io.BytesIO(pdf)).pages).split())
check("  Con la cantidad corregida en el papel", "100" in txt)
check("  Y el parte", "S0594DU0500-S1" in txt)
check("  Y sin ningun precio", "$" not in txt and "$0.00" not in txt)

print()
print("=" * 74)
print("4) CAPA B: EL ESQUEMA Y LA NORMALIZACION")
print("=" * 74)
print("  Los seis campos del encargo, en su orden:")
for c in ("override_required", "target_qty", "target_uoi", "manufacturer_brand",
          "part_number", "product_specification_summary"):
    check("    %s" % c, c in capa_b.ESQUEMA)
print("  Y dos opcionales, para no perder los requisitos de entrega:")
for c in ("dimensions_or_specs", "certification_requirements"):
    check("    %s" % c, c in capa_b.ESQUEMA)

v = capa_b.validar({"override_required": "true", "target_qty": "100",
                    "target_uoi": "ea", "manufacturer_brand": "  Timken  ",
                    "part_number": "S0594-S1", "otra_cosa": "que se ignore",
                    "product_specification_summary": "  M7A1  "})
check("  Un bool escrito como texto se convierte", v["override_required"] is True)
check("  Un entero escrito como texto tambien", v["target_qty"] == 100)
check("  Y la sigla se normaliza", v["target_uoi"] == "EA")
check("  Y los textos se recortan", v["manufacturer_brand"] == "Timken")
check("  Y una clave que no es del esquema se tira", "otra_cosa" not in v)
check("  Y se marca de que capa vino", v["_capa"] == "B")

print()
print("  Y las tres normalizaciones que evitan un PDF imposible:")
check("  override sin numero NO sobrevive",
      capa_b.validar({"override_required": True, "target_qty": 0}
                     )["override_required"] is False)
check("  Una sigla que no es de issue NO pasa",
      capa_b.validar({"target_uoi": "FURLONG"})["target_uoi"] == "")
check("  Un texto con saltos se aplana a una linea",
      "\n" not in capa_b.validar({"part_number": "A\\nB"})["part_number"])
check("  Y un JSON que no es dict sale vacio, sin reventar",
      capa_b.validar("esto no es un dict")["_capa"] == "")

print()
print("=" * 74)
print("5) CAPA C: LA RED DE SEGURIDAD")
print("=" * 74)
print("  Sale del corpus con expresiones regulares. NO fuerza una cifra.")
print("  La diferencia parece de matiz y no lo es:")
print("      'QUANTITY: 100 EA'  -> se usa")
print("      'Drawing 1110700278'-> NO se usa, es un plano")
print("      'See sheet 3 of 12' -> NO se usa, es una pagina")

CASOS_C = [
    ("QUANTITY: 100 EA bearings, part S0594DU0500-S1", 100, True),
    ("Lot contains: 24 bearings, 6 exit devices (46 total)", 46, True),
    ("Drawing 1110700278 shall be furnished", 0, False),
    ("See sheet 3 of 12 for details", 0, False),
    ("", 0, False),
    (None, 0, False),
]
for texto, esperado, debe_sobreescribir in CASOS_C:
    r = capa_b.capa_c(texto)
    ok = (r["target_qty"] == esperado and
          r["override_required"] is debe_sobreescribir)
    check("  %-48r -> %s" % ((texto or "")[:48], r["target_qty"]), ok,
          "-> esperaba %s / %s" % (esperado, debe_sobreescribir))
check("  Y el plano NO llega nunca a la columna de cantidad",
      capa_b.capa_c("Drawing 1110700278")["override_required"] is False)
r = capa_b.capa_c("QUANTITY: 100 EA, manufactured by The Timken Company, "
                  "part no S0594DU0500-S1")
check("  Y si encuentra marca y parte, tambien las da",
      "Timken" in r["manufacturer_brand"] and "S0594" in r["part_number"],
      "-> %r / %r" % (r["manufacturer_brand"], r["part_number"]))
check("  Pero no inventa el resumen de especificacion",
      capa_b.capa_c("QUANTITY: 100 EA")["product_specification_summary"] == "")

print()
print("=" * 74)
print("6) CUANDO LA API CAE")
print("=" * 74)
print("  Se pedio este texto de log, y esta en el codigo:")
import re as _re
_fuente_cb = inspect.getsource(capa_b.extraer)
check("  '[WARNING] Capa B no ejecutada debido a fallo de API'",
      "[WARNING] Capa B no ejecutada debido a fallo de API" in _fuente_cb)
print("  Y las fallas que lo disparan, todas ellas de API y no de contenido:")
for s in ("429", "RESOURCE_EXHAUSTED", "API key not valid", "INVALID_ARGUMENT"):
    check("    '%s'" % s, s in capa_b._FALLA_API)

print()
print("  Y ahora de verdad: Gemini que falla con un 400 de clave invalida.")


class _Explota:
    def generate_content(self, *a, **k):
        raise RuntimeError("400 INVALID_ARGUMENT. API key not valid. "
                           "Please pass a valid API key.")


def _instalar_roto():
    m = types.ModuleType("google.genai")
    m.Client = lambda api_key=None: types.SimpleNamespace(models=_Explota())
    g = types.ModuleType("google")
    g.genai = m
    sys.modules["google"] = g
    sys.modules["google.genai"] = m
    mt = types.ModuleType("google.genai.types")
    mt.GenerateContentConfig = lambda **k: k
    mt.ThinkingConfig = lambda **k: k
    sys.modules["google.genai.types"] = mt


_instalar_roto()
r = capa_b.extraer("QUANTITY: 100 EA bearings, part S0594DU0500-S1", "T")
print("    -> capa %s, qty %s, override %s"
      % (r["_capa"], r["target_qty"], r["override_required"]))
check("  Cae a la Capa C", r["_capa"] == "C")
check("  Y saca la cifra del corpus, para que el documento no salga vacio",
      r["target_qty"] == 100 and r["override_required"] is True)
check("  Y sigue siendo la cifra del TEXTO, no una forzosa",
      capa_b.extraer("Drawing 1110700278", "T")["override_required"] is False)

print()
print("  Y sin clave de API, sin llamar a nadie:")
_real = config.GEMINI_API_KEY
config.GEMINI_API_KEY = ""
try:
    r2 = capa_b.extraer("QUANTITY: 100 EA", "T")
finally:
    config.GEMINI_API_KEY = _real
check("  Tambien cae a la Capa C", r2["_capa"] == "C")
check("  Y sin corpus no se inventa nada",
      capa_b.extraer("", "T")["target_qty"] == 0)

print()
print("  Y una respuesta buena, que es el camino normal:")
BUENO = {"override_required": True, "target_qty": 100, "target_uoi": "EA",
         "manufacturer_brand": "The Timken Company",
         "part_number": "S0594DU0500-S1",
         "product_specification_summary": "M7A1 Bearing - 5in x 96in sheets"}


class _Bueno:
    def generate_content(self, *a, **k):
        return types.SimpleNamespace(text=json.dumps(BUENO))


m = types.ModuleType("google.genai")
m.Client = lambda api_key=None: types.SimpleNamespace(models=_Bueno())
g = types.ModuleType("google")
g.genai = m
sys.modules["google"] = g
sys.modules["google.genai"] = m
r3 = capa_b.extraer("corpus cualquiera", "T")
check("  Se lee bien el JSON de la Capa B",
      r3["target_qty"] == 100 and r3["manufacturer_brand"] == "The Timken Company",
      "-> %r" % r3)
check("  Y sale de la capa B, no de la C", r3["_capa"] == "B")
check("  Y el modelo no puede anadir claves al esquema",
      set(r3) - set(capa_b.ESQUEMA) - {"_capa", "verificada"} == set())

print()
print("=" * 74)
print("7) LA EXCEPCION DE DESCARGA, Y CUANDO NO SE LANZA")
print("=" * 74)
AVISO_CON = {"noticeId": "aaa111bbb222",
             "resourceLinks": ["https://sam.ejemplo/gov/recursos"]}
AVISO_SIN = {"noticeId": "ccc333ddd444"}


class _Resp404:
    status_code = 404
    headers = {"Content-Type": "application/json"}
    content = b""

    def json(self):
        return {}


_r = types.ModuleType("requests")
_r.get = lambda u, **k: _Resp404()
sys.modules["requests"] = _r
try:
    integral.hidratar(_datos(1, "LOT"), _sem(100))
    print("  (el estado de Gemini es irrelevante aqui)")

    # a) declara adjuntos y no se pudo bajar ninguno -> ABORTA
    lanzo = False
    try:
        adjuntos.especificaciones(AVISO_CON, "aaa111bbb222", usar_cache=False)
    except adjuntos.FalloDescargaEspecificaciones as e:
        lanzo = True
        msg = str(e)
    check("  Declara adjuntos y no baja ninguno -> ABORTA", lanzo)
    check("  Con el texto que se pidio",
          "[ERROR] Descarga de especificaciones criticas fallida" in msg)

    # b) no declara ninguno -> NO es un fallo
    ruta, texto = adjuntos.especificaciones(AVISO_SIN, "ccc333ddd444",
                                            usar_cache=False)
    check("  Sin adjuntos NO aborta: no hay nada que bajar", texto == "")
    check("  Y deja igualmente el fichero, que es informacion",
          os.path.exists(ruta), "-> %s" % os.path.basename(ruta))
finally:
    sys.modules.pop("requests", None)

print()
print("=" * 74)
if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
else:
    print("RESULTADO: el pipeline va en orden, la escritura es una sola, y la "
          "correccion\n          no sale si no esta escrita en el corpus")
print("=" * 74)
sys.exit(1 if FALLA else 0)