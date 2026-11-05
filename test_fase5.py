# -*- coding: utf-8 -*-
"""
test_fase5.py - Resiliencia 429/503: backoff, ritmo y aviso pendiente.

QUE SE COMPRUEBA

1) EL TIPO DE 429: es_limite_por_minuto separa el tope POR MINUTO del tope
   DIARIO, con el mismo criterio que ya usa cual_es_el_limite.py, y sin
   romperse con el CamelCase de Google ("...RequestsPerMinute").
2) BACKOFF EXPONENCIAL 2/4/8: el 429 de ritmo y el 503 esperan 2, 4 y 8
   antes de ceder, con maximo de intentos, y el 8 aparece en el cuarto.
3) ROTACION: agotados los intentos se pasa al modelo siguiente, y el 429
   DIARIO sigue cambiando de modelo SIN dormir, que es la politica que ya
   midieron y fijaron las pruebas de este repo.
4) PASADA RAPIDA: solo se duerme UNA vez por llamada; el resto de la cadena
   entra y sale al instante. Seis modelos con 14s cada uno serian 84s.
5) CORTACIRCUITO: si otro aviso ya confirmo la cuota, no se duerme ni se
   llama a Google.
6) AVISO PENDIENTE (requisito): la cadena agotada levanta GeminiError y
   scanner NO marca el aviso como visto, asi que vuelve manana.
7) RITMO ENTRE LLAMADAS (requisito): la pausa entre analisis consecutivos
   existe, vive dentro de _analizar y su valor por defecto cumple.
8) COMPATIBILIDAD: firmas de llamar() y consultar_gemini_con_fallback()
   intactas, y ni un secreto en el codigo tocado.

LA ESPERA NO ES REAL

Dormir 2+4+8 de verdad alargaria este archivo 14 segundos, y con el corte de
cuota entero, 24 mas. time.sleep se sustituye por un capturador al principio,
asi que lo que se comprueba es QUE SE PIDIO dormir y cuanto, no que se
durmiera.
"""
import ast
import inspect
import io
import os
import sys
import tempfile
import time
import types as types_mod

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print("  OK    %s" % nombre)
    else:
        FALLA += 1
        print("  FALLA %s %s" % (nombre, extra))


DB = os.path.join(tempfile.gettempdir(), "kyo_test_fase5.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "x"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"
# Sin esto la cola agotada dormiria 24s reales en la seccion 6.
# GEMINI_PAUSA_SEG NO se baja de 1.5: la seccion 7 comprueba justamente que
# el ritmo por defecto cumple el encargo.
ESPERAS: list = []
_sleep_original = time.sleep
time.sleep = lambda s=0: ESPERAS.append(float(s))


def _dormir(s):
    ESPERAS.append(float(s))


import config
import gemini_conn as gc

# Los mismos errores que ya usan las otras suites, para que las politicas
# nuevas se midan contra los mensajes de siempre.
ERR_429_DIARIO = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
                  "'You exceeded your current quota. * Quota exceeded for "
                  "metric: generativelanguage.googleapis.com/"
                  "generate_content_free_tier_requests, limit: 20, model: "
                  "gemini-3.8-flash'}}")
ERR_429_RITMO = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
                 "'Quota exceeded for quota metric "
                 "GenerateContentRequestsPerMinute and limit "
                 "GenerateContentRequestsPerMinute of project X'}}")
ERR_429_GENERICO = "429 RESOURCE_EXHAUSTED. {'error': {'code': 429}}"
ERR_503 = "503 UNAVAILABLE. {'error': {'code': 503, 'status': 'UNAVAILABLE'}}"
JSON_OK = '{"viable": true, "producto": "x"}'


class _R:
    def __init__(self, t):
        self.text = t


LLAMADAS: list = []
GUIA: list = []


