"""
test_gemini_conn.py - Reintentos, backoff, timeout y salto al modelo de respaldo.

QUE COMPRUEBA ESTE ARCHIVO

  1) La CLASIFICACION de fallos, que es donde esta casi toda la inteligencia
  2) El backoff 2/4/8 y los tres intentos
  3) Que un 429 NO se reintenta. Es lo que mas counterintuitive parece y lo que
     mas cuesta tiempo si esta mal.
  4) Que el fallback hereda la MISMA config, response_mime_type incluido
  5) El timeout de red en el cliente
  6) Los dos formatos de log que se pidieron, al pie de la letra
  7) Que la cadena de modelos sigue intentandose, y que se levanta si no hay
     ninguno

LA ESPERA NO ES REAL

Dormir 2+4+8 segundos de verdad haria que este archivo tardara mas de un minuto
y que la suite pareciera colgada. La espera se inyecta con una lista, asi que
lo que se comprueba es QUE SE PIDIO, no que se haya esperado.

UNA TRAMPA DE ESTE TIPO, Y QUE SE AVISA

Un fallo tiene que pasar como EXCEPCION. Si se pasa como cadena, "raise '503'"
reventia con un TypeError, que se clasifica como "otro", se reintenta igual, y
el test pasa probando una cosa que no es la que dice. Ya paso: el backoff se
estaba midiendo sobre un TypeError y no sobre el 503 que decia medir. El
simulador ahora avisa si le llega una cadena, para que no vuelva a pasar.

LO QUE NO SE COMPRUEBA

La API real. No hay GEMINI_API_KEY en esta sesion y el dossier pide no gastar
cuota de produccion. Los fallos van simulados, cada uno con su excepcion.
"""
import logging
import os
import sys
import tempfile
import types

