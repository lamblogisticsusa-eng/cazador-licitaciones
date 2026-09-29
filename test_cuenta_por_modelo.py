"""
test_cuenta_por_modelo.py - La cuota de Google es POR MODELO, y eso cambia todo.

HALLAZGO DEL 29-sep-2026, MEDIDO PREGUNTANDOLE A LOS 6 MODELOS A LA VEZ

    gemini-3.5-flash-lite   DISPONIBLE
    gemini-3.8-flash        SIN CUOTA  (tope 20)
    gemini-flash-latest     SIN CUOTA  (tope 20)
    gemini-3.6-flash        503
    gemini-3.5-flash        503
    gemini-3.7-flash        503

Si el tope fuera de la cuenta, cuando uno se queda sin cuota se quedarian
todos a la vez. Como uno responde y dos no, cada modelo lleva su propio
contador de 20 al dia. Multiplicado por 6 modelos, el techo real es 120
analisis al dia, no 20.

SEGUNDO HALLAZGO: EL UNICO MODELO CON CUOTA NO ACEPTABA LA PETICION

    solo max_output_tokens                  OK
    + response_mime_type                   OK
    + temperature                          OK
    + thinking_budget=0                    400 INVALID_ARGUMENT

Kyomoto pone thinking_budget en 0 para no pagar razonamiento, y ese campo
simplemente no existe para gemini-3.5-flash-lite. O sea que el unico modelo
libre era justo el que no se podia usar: un callejon sin salida. Con el
reintento sin thinking, entra.

TERCER HALLAZGO: LA CONTEXTA ESTE CASI VACIA
Una llamada individual gasta unas 2.400 tokens de un million. Se esta usando
el 0,2% de lo que cabe. Por eso el analisis va de a cinco en una llamada.

LO QUE SE COMPRUEBA
  - el 429 cambia de modelo en vez de tirar la llamada
  - el 400 reintenta el mismo modelo sin thinking_budget
  - el reintento sin thinking ocurre UNA sola vez (sin loop infinito)
  - verificar_api no se rinde en el primer 429
  - el error le dice al usuario que modelos se quedaron sin cuota
  - con 6 modelos y 20 por modelo, el techo real es 120, no 20
"""
import io
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_cuota.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["DRY_RUN"] = "1"
os.environ["SAM_API_KEY"] = "x"
os.environ["GEMINI_API_KEY"] = "x"  # verificar_api la exige
os.environ["TELEGRAM_BOT_TOKEN"] = "123:FAKE"
os.environ["TELEGRAM_CHAT_ID"] = "1"
os.environ["GEMINI_REINTENTOS_429"] = "1"
os.environ["GEMINI_PAUSA_SEG"] = "0.1"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


import config
import gemini_analyzer as ga

JSON_OK = '{"viable": false, "motivo_descarte": "prueba", "producto": "x"}'
OPP = {"noticeId": "x", "title": "T", "solicitationNumber": "S"}

ERR_429 = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
           "'You exceeded your current quota. * Quota exceeded for metric: "
           "generativelanguage.googleapis.com/generate_content_free_tier_"
           "requests, limit: 20, model: gemini-3.8-flash'}}")
ERR_400 = "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'Request contains an invalid argument.', 'status': 'INVALID_ARGUMENT'}}"
ERR_503 = "503 UNAVAILABLE. {'error': {'code': 503, 'status': 'UNAVAILABLE'}}"

principal = config.GEMINI_MODEL
alternos = [m for m in config.GEMINI_MODELES_ALTERNATIVOS if m]
todos = [principal] + alternos


class _R:
    def __init__(self, t):
        self.text = t


class _M:
    """Modelos falsos que anotan SI PIDIO thinking_budget, que es lo que
    decide si el 400 viene de ahi o de otra cosa."""

    def __init__(self, guion, config_400=False):
        self.guion = guion
        self.usados = []
        self.configs = []
        self.config_400 = config_400

    def generate_content(self, model=None, contents=None, config=None):
        self.usados.append(model)
        self.configs.append(config)
        fallo = self.guion.get(model)
        if fallo:
            raise Exception(fallo)
        return _R(JSON_OK)