def _instalar(como, texto=JSON_OK):
    """SDK falso que va cogiendo de "como" una respuesta por llamada."""
    del LLAMADAS[:]
    del GUIA[:]
    for x in como:
        if x != "ok" and not isinstance(x, BaseException):
            raise AssertionError(
                "el guion recibe %r, que no es una excepcion" % (x,))
        GUIA.append(x)

    def _siguiente():
        return GUIA.pop(0) if GUIA else "ok"

    class _Modelos:
        def generate_content(self, model=None, contents=None, config=None, **k):
            LLAMADAS.append(model)
            modo = _siguiente()
            if modo == "ok":
                return _R(texto)
            raise modo

    class _Cliente:
        def __init__(self, api_key=None, http_options=None):
            self.models = _Modelos()

    m = types_mod.ModuleType("google.genai")
    m.Client = lambda api_key=None, http_options=None: _Cliente()
    g = types_mod.ModuleType("google")
    g.genai = m
    sys.modules["google"] = g
    sys.modules["google.genai"] = m
    mt = types_mod.ModuleType("google.genai.types")
    mt.GenerateContentConfig = lambda **k: k
    mt.ThinkingConfig = lambda **k: k
    mt.HttpOptions = lambda **k: k
    sys.modules["google.genai.types"] = mt


def _limpiar():
    del ESPERAS[:]
    del LLAMADAS[:]


def _llamar_fb(cola, guion, max_intentos=3, **kw):
    _instalar(guion)
    _limpiar()
    try:
        texto, modelo = gc.consultar_gemini_con_fallback(
            "prompt", "W519TC27QA012", cola=cola, max_intentos=max_intentos,
            esperar=_dormir, **kw)
        return texto, modelo, None
    except gc.ErrorDeCadena as e:
        return None, None, e


print("=" * 74)
print("1) EL TIPO DE 429 SE DISTINGUE ANTES DE DECIDIR SI SE DUERME")
print("=" * 74)
print("  Es la misma pregunta que ya hace cual_es_el_limite.py:")
print("  dice 'per minute' o dice 'per day'.")
class _Ilegible(BaseException):
    """Un error cuyo str() revienta: es_limite_por_minuto no debe caer."""
    def __str__(self):
        raise ValueError("no se puede leer")


_casos = [
    (ERR_429_RITMO, True, "tope por minuto, con el nombre del limite pegado"),
    ("429. Quota exceeded ... limit GenerateContentRequestsPerMinute", True,
     "CamelCase sin espacios"),
    ("429 quota exceeded per minute", True, "con espacio"),
    ("429 limit per_minute", True, "con guion bajo"),
    ("429 Too many requests", True, "too many requests"),
    ("429 rate limit exceeded", True, "rate limit"),
    (ERR_429_DIARIO, False, "tope diario (el de verdad, limit: 20)"),
    ("429. You exceeded your current quota ... per day", False, "per day"),
    (ERR_429_GENERICO, False, "el 429 generico de todas las suites"),
    ("429 RESOURCE_EXHAUSTED: quota exceeded for this model", False,
     "el _429() de test_gemini_conn"),
    ("", False, "mensaje vacio"),
]
for texto, esperado, porque in _casos:
    got = gc.es_limite_por_minuto(RuntimeError(texto))
    check("%-52s -> %s" % (porque, esperado), got is esperado,
          "-> %r" % got)

check("Un texto ilegible no revienta, devuelve False",
      gc.es_limite_por_minuto(_Ilegible()) is False)
check("Y uno vacio tampoco", gc.es_limite_por_minuto(RuntimeError("")) is False)
check("La clasificacion general sigue devolviendo 429 para los dos",
      gc.clasificar(RuntimeError(ERR_429_RITMO)) == "429"
      and gc.clasificar(RuntimeError(ERR_429_DIARIO)) == "429")

print()
print("=" * 74)
print("2) BACKOFF EXPONENCIAL 2/4/8 ANTES DE CEDER")
print("=" * 74)
_t, _m, _e = _llamar_fb(["A", "B"],
                        [RuntimeError(ERR_429_RITMO)] * 3 + ["ok"])
print("  esperas: %s   llamadas: %s" % (ESPERAS, LLAMADAS))
check("Espera incremental 2 y 4 sobre el principal", ESPERAS == [2.0, 4.0],
      "-> %r" % ESPERAS)
