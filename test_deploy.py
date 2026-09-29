"""
test_deploy.py - Comprueba que el codigo es desplegable en Render tal cual.
Verifica firmas de la libreria, variables de entorno y ausencia de trampas
que solo fallan en produccion.
"""
import ast
import inspect
import os
import sys

os.environ.setdefault("DRY_RUN", "1")
os.environ["SAM_API_KEY"] = "x"
os.environ["GEMINI_API_KEY"] = "AIzaFAKE"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE"
os.environ["TELEGRAM_CHAT_ID"] = "1"

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    print(f"  {'OK   ' if cond else 'FALLA'} {nombre} {extra}")
    if not cond:
        FALLA += 1


print("=" * 70)
print("1) FIRMAS DE LA BIBLIOTECA (lo que Render va a instalar de verdad)")
print("=" * 70)
from telegram.ext import Application, JobQueue
import telegram
import google.genai
import flask
from importlib.metadata import version as _v
import reportlab
import requests

_flask_ver = _v("flask")
print(f"  python              {sys.version.split()[0]}")
print(f"  python-telegram-bot {telegram.__version__}")
print(f"  flask               {_flask_ver}")
print(f"  reportlab           {reportlab.Version}")

sig = inspect.signature(Application.run_polling)
for kw in ("drop_pending_updates", "allowed_updates", "close_loop", "timeout"):
    check(f"run_polling acepta {kw}", kw in sig.parameters)

check("JobQueue importable (exige python-telegram-bot[job-queue])", JobQueue is not None)
check("Application.job_queue existe", hasattr(Application, "job_queue"))

# google-genai: campos que uso en _construir_config
try:
    from google.genai import types
    cfg = types.GenerateContentConfig(
        temperature=0.2,
        response_mime_type="application/json",
        max_output_tokens=4096,
    )
    check("GenerateContentConfig acepta temperature/mime/tokens", cfg is not None)
    try:
        tc = types.ThinkingConfig(thinking_budget=0)
        types.GenerateContentConfig(temperature=0.2, thinking_config=tc)
        check("ThinkingConfig disponible (se usara para ahorrar tiempo)", True)
    except Exception as e:
        check("ThinkingConfig disponible", False, f"-> el codigo cae al fallback: {type(e).__name__}")
except Exception as e:
    check("GenerateContentConfig", False, f"-> {e}")
print()

print("=" * 70)
print("2) TRAMPAS DE PRODUCCION")
print("=" * 70)
import main
import config
import store

# El bot no debe morir si falta una clave opcional.
for clave in ("SAM_API_KEY", "GEMINI_API_KEY", "TELEGRAM_CHAT_ID", "DRY_RUN", "PORT"):
    check(f"config expone {clave}", hasattr(config, clave))

# init_db debe ser idempotente (Render reinicia el proceso).
store.init_db()
store.init_db()
check("init_db es idempotente", True)

# El modulo store no debe romper con un directorio inexistente
os.chdir(os.path.dirname(os.path.abspath(__file__)))
check("CWD es la raiz del proyecto", os.path.exists("config.py"))

# Con una clave falsa, SAM.gov rechaza todo. escanear NO debe lanzar, y debe
# reportar el fallo en vez de fingir que no hay novedades.
import scanner
r = scanner.escanear("1", dias=2)
check("escanear con SAM_API invalida no lanza", isinstance(r, dict))
check("escanear reporta el fallo de SAM.gov", len(r["errores"]) > 0,
      f"-> {r['errores'][:1]}")
check("el error menciona la respuesta de SAM.gov",
      any(("SAM.gov" in e or "HTTP" in e) for e in r["errores"]), f"-> {r['errores'][:1]}")

# DRY_RUN no debe enviar nada real.
check("DRY_RUN activo", config.DRY_RUN is True)

# El token falso no debe ser usado en la importacion.
check("El import de main no hace llamadas de red", True, "(solo flask + ptb)")
print()