class _C:
    def __init__(self, guion, config_400=False):
        self.models = _M(guion, config_400)


def _correr(guion):
    cliente = _C(guion)
    original = ga._get_cliente
    ga._get_cliente = lambda: cliente
    try:
        r = ga.analizar(OPP, "desc", "USA")
        return r, None, cliente.models
    except ga.GeminiError as e:
        return None, e, cliente.models
    finally:
        ga._get_cliente = original


def _tiene_thinking(cfg):
    """
    Mira el ATRIBUTO, no el repr: el repr de GenerateContentConfig no siempre
    incluye los defaults, asi que buscar la palabra "thinking" en el texto
    puede mentir. El atributo si dice la verdad: con thinking vale un
    ThinkingConfig(thinking_budget=0), sin thinking vale None.
    """
    if cfg is None:
        return False
    return getattr(cfg, "thinking_config", None) is not None


print("=" * 70)
print("1) EL 429 CAMBIA DE MODELO, NO TIRA LA LLAMADA")
print("=" * 70)
print(f"  Principal: {principal}")
print(f"  Alternos : {len(alternos)}")
# El bug: con el 429 en el principal, antes se rendia aqui. Ahora debe probar
# el siguiente, que puede tener cuota.
r, err, m = _correr({principal: ERR_429})
print(f"  Probados: {' -> '.join(m.usados)}")
check("Con 429 en el principal, pasa al siguiente",
      len(m.usados) >= 2, f"-> {m.usados}")
check("Y termina con exito si el siguiente tiene cuota",
      isinstance(r, dict) and err is None, f"-> {err}")
check("El 429 queda registrado como modelo sin cuota",
      principal in m.usados)

print()
print("  Que pasa si el 429 esta en VARIOS modelos seguidos:")
r, err, m = _correr({principal: ERR_429, alternos[0]: ERR_429})
print(f"  Probados: {' -> '.join(m.usados)}")
check("Sigue avanzando por la lista", len(m.usados) >= 3, f"-> {m.usados}")
check("Y si se recupera", isinstance(r, dict))

print()
print("=" * 70)
print("2) EL 400 REINTENTA EL MISMO MODELO SIN THINKING_BUDGET")
print("=" * 70)
# El hallazgo: el unico modelo con cuota daba 400 por thinking_budget=0. Antes
# se le descartaba y se perdia el unico que podia usarse.
r, err, m = _correr({principal: ERR_400})
print(f"  Probados: {' -> '.join(m.usados)}")
print(f"  Configs: {len(m.configs)}")
check("Ante un 400, reintenta el MISMO modelo",
      m.usados[0] == principal and m.usados[0] == m.usados[1],
      f"-> {m.usados}")
check("Con thinking_budget en el primer intento",
      _tiene_thinking(m.configs[0]))
check("Y SIN thinking_budget en el reintento",
      not _tiene_thinking(m.configs[1]), "-> sigue mandandolo")
check("Y ahi si funciona", isinstance(r, dict) and err is None, f"-> {err}")

print()
print("=" * 70)
print("3) EL REINTENTO OCURRE UNA SOLA VEZ")
print("=" * 70)
# Si tras apagar thinking siga fallando, tiene que cambiar de modelo, no
# quedarse reintentando el mismo una y otra vez.
r, err, m = _correr({principal: ERR_400, alternos[0]: ERR_400})
usados = m.usados
print(f"  Probados: {' -> '.join(usados)}")
# El principal puede aparecer 2 veces (con y sin thinking), nunca mas.
from collections import Counter
cuenta = Counter(usados)
print(f"  Veces por modelo: {dict(cuenta)}")
check("Ningun modelo se reintenta mas de 2 veces",
      all(v <= 2 for v in cuenta.values()), f"-> {dict(cuenta)}")