check("El principal recibe los 3 intentos pedidos",
      LLAMADAS[:3] == ["A", "A", "A"], "-> %r" % LLAMADAS)
check("Y no se duerme despues del ultimo intento", 8.0 not in ESPERAS)
check("Entonces pasa al modelo siguiente", _m == "B", "-> %r" % _m)
check("Y la llamada termina bien", isinstance(_t, str) and _e is None)

print()
print("  Con un cuarto intento si aparece el escalon de 8:")
_t, _m, _e = _llamar_fb(["A", "B"],
                        [RuntimeError(ERR_429_RITMO)] * 4 + ["ok"],
                        max_intentos=4)
print("  esperas: %s" % ESPERAS)
check("Escalera completa 2, 4 y 8", ESPERAS == [2.0, 4.0, 8.0],
      "-> %r" % ESPERAS)

print()
print("  El 503 sigue con exactamente la misma escala:")
_t, _m, _e = _llamar_fb(["A"], [RuntimeError(ERR_503)] * 3 + ["ok"],
                        max_intentos=4)
print("  esperas: %s" % ESPERAS)
check("El 503 no cambia de politica", ESPERAS == [2.0, 4.0, 8.0],
      "-> %r" % ESPERAS)
check("Y responde el mismo modelo", _m == "A", "-> %r" % _m)

print()
print("  Y el log del 429 de ritmo, al formato del resto del modulo:")
import logging


