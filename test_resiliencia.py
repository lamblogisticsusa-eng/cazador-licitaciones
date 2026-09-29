"""
test_resiliencia.py - Kyomoto aguanta que Google este saturado.

Reproduce el 503 que vio el usuario y comprueba que:
  - cambia de modelo automaticamente en vez de rendirse
  - no te manda a "copia tu clave" cuando la clave esta bien
  - si todos estan saturados, lo dice claro y no pierde el aviso
"""
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_res.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["DRY_RUN"] = "1"
os.environ["SAM_API_KEY"] = "SAM-de-prueba"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:FAKE"
os.environ["TELEGRAM_CHAT_ID"] = "1"
os.environ["GEMINI_ESPERA_503"] = "0.1"
os.environ["GEMINI_PAUSA_SEG"] = "0.1"

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


import gemini_analyzer as ga

JSON_OK = '{"viable": false, "motivo_descarte": "prueba", "producto": "x"}'


class _R:
    def __init__(self, t):
        self.text = t


class _M:
    def __init__(self, guion):
        self.guion = guion
        self.usados = []

    def generate_content(self, model=None, contents=None, config=None):
        self.usados.append(model)
        fallo = self.guion.get(model)
        if fallo:
            raise Exception(fallo)
        return _R(JSON_OK)


class _C:
    def __init__(self, guion):
        self.models = _M(guion)


# El 429 real de Google, con el detalle que solo aparece en la respuesta
# completa. El limite del plan gratis son 20 llamadas al dia, y el bot debe
# decir eso en vez de un "se agoto la cuota" que no explica nada.
ERR_429_REAL = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
    "'You exceeded your current quota, please check your plan and billing "
    "details. * Quota exceeded for metric: generativelanguage.googleapis.com/"
    "generate_content_free_tier_requests, limit: 20, model: gemini-3.8-flash. "
    "Please retry in 56.8s.', 'status': 'RESOURCE_EXHAUSTED'}}"
)


def _analizar(guion):
    """
    Devuelve (resultado_o_None, error_o_None, modelos_usados).
    Devolver la lista por separado es importante: si analizar() lanza, el
    desempaquetado no ocurre y 'usados' se quedaria con el valor del caso
    anterior, haciendo creer que se probaron mas modelos de los reales.
    """
    cliente = _C(guion)
    original = ga._get_cliente
    ga._get_cliente = lambda: cliente
    o = {"noticeId": "x", "title": "T", "solicitationNumber": "S"}
    try:
        r = ga.analizar(o, "desc", "USA")
        return r, None, cliente.models.usados
    except ga.GeminiError as e:
        return None, e, cliente.models.usados
    finally:
        ga._get_cliente = original


ERR_503 = (
    "503 UNAVAILABLE. {'error': {'code': 503, 'message': "
    "'This model is currently experiencing high demand.', "
    "'status': 'UNAVAILABLE'}}"
)
ERR_429 = "429 RESOURCE_EXHAUSTED. {'error': {'code': 429}}"
ERR_401 = "401 UNAUTHENTICATED. {'error': {'code': 401}}"
# Medido el 28-sep-2026: gemini-3.5-flash-lite contestaba "OK" al smoke test
# pero devolvia esto con el prompt de verdad.
ERR_400 = "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'Request contains an invalid argument.', 'status': 'INVALID_ARGUMENT'}}"

principal = ga.config.GEMINI_MODEL
alternos = ga.config.GEMINI_MODELES_ALTERNATIVOS

print("=" * 70)
print("1) EL MODELO DE VERDAD ESTA SATURADO: KYOMOTO CAMBIA")
print("=" * 70)
print(f"  Principal   {principal}")
print(f"  Alternos    {', '.join(alternos)}")
check("Hay al menos dos alternos configurados", len(alternos) >= 2)

r, _err, usados = _analizar({principal: ERR_503})
print(f"  Probados: {' -> '.join(usados)}")
check("Empieza por el configurado", usados[0] == principal)
check("Cambia a un alterno tras el 503", len(usados) > 1, f"-> {usados}")
check("Y termina con exito", isinstance(r, dict) and r.get("notice_id") == "x")
print()

print("=" * 70)
print("2) VARIOS SATURADOS: SIGUE RECORRIENDO LA LISTA")
print("=" * 70)
r, _err, usados = _analizar({m: ERR_503 for m in [principal, alternos[0]]})
print(f"  Probados: {' -> '.join(usados)}")
check("Prueba el segundo alterno", len(set(usados)) >= 3, f"-> {usados}")
check("Y si se recupera", isinstance(r, dict))
print()

