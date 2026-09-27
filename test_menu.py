"""
test_menu.py - El menu de Kyomoto: teclado, textos y botones.
No toca la red y no gasta cuota de Gemini.
"""
import os
import sys

os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("SAM_API_KEY", "x")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:FAKE")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


import config
import kyo
import main
import menu

print("=" * 70)
print("1) EL TECLADO")
print("=" * 70)
for activo in (True, False):
    kb = menu.teclado(activo)
    botones = [b for fila in kb.inline_keyboard for b in fila]
    textos = " ".join(b.text for b in botones)
    check(f"Menu activo={activo} tiene 7 botones", len(botones) == 7, f"-> {len(botones)}")
    check("  Boton Buscar ahora", "Buscar ahora" in textos)
    check("  Boton Estado", "Estado" in textos)
    check("  Boton Cuota", "Cuota" in textos)
    check("  Boton Puntajes", "descarté" in textos or "descarté" in textos)
    check("  Boton Etiqueta", "Etiqueta" in textos)
    check("  Boton Autodiagnostico", "Autodiagn" in textos)
    pausa = "Pausar" if activo else "Reactivar"
    check(f"  Boton de pausa dice {pausa}", pausa in textos)
    check("  Todos los callbacks con prefijo k:",
          all(b.callback_data.startswith("k:") for b in botones))
    check("  La ultima fila es la de pausa", len(kb.inline_keyboard[-1]) == 1)
print()

print("=" * 70)
print("2) CADA BOTON TIENE UN HANDLER")
print("=" * 70)
import inspect
src = inspect.getsource(main.cmd_boton)
acciones = {
    b.callback_data.split(":")[-1]
    for fila in menu.teclado(True).inline_keyboard
    for b in fila
}
for accion in sorted(acciones):
    if accion in ("on", "off"):
       continue
    check(f"  k:{accion} se atiende en cmd_boton", f'"{accion}"' in src,
          f"-> no encontrado")
app = main._crear_app()
check("Hay un CallbackQueryHandler registrado",
      any(h.__class__.__name__ == "CallbackQueryHandler" for h in app.handlers[0]))
print()

print("=" * 70)
print("3) /start, /on y /off RESPONDEN CON EL MENU")
print("=" * 70)
src_start = inspect.getsource(main.cmd_start)
src_on = inspect.getsource(main.cmd_on)
src_off = inspect.getsource(main.cmd_off)
check("/start devuelve el teclado", "menu.teclado" in src_start)
check("/start usa el texto kawaii", "kyo.resumen_menu" in src_start)
check("/on reactivacion + menu", "_menu(update, ctx)" in src_on)
check("/off pausa + menu", "_menu(update, ctx)" in src_off)
check("El menu refleja el estado", 'ESTADO["activo"]' in inspect.getsource(main._menu))
print()

print("=" * 70)
print("4) LOS TEXTOS SON FIOS Y NO ROMPEN")
print("=" * 70)
# texto_estado / texto_cuota / texto_puntajes se pueden llamar sin red.
for nombre, fn in (("estado", main.texto_estado),
                   ("cuota", main.texto_cuota),
                   ("puntajes", main.texto_puntajes)):
    try:
        cuerpo = fn()
    except Exception as e:
        check(f"texto_{nombre}() no lanza", False, f"-> {type(e).__name__}: {e}")
        continue
    check(f"texto_{nombre}() devuelve texto", isinstance(cuerpo, str) and len(cuerpo) > 20)
    check(f"  texto_{nombre} cabe en 4096", len(cuerpo) <= 4096, f"-> {len(cuerpo)}")
    # El HTML de Telegram tiene que estar balanceado.
    for tag in ("b", "i", "code", "pre"):
        ab = cuerpo.count(f"<{tag}>")
        ce = cuerpo.count(f"</{tag}>")
        check(f"  <{tag}> balanceado en {nombre}", ab == ce, f"-> {ab} abren, {ce} cierran")
    try:
        import html as _h
        _h.escape(cuerpo)
        check(f"  texto_{nombre} es escapable", True)
    except Exception as e:
        check(f"  texto_{nombre} es escapable", False, f"-> {e}")
print()

print("=" * 70)
print("5) EL INTERVALO ES EL QUE PIDISTE")
print("=" * 70)
check("INTERVALO_HORAS = 2", config.INTERVALO_HORAS == 2, f"-> {config.INTERVALO_HORAS}")
check("El rango es 5k a 250k",
      config.MIN_USD == 5000 and config.TOPE_USD == 250000,
      f"-> {config.MIN_USD:,.0f} a {config.TOPE_USD:,.0f}")
saludo = kyo.resumen_menu(True)
check("El saludo menciona el rango", "250,000" in saludo and "5,000" in saludo)
check("El saludo menciona las 2 horas", "cada 2 h" in saludo, f"-> {saludo[-120:]}")
check("Y es kawaii", any(x in saludo for x in ("Kyon", "✨", "ᐢ..ᐢ", "amo")))
print()

print("=" * 70)
print("6) EL SELFEST NO CONFUNDE 429 CON 401")
print("=" * 70)
src_selftest = inspect.getsource(main._selftest)
check("Distingue el 429", "429" in src_selftest and "RESOURCE_EXHAUSTED" in src_selftest)
check("El 429 NO es un error, es la cuota", "no es un error" in src_selftest.lower())
check("El 401 si da pasos de arreglo", "401" in src_selftest or "apikey" in src_selftest)
check("Dice cuando se reinicia la cuota", "00:00" in src_selftest or "Pacifico" in src_selftest)
print()

print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: el menu de Kyomoto funciona")
