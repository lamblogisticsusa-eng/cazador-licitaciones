"""
test_cortacircuito.py - Un 429 no hace dormir a todo el lote.

LO QUE SE VIO EN EL ESCANEO REAL DEL 29-sep-2026, 02:34

    429 de cuota. Pausa 24s.
    02:34:26  Gemini fallo en d1456ba2: se agoto la cuota (429)
    429 de cuota. Pausa 24s.
    02:34:51  Gemini fallo en 45f7df40: se agoto la cuota (429)

Dos ramas descubriendo lo MISMO. La primera duerme 24s por si la cuota
vuelve, y la otra, que no tiene ni idea, duerme sus otros 24s para
descubrir lo que ya se sabia. El escaneo se alarga 48 segundos sin aportar
nada.

Y hay algo peor que el reloj: si el 429 es por minuto, reintentar cada 24s
desde varias ramas a la vez es justo lo que mantiene el rate limit arriba.
El reintento sincronizado empeora el problema que intenta resolver.

LO QUE SE COMPRUEBA
  - el callback se consulta antes de CADA intento, no despues de un 429
  - cuando otro ya encendio el cortacircuitos, no se llama a Google
  - cuando otro ya encendio el cortacircuitos, NO se duerme la espera del
    429, que es la parte que costaba 24s por rama
  - sin el callback, analizar() sigue funcionando igual (no se rompio la
    firma que usaban las pruebas y el resto del codigo)
  - el codigo de dormir del 429 esta realmente detrás de la comprobacion
"""
import os
import sys
import tempfile
import time