todos = [principal] + alternos
r, err, usados = _analizar({m: ERR_503 for m in todos})
msg = str(err or "")
check("Lanza un error cuando todos estan saturados", err is not None,
      f"-> devolvio {r}")
print(f"  Probados: {len(set(usados))} modelos distintos")
if err is not None:
    check("Dice que es transitoria", "transitoria" in msg.lower()
          or "temporal" in msg.lower())
    check("Aclara que NO es tu clave", "NO es tu clave" in msg)
    check("Dice que no se pierde nada", "no se pierde" in msg.lower())
    check("Nombra los modelos que probo", all(m in msg for m in todos),
          f"-> faltan {[m for m in todos if m not in msg]}")
    check("No dice el error generico de intentos",
          "Gemini fallo tras" not in msg)
print()
print()
print("=" * 70)
print("4) 429 Y 401 NO CAMBIAN DE MODELO (son otro problema)")
print("=" * 70)
r, err, usados = _analizar({m: ERR_429 for m in todos})
msg = str(err or "")
check("Con 429 lanza un error", err is not None, f"-> devolvio {r}")
if err is not None:
    check("Dice que la clave SI funciona", "clave SI funciona" in msg)
    # CAMBIADO el 29-sep-2026. Este test afirmaba que la cuota era de la
    # cuenta, asi que cambiar de modelo no servia de nada. Se midio lo
    # contrario preguntandole a los 6 modelos a la vez:
    #
    #     gemini-3.5-flash-lite   DISPONIBLE
    #     gemini-3.8-flash        SIN CUOTA
    #     gemini-flash-latest     SIN CUOTA
    #
    # Si el tope fuera de la cuenta, cuando uno se agota se agotan todos. El
    # tope es POR MODELO, asi que el 429 tiene que avanzar por la lista igual
    # que el 503 y el 404.
    check("Con el 429 avanza a otros modelos (el tope es por modelo)",
          len(set(usados)) >= 3, f"-> {usados}")
    check("Y el error nombra cuales se quedaron sin cuota",
          all(m in msg for m in set(usados)),
          f"-> faltan {[m for m in set(usados) if m not in msg]}")

r, err, usados = _analizar({principal: ERR_401})
check("Con 401 lanza un error", err is not None)
if err is not None:
    check("Y no prueba otros modelos", len(set(usados)) == 1, f"-> {usados}")
print()

print("=" * 70)
print("5) UN 400 NO SE REINTENTA: SE CAMBIA DE MODELO")
print("=" * 70)
# El 400 no es transitorio, pero Suele ser una opcion de la config que se
# puede apagar. Medido el 29-sep: gemini-3.5-flash-lite rechazaba
# thinking_budget=0 con 400 INVALID_ARGUMENT, y era el unico modelo con
# cuota libre ese dia. Perderlo entero por un campo apagable era tirar un
# modelo por la ventana, asi que ahora reintenta el MISMO modelo una vez sin
# ese campo.
r, _err, usados = _analizar({m: ERR_400 for m in (principal, alternos[0])})
print(f"  Probados: {' -> '.join(usados)}")
check("Ante un 400 reintenta el mismo modelo una vez",
      usados[0] == usados[1] == principal, f"-> {usados}")
check("Y tras ese reintento cambia de modelo",
      len(set(usados)) >= 2, f"-> {usados}")
check("Y sale con exito", isinstance(r, dict))

r, err, usados = _analizar({m: ERR_400 for m in todos})
msg = str(err or "")
check("Si todos dan 400 lanza un error", err is not None, f"-> devolvio {r}")
if err is not None:
    check("Dice que es 400, no saturacion", "400" in msg)
    check("Aclara que NO es la clave", "no es tu clave" in msg.lower())
    check("Dice que no es un problema de cuota", "cuota" in msg.lower())
    check("No se llama a si mismo 'saturado'", "saturados" not in msg.lower())
    # Con el 400 hay UN reintento por modelo (para apagar thinking_budget), asi
    # que cada uno puede aparecer dos veces. Lo que no puede es tres.
    from collections import Counter
    _c = Counter(usados)
    check("Cada modelo se prueba como mucho 2 veces",
          all(v <= 2 for v in _c.values()), f"-> {dict(_c)}")
print()

