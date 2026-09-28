"""
escaneo_real.py - Primera busqueda real de Kyomoto.

No usa simulaciones: SAM.gov y Gemini de verdad. DRY_RUN=1 para que no
intente mandar nada a Telegram sin token.

    $env:SAM_API_KEY="SAM-..."
    $env:GEMINI_API_KEY="..."
    python escaneo_real.py 10
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

faltan = [v for v in ("SAM_API_KEY", "GEMINI_API_KEY") if not os.environ.get(v)]
if faltan:
    print("Faltan: " + ", ".join(faltan))
    sys.exit(2)

os.environ.setdefault("GEMINI_MODEL", "gemini-3.8-flash")
os.environ["DRY_RUN"] = "1"
# Escritura minima por la primera corrida: no tiene sentido gastar 20
# analisis sin saber primero que hay.
os.environ["PRESUPUESTO_GEMINI_DIARIO"] = "6"
os.environ["MAX_A_GEMINI"] = "4"
os.environ["MAX_NOTIFICACIONES"] = "4"
os.environ["PUNTAJE_MINIMO"] = "5"
os.environ["DIAS_DE_VENTANA"] = sys.argv[1] if len(sys.argv) > 1 else "10"

import config
import gemini_analyzer
import quota
import store
import telegram_notify as tn

DIAS = int(os.environ["DIAS_DE_VENTANA"])
print("=" * 74)
print(f"  KYOMOTO · ESCANEO REAL · ULTIMOS {DIAS} DIAS")
print("=" * 74)
print(f"  Fecha        {__import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M')}")
print(f"  Modelo       {config.GEMINI_MODEL}")
print(f"  Rango        USD {config.MIN_USD:,.0f} – {config.TOPE_USD:,.0f}")
print(f"  Margen neto  minimo {config.MARGEN_NETO_MIN * 100:.0f}%")
print()

store.init_db()
quota.init()
store.limpiar()
quota.limpiar()

print("-" * 74)
print("  ESTADO DE LAS APIs")
print("-" * 74)
s = __import__("sam_api")
sv = s.verificar_api()
gv = gemini_analyzer.verificar_api()
print(f"  SAM.gov  {'OK' if sv['ok'] else 'FALLA'}  {sv['detalle'][:90]}")
print(f"  Gemini   {'OK' if gv['ok'] else 'FALLA'}  {gv['detalle'][:90]}")
print()

print("-" * 74)
print("  BARRIDO")
print("-" * 74)
t0 = time.time()
r = __import__("scanner").escanear("1", dias=DIAS)
print()
print(f"  Tiempo total  {time.time() - t0:.0f} s")
print()

print("-" * 74)
print("  EMBUDO")
print("-" * 74)
print(f"  Avisos en SAM.gov .................. {r['traidas']}")
print(f"  Con producto fisico (veto NAICS) .. {r['candidatas']}")
print(f"  Sobre el filtro final ............. {r['puntuales']}")
print(f"  Analizados por la IA ............. {r['analizadas']}")
print(f"  VIABLES .......................... {r['viables']}")
print()

if r["errores"]:
    print("-" * 74)
    print("  PROBLEMAS")
    print("-" * 74)
    for e in r["errores"]:
        print(f"  · {e[:220]}")
    print()

if r["detalle_puntajes"]:
    print("-" * 74)
    print("  COMO PUNTUO CADA AVISO")
    print("-" * 74)
    for d in r["detalle_puntajes"][:8]:
        print(f"  [{d['puntaje']:>3}] {d['titulo'][:62]}")
        print(f"        {'; '.join(d['motivos'][:3])[:100]}")
    print()

if r["analisis"]:
    print("=" * 74)
    print("  FICHAS QUE KYOMOTO TE ENVIARIA A TELEGRAM")
    print("=" * 74)
    for a in r["analisis"]:
        print()
        print(tn.formatear_analisis(a))
        print()
        print("  " + "-" * 70)
else:
    print("  No salio ninguna opportunity viable en este barrido.")
    print("  Los motivos de descarte estan arriba, en los errores y puntajes.")