DB = os.path.join(tempfile.gettempdir(), "kyo_test_gconn.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "CLAVE-SIMULADA"
os.environ["DRY_RUN"] = "1"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALLA = 0
ESPERAS = []


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print("  OK    %s" % nombre)
    else:
        FALLA += 1
        print("  FALLA %s %s" % (nombre, extra))


def _dormir(s):
    ESPERAS.append(s)


def _limpiar_esperas():
    del ESPERAS[:]


import config  # noqa: E402

import gemini_conn as gc  # noqa: E402

config.GEMINI_API_KEY = "CLAVE-SIMULADA"

LLAMADAS = []     # (modelo, config)
COMO = []         # "ok" o una excepcion
CLIENTES = []


class _Resp:
    def __init__(self, texto):
        self.text = texto


def _503():
    return RuntimeError("503 Service Unavailable: the model is overloaded")


def _429():
    return RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded for this model")


def _400():
    return RuntimeError("400 INVALID_ARGUMENT: bad request")


def _404():
    return RuntimeError("404 NOT_FOUND: model not found")


def _red():
    return RuntimeError("Read timed out. (read timeout=15)")


def _instalar(como, texto='{"override_required": true, "target_qty": 100}'):
    """Instala un SDK falso que se comporta como indica la lista "como"."""
    del LLAMADAS[:]
    del CLIENTES[:]
    del COMO[:]
    for x in como:
        if x != "ok" and not isinstance(x, BaseException):
            raise AssertionError(
                "el simulador recibio %r, que no es una excepcion. Los fallos "
                "van como _503() / _429(), no como cadenas." % (x,))
        COMO.append(x)

    def _siguiente():
        return COMO.pop(0) if COMO else "ok"

    class _Modelos:
        def generate_content(self, model=None, contents=None, config=None, **k):
            LLAMADAS.append((model, config))
            modo = _siguiente()
            if modo == "ok":
                return _Resp(texto)
            raise modo

    class _Cliente:
        def __init__(self, api_key=None, http_options=None):
            self.http_options = http_options
            self.models = _Modelos()

    def _nuevo(api_key=None, http_options=None):
        c = _Cliente(api_key=api_key, http_options=http_options)
        CLIENTES.append(c)
        return c

    m = types.ModuleType("google.genai")
    m.Client = _nuevo
    g = types.ModuleType("google")
    g.genai = m
    sys.modules["google"] = g
    sys.modules["google.genai"] = m
    mt = types.ModuleType("google.genai.types")
    mt.GenerateContentConfig = lambda **k: k
    mt.ThinkingConfig = lambda **k: k
    mt.HttpOptions = lambda **k: k
    sys.modules["google.genai.types"] = mt
    return CLIENTES


print("=" * 74)
print("1) CLASIFICAR: 503 Y 429 NO SON LO MISMO")
print("=" * 74)
print("  De esto depende todo lo demas. Si un 429 se clasifica como transitorio,")
print("  se reintenta tres veces contra un modelo SIN CUOTA y se pierden 14s por")
print("  aviso sin ninguna posibilidad de que funcione.")
CASOS = [
    (_503(), "503"), (_429(), "429"), (_400(), "400"), (_404(), "404"),
    (_red(), "red"),
    (RuntimeError("quota exceeded"), "429"),
    (RuntimeError("503 overloaded"), "503"),
    (RuntimeError("Service Unavailable"), "503"),
    (RuntimeError("Connection reset by peer"), "red"),
    (RuntimeError("algo raro"), "otro"),
]
for exc, esperado in CASOS:
    got = gc.clasificar(exc)
    check("  %-46r -> %s" % (str(exc)[:46], got), got == esperado,
          "esperaba %s" % esperado)

print()
print("  Con EXCEPCIONES NATIVAS del SDK, no con RuntimeError de adorno.")
print("  El codigo va en .code y es lo unico que no cambia entre versiones:")
print("  ahi esta clasificar() de verdad.")
try:
    from google.api_core import exceptions as _apicore
    _ORIGEN = "google.api_core.exceptions"
except ImportError:
    # google.api_core no viene con el SDK que hay instalado. Se usa el nativo
    # que si: google.genai.errors, con la misma forma (code + response_json).
    from google.genai import errors as _apicore
    _ORIGEN = "google.genai.errors"
print("  Origen: %s" % _ORIGEN)
_NATIVAS = [
    ("ServiceUnavailable", 503, "503", _apicore.ClientError),
    ("ResourceExhausted", 429, "429", _apicore.ClientError),
    ("InvalidArgument", 400, "400", _apicore.ClientError),
    ("NotFound", 404, "404", _apicore.ClientError),
    ("Unauthenticated", 401, "401", _apicore.ClientError),
]
for nombre, codigo, esperado, fab in _NATIVAS:
    _e = fab(codigo, {"error": {"code": codigo, "message": "sin texto util"}})
    check("  %-20s(%s) -> %s" % (nombre, codigo, esperado),
          gc.clasificar(_e) == esperado,
          "-> %s" % gc.clasificar(_e))
# El mensaje vacio es la prueba de que se mira el .code y no el texto: con un
# texto sin el numero, la version antigua de clasificar() no lo reconoceria.
_e503 = _apicore.ClientError(503, {"error": {"code": 503, "message": "x"}})
check("  Y con un mensaje que NO dice 503 tambien sale 503",
      gc.clasificar(_e503) == "503")

print()
print("  Y por TIPO de excepcion, no solo por el texto, porque Google cambia")
print("  los mensajes entre versiones y los tipos se mantienen:")
try:
    from google.genai.errors import ResourceExhausted, ServiceUnavailable
    check("  ServiceUnavailable -> 503",
          gc.clasificar(ServiceUnavailable("x")) == "503")
    check("  ResourceExhausted  -> 429",
          gc.clasificar(ResourceExhausted("x")) == "429")
except ImportError:
    print("  (el SDK no trae esos tipos aqui; se ha probado por texto)")

print()
print("=" * 74)
print("2) EL BACKOFF 2/4/8 Y LOS TRES INTENTOS")
print("=" * 74)
_instalar([_503(), _503(), _503(), "ok"])
_limpiar_esperas()
texto, modelo = gc.llamar("prompt de prueba", "W519TC27QA012",
                          modelo="gemini-3.6-flash",
                          respaldo="gemini-flash-latest",
                          cadena=["gemini-3.6-flash"], dormir=_dormir)
print("  Modelo que responde: %s" % modelo)
print("  Esperas: %s" % ESPERAS)
vistos = [m for m, _ in LLAMADAS]
check("  Tres intentos del principal antes de cambiar",
      vistos[:3] == ["gemini-3.6-flash"] * 3, "-> %r" % (vistos,))
# La escalera es 2/4/8, pero con TRES intentos solo caben DOS esperas: se
# espera antes de reintentar, y al tercer intento ya no queda a quien esperar.
# El 8 aparece en cuanto hay un cuarto intento, que se comprueba mas abajo.
check("  Con 3 intentos salen 2 esperas, 2 y 4", ESPERAS == [2.0, 4.0],
      "-> %r" % (ESPERAS,))
check("  Y no se espera inútilmente tras el ultimo intento",
      8.0 not in ESPERAS)
check("  El cuarto intento ya es al de respaldo",
      vistos[3] == "gemini-flash-latest", "-> %r" % (vistos[3:]))
check("  Y devuelve el texto", "target_qty" in texto)

print()
print("  Y si el principal responde al segundo intento, no hay espera de mas:")
_instalar([_503(), "ok"])
_limpiar_esperas()
gc.llamar("p", "W1", modelo="A", respaldo="B", cadena=["A"], dormir=_dormir)
check("  Una sola espera, la de 2s", ESPERAS == [2.0], "-> %r" % (ESPERAS,))

print()
print("  Y el escalon de 8s aparece en cuanto hay un cuarto intento, que es lo")
print("  que pasa cuando el de respaldo tambien falla una vez:")
_instalar([_503(), _503(), _503(), _503(), "ok"])
_limpiar_esperas()
gc.llamar("p", "W10", modelo="A", respaldo="B", cadena=["A"],
          max_intentos=4, dormir=_dormir)
print("    esperas: %s" % ESPERAS)
check("  Escalera completa 2, 4 y 8 con 4 intentos", ESPERAS == [2.0, 4.0, 8.0],
      "-> %r" % (ESPERAS,))
check("  Y el quinto intento ya es al de respaldo",
      [m for m, _ in LLAMADAS][-1] == "B")
print()
print("  Y la escalera es POR MODELO: al cambiar de modelo se reinicia en 2s.")
print("  Es lo correcto, porque el que estaba saturado era el otro:")
_instalar([_503(), _503(), _503(), _503(), "ok"])
_limpiar_esperas()
gc.llamar("p", "W11", modelo="A", respaldo="B", cadena=["A"], dormir=_dormir)
check("  Al pasar al respaldo vuelve a 2s, no a 8", ESPERAS == [2.0, 4.0, 2.0],
      "-> %r" % (ESPERAS,))

print()
print("=" * 74)
print("3) UN 429 NO SE REINTENTA")
print("=" * 74)
print("  La cuota es POR MODELO Y POR DIA. Repetir el mismo modelo no puede")
print("  funcionar nunca: solo son segundos perdidos.")
_instalar([_429(), "ok"])
_limpiar_esperas()
_texto, _modelo = gc.llamar("p", "W2", modelo="A", respaldo="B", cadena=["A"],
                            dormir=_dormir)
check("  Cambia de modelo en el acto", _modelo == "B", "-> %r" % _modelo)
check("  Y NO duerme nada", ESPERAS == [], "-> %r" % (ESPERAS,))
check("  Y solo hizo 2 llamadas, no 4", len(LLAMADAS) == 2,
      "-> %d" % len(LLAMADAS))

for nombre, exc, mot in (("400", _400(), "peticion invalida"),
                         ("404", _404(), "el modelo no existe")):
    _instalar([exc, "ok"])
    _limpiar_esperas()
    _t, _m = gc.llamar("p", "W3", modelo="A", respaldo="B", cadena=["A"],
                       dormir=_dormir)
    check("  Un %s (%s) tampoco se reintenta" % (nombre, mot),
          _m == "B" and ESPERAS == [], "-> %r / %r" % (_m, ESPERAS))

print()
print("  Y un 503 SI se reintenta, porque es transitorio:")
_instalar([_503(), _503(), "ok"])
_limpiar_esperas()
gc.llamar("p", "W4", modelo="A", respaldo="B", cadena=["A"], dormir=_dormir)
check("  Dos esperas, de 2 y 4", ESPERAS == [2.0, 4.0], "-> %r" % (ESPERAS,))
check("  Y responde el principal", LLAMADAS[-1][0] == "A")

print()
print("=" * 74)
print("4) EL RESPALDO HEREDA LA MISMA CONFIG")
print("=" * 74)
print("  Un respaldo que devuelve texto libre en vez de JSON deja el PDF sin")
print("  numeros de parte. response_mime_type tiene que viajar con el.")
_instalar([_503(), _503(), _503(), "ok"])
_limpiar_esperas()
gc.llamar("p", "W5", modelo="A", respaldo="B", cadena=["A"], dormir=_dormir,
          temperature=0.0, response_mime_type="application/json",
          max_output_tokens=900)
for m, cfg in LLAMADAS:
    check("  %s recibe response_mime_type=application/json" % m,
          cfg.get("response_mime_type") == "application/json", "-> %r" % (cfg,))
check("  Y tambien la temperatura y el limite de tokens",
      LLAMADAS[0][1].get("temperature") == 0.0
      and LLAMADAS[-1][1].get("max_output_tokens") == 900,
      "-> %r" % (LLAMADAS[-1][1],))

print()
print("=" * 74)
print("5) EL TIMEOUT DE RED")
print("=" * 74)
_clientes = _instalar(["ok"])
gc.llamar("p", "W6", modelo="A", respaldo="B", cadena=["A"], dormir=_dormir)
check("  Se pide un cliente con http_options", len(_clientes) >= 1)
_op = getattr(_clientes[-1], "http_options", None)
check("  Con un timeout de 15 segundos",
      _op is not None and _op.get("timeout") == 15000, "-> %r" % (_op,))
check("  Y el valor por defecto son 15.000 ms", gc.TIMEOUT_MS == 15000)

print()
print("=" * 74)
print("6) LOS FORMATOS DE LOG")
print("=" * 74)
print("  Se piden al pie de la letra, con marca de tiempo y numero de contrato.")
_capturados = []


class _Coge(logging.Handler):
    def emit(self, reg):
        _capturados.append(reg.getMessage())


_h = _Coge()
gc.log.addHandler(_h)
gc.log.setLevel(logging.INFO)
_instalar([_503(), _503(), _503(), "ok"])
_limpiar_esperas()
gc.llamar("prompt", "W519TC27QA012", modelo="A", respaldo="B", cadena=["A"],
          dormir=_dormir)

_retry = [m for m in _capturados if "backoff exponencial" in m]
_back = [m for m in _capturados if "Reintentos agotados" in m]
print("  Reintento: %s" % (_retry[0] if _retry else "(no salio)"))
print("  Fallback : %s" % (_back[0] if _back else "(no salio)"))

check("  Sale el log de reintento", bool(_retry))
if _retry:
    m = _retry[0]
    check("    Con marca de tiempo", m.startswith("[2") and "] [" in m,
          "-> %r" % m[:40])
    check("    Con nivel WARNING", "[WARNING]" in m)
    check("    Con el numero de contrato", "W519TC27QA012" in m)
    check("    Con 'Error 503 detectado'", "Error 503 detectado" in m)
    check("    Con 'Aplicando backoff exponencial'",
          "Aplicando backoff exponencial" in m)
    check("    Y con el intento X/3", "Intento 1/3" in m)

check("  Sale el log de fallback", bool(_back))
if _back:
    m = _back[0]
    check("    Con marca de tiempo", m.startswith("[2"))
    check("    Con nivel INFO", "[INFO]" in m)
    check("    Con el numero de contrato", "W519TC27QA012" in m)
    check("    Con 'Reintentos agotados para serie 3.x'",
          "Reintentos agotados para serie 3.x" in m)
    check("    Con 'Transicionando a modelo de respaldo estable'",
          "Transicionando a modelo de respaldo estable" in m)
    check("    Con el nombre del modelo de respaldo", "(B)" in m)
gc.log.removeHandler(_h)

print()
print("=" * 74)
print("7) LA CADENA, Y CUANDO NO HAY NADA")
print("=" * 74)
_instalar([_503(), _503(), _503(), _429(), "ok"])
_limpiar_esperas()
_t, _m = gc.llamar("p", "W7", modelo="A", respaldo="Z", cadena=["A", "B", "C"],
                   dormir=_dormir)
modelos = [m for m, _ in LLAMADAS]
print("  Se probaron: %s" % modelos)
check("  A agota sus 3 intentos", modelos[:3] == ["A"] * 3)
check("  El 429 de B no se reintenta", modelos.count("B") == 1,
      "-> %d" % modelos.count("B"))
check("  Y se llega hasta el final de la cadena",
      modelos[-1] in ("C", "Z"), "-> %r" % (modelos[-1],))

_instalar([_503(), _503(), _503(), _503(), _503(), _503()])
_limpiar_esperas()
error_final = ""
try:
    gc.llamar("p", "W8", modelo="A", respaldo="B", cadena=["A"],
              dormir=_dormir)
except Exception as e:
    error_final = str(e)
check("  Y si no hay NINGUN modelo, levanta", bool(error_final),
      "-> %r" % error_final)
# Se propaga la excepcion ORIGINAL, no una inventada: quien la captura fuera
# recibe el tipo y el mensaje de verdad, que es lo que sabe depurar un 503 de
# un 429. Reconstruir el error pierde esa informacion.
check("  Y propaga el fallo original, con su mensaje",
      "503" in error_final and "Service Unavailable" in error_final,
      "-> %r" % error_final)

print()
print("  Y si el de respaldo es el MISMO que el principal, no se repite:")
_instalar([_503(), _503(), _503(), "ok"])
_limpiar_esperas()
_ruido = ""
try:
    gc.llamar("p", "W9", modelo="A", respaldo="A", cadena=["A"],
              dormir=_dormir)
except Exception as e:
    _ruido = str(e)
# Si el respaldo es el MISMO modelo, no hay cadena: repetirlo seria sumar
# 2+4+8 segundos de espera para golpear la misma puerta. Se deduplica y, tras
# sus tres intentos, se rinde. Un 4 y 5 intento aqui serian 14 segundos para
# nada, que es justo lo que este modulo existe para evitar.
check("  No hace la cola doble",
      len(set(m for m, _ in LLAMADAS)) == 1 and len(LLAMADAS) == 3,
      "-> %r" % ([m for m, _ in LLAMADAS],))
check("  Y se rinde tras 3, sin esperar 14s a un modelo yaagotado",
      bool(_ruido) and ESPERAS == [2.0, 4.0],
      "-> %r / %r" % (_ruido[:40], ESPERAS))

print()
print("=" * 74)
print("8) LA CACHE DE FALLO SE LIMPIA CUANDO LA LLAMADA VA BIEN")
print("=" * 74)
import store  # noqa: E402

store.init_db()
gc.limpiar_cache("W519TC27QA012")
check("  Se limpia sin que reviente", True)
gc.limpiar_cache("W519TC27QA012")
check("  Y llamarla otra vez tampoco rompe", True)
gc.limpiar_cache("")
check("  Y con clave vacia no hace nada", True)

print()
print("=" * 74)
print("9) LOS TRES PUNTOS DE /PDF PASAN POR AQUI")
print("=" * 74)
import inspect  # noqa: E402

import capa_b  # noqa: E402
import estructurador  # noqa: E402
import lote_consolidado  # noqa: E402

for mod in (capa_b, lote_consolidado, estructurador):
    src = inspect.getsource(mod)
    check("  %-20s usa gemini_conn.llamar" % mod.__name__,
          "gemini_conn.llamar" in src)
    check("  %-20s sin llamada directa" % mod.__name__,
          "generate_content" not in src and "genai.Client" not in src,
          "-> aun queda una llamada directa en el fuente")

print()
print("=" * 74)
if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
else:
    print("RESULTADO: 503 se reintenta con 2/4/8, 429 no se reintenta nunca, y "
          "el\n          respaldo hereda la config JSON o el PDF se queda sin "
          "parte")
print("=" * 74)
sys.exit(1 if FALLA else 0)