print()
print("=" * 70)
print("5b) EL 429 DICE EL LIMITE REAL, NO UN 'AGOTADO' GENERICO")
print("=" * 70)
# El plan gratis son 20 llamadas al dia, segun dice la respuesta de Google.
# Kyomoto debe repetir ese numero, porque si no el usuario no entiende por que
# no le llega nada y no sabe que hacer al respecto.
r, err, usados = _analizar({m: ERR_429_REAL for m in todos})
msg = str(err or "")
check("Lanza un error con el 429 real", err is not None)
if err is not None:
    print(f"  Mensaje:\n")
    for linea in msg.splitlines():
        print(f"    {linea}")
    check("Nombra el limite de 20", "20" in msg, "-> no dice cuantas son")
    check("Dice que hay que activar facturacion",
          "facturacion" in msg.lower() or "Billing" in msg)
    check("Y da una alternativa sin pagar",
          "no quieres pagar" in msg)
    # Acepta las dos formas de decirlo: "no se pierde" y "nada se pierde".
    _plano = msg.lower()
    check("Aclara que no se pierde trabajo",
          "no se pierde" in _plano or "nada se pierde" in _plano)
    check("No manda a tocar la clave", "copiala" not in msg.lower())
print()

print("=" * 70)
print("6) EL SMOKE TEST USA LA MISMA CONFIG QUE EL ESCANEO REAL")
print("=" * 70)
import ast as _ast
import io as _io

_f = _io.open(ga.__file__, encoding="utf-8").read()
_arbol = _ast.parse(_f)
_fn = next(n for n in _arbol.body
           if isinstance(n, _ast.FunctionDef) and n.name == "verificar_api")
# Solo el CODIGO, sin el docstring: el docstring menciona justamente el dict
# literal que se prohibe, para explicar el bug. Buscarlo ahi daria un falso
# positivo.
_llamadas = [
    _ast.unparse(n) for n in _ast.walk(_fn)
    if isinstance(n, _ast.Call)
    # Es cliente.models.generate_content: una Attribute, no un Name suelto.
    and getattr(n.func, "attr", "") == "generate_content"
]
check("verificar_api llama a _construir_config()",
      any("_construir_config()" in c for c in _llamadas),
      f"-> generate_content recibe: {_llamadas}")
check("Y no manda un config literal",
      not any('"max_output_tokens"' in c for c in _llamadas),
      f"-> generate_content recibe: {_llamadas}")
check("Y el prompt exige JSON, como el escaneo real",
      any("JSON" in _ast.unparse(n) for n in _ast.walk(_fn)
          if isinstance(n, _ast.Constant) and isinstance(n.value, str)))
print()

print("=" * 70)
print("5) EL AUTODIAGNOSTICO DA EL CONSEJO CORRECTO")
print("=" * 70)
import asyncio

import main


class _M2:
    def __init__(self):
        self.editado = ""

    async def reply_text(self, t, **k):
        return self

    async def edit_text(self, t, **k):
        self.editado = t
        return self


def _sin_acentos(s):
    return (s.replace("\u00ed", "i").replace("\u00e1", "a").replace("\u00f3", "o")
             .replace("\u00e9", "e").replace("\u00fa", "u").lower())


CASOS = (
    ("503", ERR_503,
     ["temporal", "tiene mucha demanda", "otros modelos"],
     ["copiala", "boton de copiar", "restriccion de ip"]),
    ("429", ERR_429,
     ["limite del plan", "reinicia sola", "no se pierde nada"],
     ["copiala", "boton de copiar"]),
    ("401", ERR_401,
     ["copiala", "boton de copiar", "restriccion de ip"],
     ["tiene mucha demanda"]),
)

for etiqueta, error, debe, no_debe in CASOS:
    original = main.gemini_analyzer.verificar_api
    main.gemini_analyzer.verificar_api = lambda d={"ok": False, "detalle": error}: d
    original_sam = main.sam_api.verificar_api
    main.sam_api.verificar_api = lambda: {"ok": False, "detalle": "SAM caido 429"}
    m = _M2()
    try:
        asyncio.run(main._selftest(m, None))
        cuerpo = m.editado
    except Exception as e:
        cuerpo = f"LANZO {type(e).__name__}: {e}"
    finally:
        main.gemini_analyzer.verificar_api = original
        main.sam_api.verificar_api = original_sam

    plano = _sin_acentos(cuerpo)
    faltan = [x for x in debe if _sin_acentos(x) not in plano]
    sobran = [x for x in no_debe if _sin_acentos(x) in plano]
    check(f"{etiqueta}: dice lo que toca", not faltan, f"-> faltan {faltan}")
    check(f"{etiqueta}: NO dice el consejo equivocado", not sobran, f"-> {sobran}")
    if etiqueta == "503":
        check("  y lo marca temporal, no como error", "\u23f3" in cuerpo)
    if etiqueta == "429":
        check("  y lo marca con aviso, no como error", "⚠" in cuerpo or "límite" in cuerpo)

for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

print()
print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: Kyomoto aguanta la saturacion de Google")
