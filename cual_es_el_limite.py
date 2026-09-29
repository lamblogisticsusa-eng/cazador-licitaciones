"""
cual_es_el_limite.py - Dice si el 429 es por minuto o por dia, y de cuanto.

No es un adorno: la solucion depende completamente de la respuesta.

  - Si fuera POR MINUTO, la respuesta es bajar el ritmo y el bot sigue
    trabajando con la misma clave.
  - Si es POR DIA (que es lo que tememos), ninguna optimizacion de codigo
    sirve: hay que cambiar de plan o cambiar de herramienta.

Ademas imprime la respuesta completa de la API, que nombra la metrica de
cuota agotada y a veces el limite concreto.
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if not os.environ.get("GEMINI_API_KEY"):
    print("Falta GEMINI_API_KEY en el entorno.")
    sys.exit(2)

from google import genai

cliente = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

MODELOS = [
    "gemini-3.8-flash",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
]

ok = 0
vistos_503 = set()
intentos = 0
t0 = time.time()

for ronda in range(25):
    for m in MODELOS:
        intentos += 1
        try:
            cliente.models.generate_content(
                model=m, contents="di OK", config={"max_output_tokens": 2048}
            )
            ok += 1
        except Exception as e:
            s = str(e)
            if "429" in s or "RESOURCE_EXHAUSTED" in s:
                print("=" * 70)
                print("  429: LA CUOTA ESTA AGOTADA")
                print("=" * 70)
                i = s.find("'message'")
                print()
                print("  Mensaje de Google:")
                print("  " + (s[i:i + 500] if i >= 0 else s[:500]))
                print()
                print("  ¿Que tipo de limite?")
                print(f"    dice 'per minute' : {'per minute' in s}")
                print(f"    dice 'per day'    : {'per day' in s}")
                print(f"    dice 'plan/billing': {'plan and billing' in s}")
                print()
                print("  Cuantas llamadas RESPONDIERON antes de agotarse:", ok)
                print(f"  Intentos totales: {intentos}")
                print(f"  Tiempo: {time.time() - t0:.0f}s")
                print()
                if "per minute" in s:
                    print("  DIAGNOSTICO: es un tope POR MINUTO.")
                    print("  Se arregla bajando el ritmo, no cambiando de plan.")
                else:
                    print("  DIAGNOSTICO: es un tope POR DIA (cuota del plan).")
                    print("  Ninguna optimizacion de codigo lo quita.")
                sys.exit(0)
            else:
                vistos_503.add(m)
    time.sleep(0.4)

print("=" * 70)
print("  SIN 429 EN ESTA RONDA")
print("=" * 70)
print(f"  Llamadas que respondieron: {ok} de {intentos}")
print(f"  Modelos con 503: {', '.join(sorted(vistos_503)) or 'ninguno'}")
print()
print("  Ahora mismo la API responde. El 429 viene y se va segun las horas")
print("  que lleva gastada la cuota diaria de la cuenta.")
