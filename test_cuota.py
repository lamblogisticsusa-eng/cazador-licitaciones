"""
test_cuota.py - Prueba el presupuesto diario de Gemini.

OJO: PRESUPUESTO_GEMINI_DIARIO es POR MODELO. El tope del dia es ese
numero multiplicado por cuantos modelos haya en la cadena. Medido el
29-sep-2026: la cuota es de cada modelo por separado, no de la cuenta.
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
check("El tope es POR MODELO: 10 x 6 modelos",
      e["presupuesto"] == 10 * e["modelos"],
      f"-> {e['presupuesto']} con {e['modelos']} modelos")
check("Y dice cuantos modelos son", e["modelos"] >= 4, f"-> {e['modelos']}")
check("Y cuanto es por modelo", e["por_modelo"] == 10,
      f"-> {e['por_modelo']}")
check("Todos los disponibles", e["restantes"] == 10 * e["modelos"],
      f"-> {e['restantes']}")
check("No agotado", e["agotado"] is False)
print()

for _ in range(6):
    quota.gastar()
e = quota.estado()
check(f"Tras 6 llamadas quedan {10 * 6 - 6}",
      e["restantes"] == 10 * e["modelos"] - 6, f"-> {e['restantes']}")
quota.gastar_fallo()
check("El fallo no cuenta como llamada", quota.estado()["usadas"] == 6,
      f"-> {quota.estado()['usadas']}")
check("El fallo si se registra", quota.estado()["fallidas"] == 1)
print()

print("=" * 70)
print("2) NO SE PASA DEL PRESUPUESTO")
print("=" * 70)
# Se gasta de mas a proposito, para comprobar que quedan() corta en el tope
# y no lo rebasa. El tope ahora es 10 x 6 modelos = 60, no 10.
TOPE = 10 * quota.modelos_disponibles()
for _ in range(TOPE + 6):
    if quota.quedan() > 0:
        quota.gastar()
e = quota.estado()
check(f"Se detiene en {TOPE}, no en {TOPE + 6}",
      e["usadas"] == TOPE, f"-> {e['usadas']}")
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
check("El conteo sobrevive al reinicio",
      e2["usadas"] == 10 * 6, f"-> {e2['usadas']}")
check("Las reservas sobreviven", quota.reservar("aviso-X") is False)
print()

print("=" * 70)
print("5) HISTORIAL")
print("=" * 70)
d = quota.dias_registrados()
check("Registra el dia de hoy", len(d) == 1, f"-> {d}")
check("Con el conteo correcto", d[0]["llamadas"] == 10 * 6, f"-> {d[0]}")
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
