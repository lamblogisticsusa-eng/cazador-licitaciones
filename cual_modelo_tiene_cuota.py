"""
cual_modelo_tiene_cuota.py - ¿El tope de 20/día es por modelo o de la cuenta?

POR QUE IMPORTA MUCHO
Google devuelve, en el 429, el nombre del modelo que se quedo sin cuota:

    * Quota exceeded for metric:
      generativelanguage.googleapis.com/generate_content_free_tier_requests,
      limit: 20, model: gemini-3.8-flash

Si ese "model:" es solo informativo, la cuota es de la cuenta y da igual
cambiar de modelo: 20 llamadas al dia, punto.
Si el tope es POR MODELO, entonces cada modelo de la lista tiene sus propias
20, y el total real multiplica. Con 6 modelos serian 120 avisos analizados al
dia sin pagar un peso. Kyomoto hoy cambia de modelo en el 503, pero se rinde
en cuanto uno responde 429, asi que no aprovecha esa capacidad.

Este script lo comprueba preguntandole a cada modelo por separado.

    python cual_modelo_tiene_cuota.py
"""
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if not os.environ.get("GEMINI_API_KEY"):
    print("Falta GEMINI_API_KEY en el entorno.")
    sys.exit(2)

from google import genai

cliente = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

MODELOS = [
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-flash-latest",
    "gemini-3.5-flash-lite",
]

print("=" * 74)
print("  ¿CADA MODELO TIENE SU PROPIA CUOTA?")
print("=" * 74)
print()

ok, sin_cuota, saturados, otros = [], [], [], []

for m in MODELOS:
    try:
        cliente.models.generate_content(
            model=m, contents="di OK", config={"max_output_tokens": 2048}
        )
        ok.append(m)
        print(f"  ✅ {m:24} DISPONIBLE")
    except Exception as e:
        s = str(e)
        if "429" in s:
            sin_cuota.append(m)
            tope = re.search(r"limit:\s*(\d+)", s)
            dicho = re.search(r"model:\s*(\S+?),", s + ",")
            print(f"  ⛔ {m:24} SIN CUOTA   tope={tope.group(1) if tope else '?'}"
                  f"  (el error nombra: {dicho.group(1) if dicho else '?'})")
        elif "503" in s:
            saturados.append(m)
            print(f"  ⏳ {m:24} SATURADO (503, transitorio)")
        else:
            otros.append(m)
            print(f"  ❓ {m:24} {type(e).__name__}")
    time.sleep(0.5)

print()
print("=" * 74)
print("  RESULTADO")
print("=" * 74)
print(f"  Con cuota   : {len(ok)}  {', '.join(ok) if ok else '-'}")
print(f"  Sin cuota   : {len(sin_cuota)}  {', '.join(sin_cuota) if sin_cuota else '-'}")
print(f"  Saturados   : {len(saturados)}  {', '.join(saturados) if saturados else '-'}")
print()

if ok and sin_cuota:
    print("  DIAGNOSTICO: el tope es POR MODELO.")
    print()
    print(f"  Hay {len(ok)} modelos con cuota y {len(sin_cuota)} sin ella al")
    print("  mismo tiempo. Si la cuota fuera de la cuenta, ningun modelo")
    print("  responderia. Como unos funcionan y otros no, cada uno lleva su")
    print("  propio contador de 20 al dia.")
    print()
    print(f"  Kyomoto puede pedirle {len(ok)} x 20 = {len(ok) * 20} analisis al")
    print("  dia sin pagar nada, en vez de 20.")
    print()
    print("  Para aprovecharlo hay que cambiar una cosa: hoy, en cuanto un")
    print("  modelo responde 429, Kyomoto da toda la llamada por perdida.")
    print("  Deberia pasar al siguiente de la lista, como hace con el 503.")
elif ok and not sin_cuota:
    print("  Todos responden: la cuota no se ha tocado hoy (o se reinicio).")
    print("  No se puede concluir nada sobre el reparto por modelo.")
else:
    print("  Ningun modelo responde ahora mismo. Tope de la cuenta, o")
    print("  saturacion general de Google. Se vera cuando se reinicie la cuota.")

print()
print("=" * 74)
if ok and sin_cuota:
    print("  ACCION: cambiar el 429 para que pruebe el siguiente modelo")
    sys.exit(1)
print("  ACCION: ninguna, el comportamiento actual sirve")
sys.exit(0)
