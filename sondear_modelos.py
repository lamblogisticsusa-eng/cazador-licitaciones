"""
sondear_modelos.py - Que modelos de Gemini responden ahora mismo.

Ante un 503 de "alta demanda", la salida no es esperar 16 segundos: es
probar con otro modelo. Esto mide cuales estan disponibles.

    python sondear_modelos.py
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if not os.environ.get("GEMINI_API_KEY"):
    print("Falta GEMINI_API_KEY en el entorno.")
    sys.exit(2)

from google import genai

# Los candidatos, con un MODELO_MALO al final a proposito: "gemini-flash-lite"
# no existe y sirve de control. Si un dia la API empiezan a responder a un
# modelo inexistente, ese control deja de servir y hay que poner otro.
#
# OJO: esta lista es de SONDEO, no la cadena de respaldo. Aqui van todos los
# candidatos, viejos incluidos, porque el objeto es descubrir cuales existen y
# tienen cuota. Los que ya no se usan en Kyomoto se pueden quitar de
# config.GEMINI_MODELES_ALTERNATIVOS y seguir estando aqui.
MODELOS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    # Entro el 10-oct-2026 en la cadena de respaldo. Hay que confirmar que
    # existe y que responde, que es justo lo que hace este script.
    "gemini-3.6-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite",
]

cliente = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

print("=" * 70)
print("  MODELOS DISPONIBLES AHORA")
print("=" * 70)
print()

libres = []
for m in MODELOS:
    t0 = time.time()
    try:
        r = cliente.models.generate_content(
            model=m, contents="di OK", config={"max_output_tokens": 2048}
        )
        dt = time.time() - t0
        print(f"  ✅ {m:26} {dt:5.1f}s  -> {str(r.text)[:20]!r}")
        libres.append(m)
    except Exception as e:
        s = str(e)
        if "429" in s:
            estado = "⛔ cuota agotada"
        elif "503" in s or "UNAVAILABLE" in s:
            estado = "⏳ saturado (503)"
        elif "404" in s or "not_found" in s:
            estado = "❌ no existe"
        else:
            estado = f"❌ {s[:50]}"
        print(f"  {estado:32} {m}")
    time.sleep(1.0)

print()
print("=" * 70)
if not libres:
    print("  NINGUN MODELO RESPONDE")
    print("  Puede ser cuota agotada o saturacion general de Google.")
    print("  Espera a que se reinicie la cuota (medianoche del Pacifico).")
else:
    print(f"  FUNCIONAN: {', '.join(libres)}")
    print()
    print("  Configura el bot con el primero de esta lista:")
    print(f"    GEMINI_MODEL={libres[0]}")
print("=" * 70)