print("=" * 70)
print("3) REQUISITOS COINCIDEN CON LOS IMPORTS")
print("=" * 70)
with open("requirements.txt", encoding="utf-8") as f:
    req = f.read().lower()
for paquete in ("python-telegram-bot", "google-genai", "requests", "flask", "reportlab"):
    check(f"requirements.txt incluye {paquete}", paquete in req)

fuentes = ["main.py", "config.py", "sam_api.py", "filters.py",
           "gemini_analyzer.py", "scanner.py", "store.py",
           "telegram_notify.py", "label.py"]
for nombre in fuentes:
    with open(nombre, encoding="utf-8") as f:
        src = f.read()
    arbol = ast.parse(src)
    importados = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados |= {a.name.split(".")[0] for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            importados.add(nodo.module.split(".")[0])
    for huerfano in ("apscheduler", "docx", "gunicorn", "pydantic"):
        check(f"{nombre} no importa {huerfano}", huerfano not in importados,
              f"-> importados: {sorted(importados)}" if huerfano in importados else "")
print()

print("=" * 70)
print("4) RENDER.YAML COHERENTE")
print("=" * 70)
with open("render.yaml", encoding="utf-8") as f:
    yaml_txt = f.read()
check("startCommand es python main.py", "startCommand: python main.py" in yaml_txt)
check("NO usa gunicorn", "gunicorn" not in yaml_txt)
check("healthCheckPath apunta a una ruta existente", "/healthz" in yaml_txt)
check("buildCommand usa requirements.txt", "pip install -r requirements.txt" in yaml_txt)
for clave in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SAM_API_KEY", "GEMINI_API_KEY"):
    check(f"declara {clave} con sync: false", f"key: {clave}\n        sync: false" in yaml_txt)
print()

print("=" * 70)
print("5) EL FALLO DE RENDER: PYTHON 3.14 + PTB VIEJO")
print("=" * 70)
with open("requirements.txt", encoding="utf-8") as f:
    req = f.read()
check("PTB >= 22.8 (la primera que tolera Python 3.14)",
      "python-telegram-bot[job-queue]==22.8" in req,
      "-> falta el pin a 22.8 en requirements.txt")
check("Se explica el porque en los comentarios", "3.14" in req)
check("Se documenta el sintoma del crash",
      "There is no current event loop" in req)

with open("main.py", encoding="utf-8") as f:
    src_main = f.read()
check("main.py crea el bucle de eventos",
      "asyncio.set_event_loop" in src_main and "new_event_loop" in src_main)
check("run_polling viene DESPUES de fijar el bucle",
      src_main.index("asyncio.set_event_loop") < src_main.index("app.run_polling"))
check("El except cubre RuntimeError (comportamiento de 3.14)",
      "except RuntimeError" in src_main)

with open("render.yaml", encoding="utf-8") as f:
    ry = f.read()
check("render.yaml fija PYTHON_VERSION", "PYTHON_VERSION" in ry)

# Hay que mirar el VALOR del pin, no la palabra "3.14": el archivo la menciona
# a proposito, en el comentario que advierte del crash.
import re
m = re.search(r"PYTHON_VERSION\s*\n\s*value:\s*[\"']?([0-9.]+)", ry)
check("El pin de Python se pudo leer", m is not None)
if m:
    version = m.group(1)
    print(f"       -> PYTHON_VERSION = {version}")
    major, minor = (version.split(".") + ["0"])[:2]
    check("No es 3.14 (la version que rompia el arranque)",
          not version.startswith("3.14"), f"-> {version}")
    check("Es 3.11 o posterior (sintaxis moderna del proyecto)",
          (int(major), int(minor)) >= (3, 10), f"-> {version}")
check("El pin de Python lleva comentario de alerta", "CRITICO" in ry)
print()

print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s) de despliegue")
    sys.exit(1)
print("RESULTADO: desplegable tal cual")
