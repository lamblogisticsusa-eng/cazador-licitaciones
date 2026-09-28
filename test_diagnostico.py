"""
test_diagnostico.py - El autodiagnostico no puede reventar.

Bug encontrado en produccion: sam_api.verificar_api() lanzaba SamError en
vez de devolver {"ok": False}. Eso rompia /selftest y /estado justo cuando
hay que diagnosticar algo. El autodiagnostico que se cae no diagnostica.
"""
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_diag.db")
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

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


import gemini_analyzer
import main
import sam_api

print("=" * 70)
print("1) verificar_api NUNCA LANZA")
print("=" * 70)


def seguro(fn, nombre):
    try:
        r = fn()
        check(nombre, isinstance(r, dict), f"-> devolvio {type(r).__name__}")
        return r
    except Exception as e:
        check(nombre, False, f"-> LANZO {type(e).__name__}: {e}")
        return None


# Sin clave
_original = sam_api.config.SAM_API_KEY
sam_api.config.SAM_API_KEY = ""
r = seguro(sam_api.verificar_api, "Sin SAM_API_KEY devuelve dict")
check("  y dice que falta la clave", r and "no configurada" in r["detalle"])

# Con clave y throttled
sam_api.config.SAM_API_KEY = _original
sam_api._proximo_acceso = "2026-09-28T00:00:00Z"
r = seguro(sam_api.verificar_api, "Throttled devuelve dict")
check("  y dice hasta cuando", r and "2026-09-28" in r["detalle"], f"-> {r}")
check("  y marca throttled", r and r.get("throttled") is True)

# Con clave que da 429 de verdad
sam_api._proximo_acceso = None
r = seguro(sam_api.verificar_api, "Con API caida devuelve dict")
check("  y reporta el fallo sin reventar", r is not None and r["ok"] is False,
      f"-> {r}")
print()

print("=" * 70)
print("2) /estado Y /selftest NO SE ROMPEN CON LAS APIs CAIDAS")
print("=" * 70)
# texto_estado() ya lo usa quota.estado() y store.total_procesadas().
for nombre, fn in (
    ("texto_estado", main.texto_estado),
    ("texto_cuota", main.texto_cuota),
    ("texto_puntajes", main.texto_puntajes),
):
    try:
        cuerpo = fn()
        check(f"{nombre}() responde", isinstance(cuerpo, str) and len(cuerpo) > 20)
        check(f"  {nombre} cabe en 4096", len(cuerpo) <= 4096, f"-> {len(cuerpo)}")
    except Exception as e:
        check(f"{nombre}() responde", False, f"-> {type(e).__name__}: {e}")

# verificar_api de Gemini tambien debe ser segura
r = seguro(gemini_analyzer.verificar_api, "Gemini verificar_api devuelve dict")
print()

print("=" * 70)
print("3) EL SELFTEST DICE LA VERDAD")
print("=" * 70)
# _selftest corre en un hilo. Con las APIs caidas debe terminar y decir
# "no esta en orden", no reventar.
import asyncio


class _MensajeFalso:
    """Captura lo que el selftest intentaria enviar."""

    def __init__(self):
        self.editado = ""

    async def reply_text(self, texto, **k):
        return self

    async def edit_text(self, texto, **k):
        self.editado = texto
        return self


class _CtxFalso:
    pass


mensaje = _MensajeFalso()
try:
    asyncio.run(main._selftest(mensaje, _CtxFalso()))
    ok = True
except Exception as e:
    ok = False
    print(f"  LANZO: {type(e).__name__}: {e}")
check("El selftest termina sin excepcion", ok)
check("Y escribio un informe", len(mensaje.editado) > 50, f"-> {len(mensaje.editado)} chars")
if mensaje.editado:
    cuerpo = mensaje.editado
    check("Dice que SAM.gov no esta bien",
          "SAM.gov" in cuerpo and ("429" in cuerpo or "Tope" in cuerpo or "❌" in cuerpo))
    check("No dice 'todo en orden' si algo falla", "Todo en orden" not in cuerpo)
    # "Accion concreta" = dice QUE cambiar o QUE comando correr. No se buscan
    # palabras exactas porque el consejo correcto depende del fallo.
    acciones = (
        "SAM_API_KEY", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "sondear_modelos.py", "probar_clave.py", "PYTHON_VERSION",
    )
    check("Y dice que revisar o que comando correr",
          any(a in cuerpo for a in acciones),
          f"-> ninguna de {acciones} aparece")
    check("El consejo de SAM.gov menciona la variable",
          "SAM_API_KEY" in cuerpo or "API pública" in cuerpo)

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
print("RESULTADO: el diagnostico aguanta cuando todo esta mal")