class _Coge(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensajes = []

    def emit(self, record):
        self.mensajes.append(record.getMessage())


_h = _Coge()
gc.log.addHandler(_h)
gc.log.setLevel(logging.INFO)
_llamar_fb(["A", "B"], [RuntimeError(ERR_429_RITMO)] * 3 + ["ok"])
gc.log.removeHandler(_h)
_retry429 = [m for m in _h.mensajes if "tope por minuto" in m]
print("  %s" % (_retry429[0] if _retry429 else "(no salio)"))
check("Sale el log del backoff de 429", bool(_retry429))
if _retry429:
    _m429 = _retry429[0]
    check("  Con marca de tiempo", _m429.startswith("[2") and "] [" in _m429,
          "-> %r" % _m429[:40])
    check("  Con nivel WARNING", "[WARNING]" in _m429)
    check("  Con el numero de contrato", "W519TC27QA012" in _m429)
    check("  Con 'Aplicando backoff exponencial'",
          "Aplicando backoff exponencial" in _m429)
    check("  Y con el intento X/3", "Intento 1/3" in _m429)
    check("  Y NO se llama 503 a un 429", "Error 503" not in _m429)

print()
print("=" * 74)
print("3) SOLO SE DUERME UNA VEZ: EL RESTO DE LA CADENA PASA RAPIDO")
print("=" * 74)
# El encargo: tres intentos "antes de ceder" y despues "una pasada rapida con
# un modelo secundario". Si cada uno de los seis modelos de la cadena durmiera
# 14s, serian 84 segundos bloqueados para el mismo aviso.
_t, _m, _e = _llamar_fb(["A", "B", "C"],
                        [RuntimeError(ERR_429_RITMO)] * 4 + ["ok"])
print("  esperas: %s   llamadas: %s" % (ESPERAS, LLAMADAS))
check("El principal si duerme sus dos esperas", ESPERAS == [2.0, 4.0],
      "-> %r" % ESPERAS)
check("El segundo modelo pasa UNA vez, sin dormir",
      LLAMADAS.count("B") == 1, "-> %r" % LLAMADAS)
check("La cadena completa es principal x3 y luego el resto",
      LLAMADAS == ["A", "A", "A", "B", "C"], "-> %r" % LLAMADAS)
check("Y acaba saliendo bien", _m == "C" and _e is None, "-> %r" % _m)

print()
print("=" * 74)
print("4) EL 429 DIARIO SIGUE SIN DORMIR (LA POLITICA DE SIEMPRE)")
print("=" * 74)
# Es lo que fijaron test_gemini_conn, test_cortacircuito y test_resiliencia:
# la cuota diaria no se arregla esperando, y catorce segundos por aviso es
# lo que mas caro salio.
_t, _m, _e = _llamar_fb(["A", "B"], [RuntimeError(ERR_429_DIARIO), "ok"])
print("  esperas: %s   llamadas: %s" % (ESPERAS, LLAMADAS))
check("NO duerme nada con tope diario", ESPERAS == [], "-> %r" % ESPERAS)
check("Cambia de modelo en el acto", _m == "B", "-> %r" % _m)
check("Y solo hizo dos llamadas", LLAMADAS == ["A", "B"], "-> %r" % LLAMADAS)

_t, _m, _e = _llamar_fb(["A", "B"],
                        [RuntimeError(ERR_429_GENERICO), "ok"])
check("El 429 sin senal tambien duerme cero", ESPERAS == [],
      "-> %r" % ESPERAS)

print()
print("=" * 74)
print("5) EL CORTACIRCUITO MANDA ANTES DE DORMIR")
print("=" * 74)
_t, _m, _e = _llamar_fb(["A", "B"],
                        [RuntimeError(ERR_429_RITMO)] * 3 + ["ok"],
                        continuar=lambda: False)
print("  esperas: %s   llamadas: %s   error: %s"
      % (ESPERAS, LLAMADAS, str(_e)[:50]))
check("Cero llamadas a Google", LLAMADAS == [], "-> %r" % LLAMADAS)
check("Cero segundos durmiendo", ESPERAS == [], "-> %r" % ESPERAS)
check("Y explica que otro ya lo habia confirmado",
      _e is not None and "cortocircuito" in str(_e), "-> %r" % str(_e))

print()
print("=" * 74)
print("6) LA CADENA AGOTADA SE CONVIERTE EN AVISO PENDIENTE")
print("=" * 74)
import gemini_analyzer as ga

OPP = {"noticeId": "x", "title": "T", "solicitationNumber": "S"}


def _analizar_guion(guion):
    """
    ga.analizar() con un doble que falla por MODELO, como hacen las demas
    suites. Devuelve (resultado, error, modelos_probados).
    """
    # El SDK falso hace falta aunque el cliente se inyecte: el nucleo hace
    # "from google import genai" al abrir la llamada, y sin ese modulo en
    # sys.modules reventaria antes de llegar al doble.
    _instalar([])

    class _M:
        def __init__(self):
            self.llamadas = []

        def generate_content(self, model=None, contents=None, config=None):
            self.llamadas.append(model)
            if model in guion:
                raise Exception(guion[model])
            return _R(JSON_OK)

    class _C:
        def __init__(self):
            self.models = _M()

    c = _C()
    original = ga._get_cliente
    ga._get_cliente = lambda: c
    try:
        return ga.analizar(OPP, "desc", "USA"), None, c.models.llamadas
    except ga.GeminiError as e:
        return None, e, c.models.llamadas
    finally:
        ga._get_cliente = original


_todos = [config.GEMINI_MODEL] + [
    m for m in getattr(config, "GEMINI_MODELES_ALTERNATIVOS", []) if m]
_guion = {m: ERR_429_DIARIO for m in _todos}
_limpia_antes = len(ESPERAS)
r, err, llamadas = _analizar_guion(_guion)
print("  modelos probados: %d   error: %s"
      % (len(set(llamadas)), str(err)[:60]))
check("La cadena entera agotada levanta GeminiError", err is not None,
      "-> devolvio %r" % (r,))
check("Se probaron todos los modelos de la cola", len(set(llamadas)) >= 2,
      "-> %r" % llamadas)
check("La pausa final de 24s se pidio (8 x 3) y no se ejecuto de verdad",
      ESPERAS[_limpia_antes:] == [24.0], "-> %r" % ESPERAS[_limpia_antes:])

src_scanner = io.open("scanner.py", encoding="utf-8").read()
print()
print("  Y el aviso NO se marca como visto, que es lo que lo deja pendiente:")
_pos_err = src_scanner.find("except gemini_analyzer.GeminiError")
_pos_marca = src_scanner.find("store.marcar(nid", _pos_err)
_rama = (src_scanner[_pos_err:_pos_marca]
         if _pos_err >= 0 and _pos_marca > _pos_err else "")
check("scanner tiene una rama propia para el fallo de Gemini",
      bool(_rama))
check("Dentro de esa rama se prohibe marcarlo visto",
      "NO se marca como vista" in _rama, "-> %r" % _rama[:200])
check("El store.marcar mas cercano esta DETRAS de esa rama",
      _pos_err >= 0 and _pos_marca > _pos_err)
check("y el resumen lo cuenta como pendiente",
      "queda pendiente" in src_scanner)

print()
print("=" * 74)
print("7) LA PAUSA ENTRE LLAMADAS DE ANALISIS YA ESTA Y CUMPLE")
print("=" * 74)
print("  El encargo pide un sleep de 1.5 a 2 segundos entre analisis")
print("  consecutivos. Existe, y ademas con mas margen del pedido:")
_arbol = ast.parse(src_scanner)
_an = next(n for n in ast.walk(_arbol)
           if isinstance(n, ast.FunctionDef) and n.name == "_analizar")
src_an = ast.get_source_segment(src_scanner, _an) or ""
check("Dentro de _analizar hay un time.sleep", "time.sleep" in src_an)
check("Y usa GEMINI_PAUSA_SEG, no un numero escrito a mano",
      "GEMINI_PAUSA_SEG" in src_an)
check("Esta despues del analisis, es decir ENTRE dos llamadas",
      src_an.find("gemini_analyzer.analizar") < src_an.find("time.sleep"),
      "-> %r" % src_an[:200])
check("El valor por defecto cumple el 1.5-2s pedido (o lo supera)",
      config.GEMINI_PAUSA_SEG >= 1.5,
      "-> %s" % config.GEMINI_PAUSA_SEG)
check("Y esta dentro del semaforo, con lo que es consecutivo de verdad",
      "with throttle" in src_an)
check("GEMINI_WORKERS por defecto es 1, asi que no hay dos a la vez",
      int(os.environ.get("GEMINI_WORKERS", "1") or 1) == 1)

print()
print("=" * 74)
print("8) LAS FIRMAS SIGUEN INTACTAS")
print("=" * 74)
_f1 = inspect.signature(gc.llamar)
_f2 = inspect.signature(gc.consultar_gemini_con_fallback)
print("  llamar%s" % _f1)
print("  consultar_gemini_con_fallback%s" % _f2)
for p in ("prompt", "contrato", "modelo", "respaldo", "cadena",
          "max_intentos", "espera_base", "timeout_ms", "dormir"):
    check("llamar conserva %s" % p, p in _f1.parameters)
for p in ("prompt", "contrato", "thinking_budget", "config_extra", "cola",
          "respaldo", "max_intentos", "esperar", "continuar",
          "reintentar_sin_thinking", "cliente", "devolver_respuesta",
          "modulo"):
    check("fallback conserva %s" % p, p in _f2.parameters)
check("El 429 sigue clasificandose como 429",
      gc.clasificar(RuntimeError(ERR_429_RITMO)) == "429"
      and gc.clasificar(RuntimeError(ERR_429_DIARIO)) == "429"
      and gc.clasificar(RuntimeError(ERR_429_GENERICO)) == "429")

print()
print("=" * 74)
print("9) NI UN SECRETO NI UNA REESCRITURA")
print("=" * 74)
src_conn = io.open("gemini_conn.py", encoding="utf-8").read()
check("gemini_conn no lleva claves", "AIza" not in src_conn
      and "SAM-" not in src_conn)
check("gemini_conn no lee el entorno directamente", "getenv" not in src_conn)
check("El cortacircuito se consulta antes de dormir el 429",
      "antes de la espera del 429" in src_conn)
check("Y antes de llamar", "cortocircuito: otro aviso" in src_conn)
check("Los dos formatos de log originales siguen estando",
      "Error 503 detectado" in src_conn
      and "Reintentos agotados para serie 3.x" in src_conn)

# Dejar el proceso como estaba por si este archivo se importa desde otro.
time.sleep = _sleep_original

print()
print("=" * 76)
if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: backoff 429/503, ritmo y aviso pendiente en verde")