DB = os.path.join(tempfile.gettempdir(), "kyo_test_cc.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["DRY_RUN"] = "1"
os.environ["SAM_API_KEY"] = "x"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:FAKE"
os.environ["TELEGRAM_CHAT_ID"] = "1"
os.environ["GEMINI_PAUSA_SEG"] = "2"      # el 429 dormiria 6s
os.environ["GEMINI_REINTENTOS_429"] = "1"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


import ast
import inspect
import io

import gemini_analyzer as ga

ERR_429 = "429 RESOURCE_EXHAUSTED. {'error': {'code': 429}}"
ERR_503 = "503 UNAVAILABLE. {'error': {'code': 503, 'status': 'UNAVAILABLE'}}"
JSON_OK = '{"viable": false, "motivo_descarte": "prueba"}'
OPP = {"noticeId": "x", "title": "T", "solicitationNumber": "S"}


class _R:
    def __init__(self, t):
        self.text = t


class _M:
    """Modelos falsos que ademas CUENTAN las llamadas y el tiempo."""

    def __init__(self, guion):
        self.guion = guion
        self.usados = []
        self.llamadas = 0

    def generate_content(self, model=None, contents=None, config=None):
        self.llamadas += 1
        self.usados.append(model)
        if model in self.guion:
            raise Exception(self.guion[model])
        return _R(JSON_OK)


class _C:
    def __init__(self, guion):
        self.models = _M(guion)


def _correr(guion, continuar=None):
    cliente = _C(guion)
    original = ga._get_cliente
    ga._get_cliente = lambda: cliente
    t0 = time.time()
    try:
        r = ga.analizar(OPP, "desc", "USA", continuar=continuar)
        return r, None, cliente.models.llamadas, time.time() - t0
    except ga.GeminiError as e:
        return None, e, cliente.models.llamadas, time.time() - t0
    finally:
        ga._get_cliente = original


print("=" * 70)
print("1) LA FIRMA SIGUE SIENDO COMPATIBLE")
print("=" * 70)
firma = inspect.signature(ga.analizar)
print(f"  {firma}")
check("Sigue aceptando (opp, descripcion, lugar)",
      all(p in firma.parameters for p in ("opp", "descripcion", "lugar")))
check("Y ahora admite 'continuar'", "continuar" in firma.parameters)
check("'continuar' es opcional", firma.parameters["continuar"].default is None)
r, err, n, dt = _correr({})
check("Sin el callback funciona igual que antes",
      isinstance(r, dict) and err is None, f"-> {err}")

print()
print("=" * 70)
print("2) CUANDO OTRO YA PARO, NO SE LLAMA A GOOGLE")
print("=" * 70)
# Situacion: otro hilo ya encendio el cortacircuitos.
r, err, llamadas, dt = _correr({}, continuar=lambda: False)
print(f"  llamadas a Google: {llamadas}   error: {str(err)[:60]!r}")
check("Cero llamadas a Google", llamadas == 0, f"-> {llamadas}")
check("Falla sin demora", dt < 0.5, f"-> {dt:.2f}s")
check("Y lo explica", err is not None and "429" in str(err))
check("Dice que no se pierde nada",
      err is not None and "no se pierde" in str(err))

# Y lo contrario: mientras el cortacircuitos este apagado, sigue trabajando.
estado = {"parado": False}
r, err, llamadas, dt = _correr({}, continuar=lambda: not estado["parado"])
check("Con el cortacircuitos apagado, llama a Google",
      llamadas >= 1 and err is None, f"-> {llamadas} llamadas, {err}")

print()
print("=" * 70)
print("3) EL 429 YA NO DUERME SI OTRO ESTA ESPERANDO")
print("=" * 70)
# Con el 429 y sin cortacircuitos: una pausa de GEMINI_PAUSA_SEG*3 = 6s.
r, err, llamadas, dt = _correr({ga.config.GEMINI_MODEL: ERR_429})
print(f"  sin cortacircuitos: {llamadas} llamadas, {dt:.1f}s "
      f"(una espera de {ga.config.GEMINI_PAUSA_SEG * 3:.0f}s)")
check("Sin cortacircuito SI duerme la pausa", dt >= 5.0, f"-> {dt:.1f}s")

# Con el cortacircuitos ya encendido: ni una llamada, ni una espera.
estado = {"parado": True}
r, err, llamadas, dt = _correr(
    {ga.config.GEMINI_MODEL: ERR_429}, continuar=lambda: not estado["parado"])
print(f"  con cortacircuito: {llamadas} llamadas, {dt:.2f}s")
check("Con cortacircuito NO duerme", dt < 0.5, f"-> {dt:.1f}s")
check("Y no llega a llamar a Google", llamadas == 0, f"-> {llamadas}")
ahorro = 6.0
print(f"  ahorro por rama: {ahorro:.0f}s. Con 2 ramas son {ahorro * 2:.0f}s,")
print(f"  y con 12 barridos al dia, {ahorro * 12 / 60:.1f} minutos.")

print()
print("=" * 70)
print("4) EL CODIGO: LA COMPROBACION ESTA ANTES DE DORMIR")
print("=" * 70)
# No basta con que funcione: hay que comprobar que la comprobacion este
# realmente antes de la espera, y no despues.
_arbol = ast.parse(io.open(ga.__file__, encoding="utf-8").read())
_fn = next(n for n in _arbol.body
           if isinstance(n, ast.FunctionDef) and n.name == "analizar")
_cuerpo = ast.unparse(_fn)
check("Prueba 'continuar' dentro de analizar()", "continuar()" in _cuerpo)

i_cont = _cuerpo.find("continuar()")
i_sleep = _cuerpo.find("time.sleep")
i_call = _cuerpo.find("generate_content")
check("La comprobacion va ANTES de la llamada a Google",
      i_cont < i_call, f"-> continua en {i_cont}, llama en {i_call}")
check("Y ANTES de la espera del 429",
      i_cont < i_sleep, f"-> continua en {i_cont}, duerme en {i_sleep}")
# Y hay una segunda comprobacion justo antes de la espera del 429.
check("Hay una segunda comprobacion antes de la espera del 429",
      _cuerpo.count("continuar()") >= 2,
      f"-> {_cuerpo.count('continuar()')} comprobaciones")
print("  Con una sola comprobacion, el primer 429 todavia dormiria.")

print()
print("=" * 70)
print("5) EL ESCANER ENCIENDE EL CORTACIRCUITOS")
print("=" * 70)
import scanner

_c = io.open(scanner.__file__, encoding="utf-8").read()
check("El escaner crea un threading.Event", "threading.Event()" in _c)
check("Y lo enciende en el 429", "sin_cuota.set()" in _c)
check("Y se lo pasa a analizar()", "continuar=_continuar" in _c)
check("El evento se consulta", "sin_cuota.is_set()" in _c)
print("  Sin el .set() en el 429 el evento nunca se enciende y no sirve de nada.")

# Y que el aviso al usuario lo diga, para que no piense que se perdio trabajo.
check("El aviso al usuario menciona que paro las analisis",
      "paro las" in _c, "-> no explica que fue decision suya")

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
print("RESULTADO: un 429 para el lote entero, al instante")
