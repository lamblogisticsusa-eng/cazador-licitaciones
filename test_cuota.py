"""
test_cuota.py - Prueba el presupuesto diario de Gemini.
No toca la red: usa una base de datos temporal.
"""
import os
import sys
import tempfile

os.environ.setdefault("DRY_RUN", "1")

DB = os.path.join(tempfile.gettempdir(), "kyo_test_cuota.db")
for sufijo in ("", "-wal", "-shm"):
    try:
        os.remove(DB + sufijo)
    except OSError:
        pass
os.environ["DB_PATH"] = DB
os.environ["PRESUPUESTO_GEMINI_DIARIO"] = "10"

import quota

FALLA = 0


def check(nombre, condicion, extra=""):
    global FALLA
    if condicion:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


quota.init()
print("=" * 70)
print("1) CONTEO DEL PRESUPUESTO")
print("=" * 70)
e = quota.estado()
check("Arranca en 0", e["usadas"] == 0, f"-> {e}")
check("Presupuesto de 10", e["presupuesto"] == 10, f"-> {e['presupuesto']}")
check("10 disponibles", e["restantes"] == 10, f"-> {e['restantes']}")
check("No agotado", e["agotado"] is False)
print()

for _ in range(6):
    quota.gastar()
e = quota.estado()
check("Tras 6 llamadas quedan 4", e["restantes"] == 4, f"-> {e['restantes']}")
quota.gastar_fallo()
check("El fallo no cuenta como llamada", quota.estado()["usadas"] == 6,
      f"-> {quota.estado()['usadas']}")
check("El fallo si se registra", quota.estado()["fallidas"] == 1)
print()

print("=" * 70)
print("2) NO SE PASA DEL PRESUPUESTO")
print("=" * 70)
for _ in range(10):
    if quota.quedan() > 0:
        quota.gastar()
e = quota.estado()
check("Se detiene en 10, no en 16", e["usadas"] == 10, f"-> {e['usadas']}")
check("Restantes en 0", e["restantes"] == 0)
check("Marca agotado", e["agotado"] is True)
print()

print("=" * 70)
print("3) RESERVAS: UN AVISO NO CUENTA DOS VECES")
print("=" * 70)
check("Primera reserva de X es True", quota.reservar("aviso-X") is True)
check("Segunda reserva de X es False", quota.reservar("aviso-X") is False)
check("Otro aviso si se puede", quota.reservar("aviso-Y") is True)
print()

print("=" * 70)
print("4) PERSISTE ENTRE PROCESOS (Render reinicia el proceso)")
print("=" * 70)
quota.init()
e2 = quota.estado()
check("El conteo sobrevive al reinicio", e2["usadas"] == 10, f"-> {e2['usadas']}")
check("Las reservas sobreviven", quota.reservar("aviso-X") is False)
print()

print("=" * 70)
print("5) HISTORIAL")
print("=" * 70)
d = quota.dias_registrados()
check("Registra el dia de hoy", len(d) == 1, f"-> {d}")
check("Con el conteo correcto", d[0]["llamadas"] == 10, f"-> {d[0]}")
print()

for sufijo in ("", "-wal", "-shm"):
    try:
        os.remove(DB + sufijo)
    except OSError:
        pass

print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: presupuesto diario correcto")
