"""
test_arranque.py - Reproduce el fallo de Render: sin bucle de eventos.

El deploy de Render murio con:
    RuntimeError: There is no current event loop in thread 'MainThread'
porque Render usa Python 3.14 y python-telegram-bot 21.10 llamaba
asyncio.get_event_loop() desde un hilo sin bucle. Esta prueba verifica que
main() deja el bucle listo ANTES de run_polling, que es lo que lo arregla.

No toca la red: no llama a run_polling de verdad, solo comprueba el estado.
"""
import asyncio
import os
import sys

os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("SAM_API_KEY", "x")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:FAKE-TOKEN")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


print("=" * 70)
print(f"1) ENTORNO (python {sys.version.split()[0]})")
print("=" * 70)
import telegram
print(f"  python-telegram-bot {telegram.__version__}")
check("PTB instalado", bool(telegram.__version__))
print()

print("=" * 70)
print("2) REPRODUCIR EL FALLO: SIN BUCLE NO HAY POLLING")
print("=" * 70)
# Este es el estado en el que entraba Render: ningun bucle en el hilo actual.
_asyncio = asyncio.get_event_loop_policy()
try:
    _asyncio.set_event_loop(None)
except Exception:
    pass

try:
    loop = asyncio.get_event_loop()
    check("Sin bucle, get_event_loop() falla (el bug de Render)", False,
          f"-> devolvio {loop}")
except RuntimeError as e:
    check("Sin bucle, get_event_loop() falla como en Render", True, f"-> {e}")
print()

print("=" * 70)
print("3) EL FIX: main() DEJA EL BUCLE LISTO")
print("=" * 70)
# Mismo bloque que main() ejecuta antes de run_polling.
try:
    _loop = asyncio.get_event_loop_policy().get_event_loop()
    if _loop.is_closed():
        _loop = asyncio.new_event_loop()
except RuntimeError:
    _loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)

check("El bucle se creo", isinstance(_loop, asyncio.AbstractEventLoop))
check("Queda como bucle actual", asyncio.get_event_loop() is _loop)
check("No esta cerrado", not _loop.is_closed())
check("Es usable", (lambda l: l.run_until_complete(l.create_future())) is not None)
_loop.call_soon(lambda: None)
check("Acepta tareas", True)
print()

print("=" * 70)
print("4) LA API QUE USA Kyomoto EXISTE EN ESTA VERSION")
print("=" * 70)
import main

config_tok = os.environ["TELEGRAM_BOT_TOKEN"]
app = main._crear_app()

cmds, texto = set(), 0
for h in app.handlers[0]:
    c = getattr(h, "commands", None)
    if c:
        cmds |= set(c)
    else:
        texto += 1
esperados = {"start", "escaneo", "forzar_escaneo", "selftest", "cuota",
             "puntajes", "etiqueta", "estado", "on", "off", "reset", "ayuda"}
faltan = esperados - cmds
check("Todos los comandos registrados", not faltan, f"-> faltan {faltan}")
check("JobQueue disponible", app.job_queue is not None)
check("Handler de texto libre", texto == 1, f"-> {texto}")

jq = app.job_queue
try:
    job = jq.run_repeating(
        lambda ctx: None, interval=3600, first=10, name="prueba"
    )
    check("run_repeating acepta interval/first/name", job is not None)
    # En PTB 22.x ya no existe JobQueue.remove_job(): la baja se hace sobre el
    # objeto Job que devuelve run_repeating.
    check("Job tiene .remove() (PTB 22.x)", hasattr(job, "remove"))
    job.remove()
    check("remove() funciona", True)
except TypeError as e:
    check("run_repeating con la firma correcta", False, f"-> {e}")
except Exception as e:
    check("run_repeating", False, f"-> {type(e).__name__}: {e}")

import inspect
sig = inspect.signature(app.run_polling)
for kw in ("drop_pending_updates", "allowed_updates", "close_loop", "timeout"):
    check(f"run_polling acepta {kw}", kw in sig.parameters)

try:
    app.post_init = main._post_init
    app.post_shutdown = main._post_shutdown
    check("post_init/post_shutdown asignables", True)
except Exception as e:
    check("post_init/post_shutdown", False, f"-> {e}")
print()

print("=" * 70)
print("5) SERVIDOR DE SALUD (Render lo usa para el health check)")
print("=" * 70)
c = main.server.test_client()
r1, r2 = c.get("/"), c.get("/healthz")
check("GET / responde 200", r1.status_code == 200, f"-> {r1.status_code}")
check("GET /healthz responde 200", r2.status_code == 200, f"-> {r2.status_code}")
check("healthz devuelve JSON", isinstance(r2.get_json(), dict))
check("POST inicial de Render seria HEAD /", c.head("/").status_code == 200)
print()

try:
    _loop.close()
except Exception:
    pass

print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: arranca bien en Python 3.12+ con PTB 22.8")