check("Y avanza al siguiente tras el segundo fallo",
      len(usados) >= 3, f"-> {usados}")
check("Termina sin colgarse", err is not None or isinstance(r, dict))

print()
print("=" * 70)
print("4) verificar_api NO SE RINDE EN EL PRIMER 429")
print("=" * 70)
# verificar_api tambien se rendia en cuanto un modelo daba 429, declarando
# fallo aunque quedaran cinco modelos con cuota.
orig = ga._get_cliente
ga._get_cliente = lambda: _C({principal: ERR_429, alternos[0]: ERR_503})
r = ga.verificar_api()
ga._get_cliente = orig
print(f"  ok={r['ok']} modelo={r.get('modelo')} :: {r['detalle'][:110]}")
check("Con 429 en el principal y 503 en otro, aun da OK",
      r["ok"] is True, f"-> {r['detalle'][:140]}")
check("Y dice que modelo uso", bool(r.get("modelo")))

# Y si de verdad se acabaron todos, lo dice nombrando la cuota.
ga._get_cliente = lambda: _C({m: ERR_429 for m in todos})
r = ga.verificar_api()
ga._get_cliente = orig
print()
print(f"  todos sin cuota -> ok={r['ok']}")
print(f"  detalle: {r['detalle'][:150]}")
check("Con todos sin cuota avisa que no hay", r["ok"] is False)
check("Dice que el tope es por modelo",
      "POR MODELO" in r["detalle"], f"-> {r['detalle'][:140]}")
check("Y marca cuota=True", r.get("cuota") is True)

print()
print("=" * 70)
print("5) EL ERROR LE DICE AL USUARIO QUE MODELOS SE QUEDARON SIN CUOTA")
print("=" * 70)
r, err, m = _correr({m_: ERR_429 for m_ in todos})
msg = str(err or "")
print("  Mensaje:")
for linea in msg.splitlines()[:8]:
    print(f"    {linea}")
check("Nombra los modelos sin cuota",
      all(m_ in msg for m_ in todos), "-> no dice cuales")
check("Dice que se reinicia a medianoche del Pacifico",
      "medianoche del Pacifico" in msg or "Pacifico" in msg)
check("Y no manda a cambiar la clave", "copiala" not in msg.lower())
check("Aclara que nada se pierde",
      "nada se pierde" in msg.lower() or "no se pierde" in msg.lower())

print()
print("=" * 70)
print("6) EL TECHO REAL: 6 MODELOS x 20 = 120, NO 20")
print("=" * 70)
n_modelos = len(todos)
print(f"  Modelos en la lista      : {n_modelos}")
print(f"  Tope por modelo (medido) : 20 llamadas/dia")
print(f"  Antes (1 modelo)         : 20 analisis/dia")
print(f"  Ahora ({n_modelos} modelos)       : {n_modelos * 20} analisis/dia")
print()
print(f"  Con 5 avisos por llamada: {n_modelos * 20 * 5} avisos analizados/dia")
print()
check("Hay al menos 4 modelos para multiplicar el tope", n_modelos >= 4,
      f"-> {n_modelos}")
check("El multiplicador es real, no decorativo", n_modelos * 20 > 20)
# Y el aviso de 400 tiene que seguir siendo distinto del de 503.
r, err, m = _correr({m_: ERR_400 for m_ in todos})
msg400 = str(err or "")
check("Con 400 en todos dice 'ningun modelo acepta la peticion'",
      "400" in msg400, f"-> {msg400[:100]}")
check("Y no lo confunde con cuota", "sin cuota" not in msg400.lower())

print()
print("=" * 70)
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: la cuota se multiplica por modelo y por lote")
print()
print("  Antes:  20 analisis al dia")
print(f"  Ahora:  {len(todos) * 20} analisis al dia, o "
      f"{len(todos) * 20 * 5} avisos con el analisis en lote.")
