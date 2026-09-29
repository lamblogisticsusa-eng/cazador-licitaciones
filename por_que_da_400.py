"""
por_que_da_400.py - Descubre por que gemini-3.5-flash-lite rechaza el prompt.

POR QUE IMPORTA
Descubrimos que la cuota de 20/dia es POR MODELO, no de la cuenta. En el
momento de la prueba:

    gemini-3.8-flash        SIN CUOTA
    gemini-flash-latest     SIN CUOTA
    gemini-3.5-flash-lite   DISPONIBLE   <-- el unico con cuota

Pero ese ultimo devolvia 400 INVALID_ARGUMENT con la configuracion real del
escaneo, asi que de nada sirve tener cuota si despues no acepta la peticion.
Es un callejon sin salida: los modelos que aceptan el prompt no tienen cuota, y
el que tiene cuota no acepta el prompt.

Este script va quitando piezas de la configuracion una a una para encontrar
CUAL es la que lo rechazo, porque si es algo que se puede apagar (por ejemplo
thinking_budget=0, que algunos modelos no admiten) se desbloquea un modelo con
cuota y la cuenta da un salto de 20 a 40 avisos analizados al dia gratis.

    python por_que_da_400.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if not os.environ.get("GEMINI_API_KEY"):
    print("Falta GEMINI_API_KEY en el entorno.")
    sys.exit(2)

from google import genai
from google.genai import types

cliente = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODELO = "gemini-3.5-flash-lite"

PROMPT = (
    'Responde solo con un JSON. '
    '{"id":"x","viable":true,"cantidad_total":45,"margen_neto_porcentaje":18.1}'
)

BASE = {
    "temperature": 0.2,
    "response_mime_type": "application/json",
    "max_output_tokens": 2048,
}


def probar(nombre, config, contents=PROMPT):
    try:
        r = cliente.models.generate_content(
            model=MODELO, contents=contents, config=config
        )
        print(f"  ✅ {nombre}")
        print(f"     -> {str(r.text)[:90]!r}")
        return True
    except Exception as e:
        s = str(e)
        if "400" in s:
            msg = s[s.find("'message'"):s.find("'message'") + 170]
            print(f"  ❌ {nombre}")
            print(f"     400: {msg}")
        elif "429" in s:
            print(f"  ⛔ {nombre}  (sin cuota ahora mismo)")
        elif "503" in s:
            print(f"  ⏳ {nombre}  (503, saturado, transitorio)")
        else:
            print(f"  ❓ {nombre}  {type(e).__name__}: {str(e)[:110]}")
        return False


print("=" * 74)
print(f"  POR QUE {MODELO} RECHAZA LA PETICION")
print("=" * 74)
print()

print("  1) Config minima, la que usa el smoke test clasico:")
config_minima = {"max_output_tokens": 2048}
p1 = probar("solo max_output_tokens", config_minima)

print()
print("  2) La config real del escaneo, parte por parte:")
p2 = probar("+ response_mime_type", {**config_minima, "response_mime_type": "application/json"})
p3 = probar("+ temperature", {**config_minima, "temperature": 0.2})
try:
    tc = types.ThinkingConfig(thinking_budget=0)
    p4 = probar("+ thinking_budget=0", {**config_minima, "thinking_config": tc})
except Exception as e:
    p4 = False
    print(f"  ❌ + thinking_budget=0  no se puede construir: {type(e).__name__}")

print()
print("  3) Config completa tal cual la usa Kyomoto:")
try:
    config_real = types.GenerateContentConfig(
        temperature=0.2,
        response_mime_type="application/json",
        max_output_tokens=2048,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    p5 = probar("config completa (la que falla)", config_real)
except Exception as e:
    p5 = False
    print(f"  ❓ config completa no se puede construir: {e}")

print()
print("=" * 74)
print("  DIAGNOSTICO")
print("=" * 74)
print()

if p1 and not (p2 or p3 or p4):
    print("  Con la config minima responde, y con CUALQUIER extra falla.")
    print("  Eso apunta a response_mime_type o thinking_budget=0.")
elif p1 and p2 and p3 and not p4:
    print("  >>> LA CULPA ES thinking_budget=0. <<<")
    print()
    print("  El modelo acepta response_mime_type y temperature, pero rechaza")
    print("  thinking_budget en cero. Kyomoto lo pone en 0 para no pagar")
    print("  razonamiento, y este modelo no lo admite.")
    print()
    print("  ARREGLO: si la config con thinking_budget=0 da 400, reintentar")
    print("  sin ese campo. Se pierde un poco de calidad en el razonamiento")
    print("  pero se desbloquea un modelo que TIENE CUOTA.")
elif not p1:
    print("  El modelo no acepta ni la config minima ahora mismo. Puede ser")
    print("  cuota o saturacion; hay que repetir cuando responda.")
else:
    print("  Acepta partes sueltas y rechaza el conjunto. Habria que probar")
    print("  combinaciones, pero el camino es el mismo: apagar lo que moleste.")

print()
print("=" * 74)
if p1 and p2 and p3 and not p4:
    print("  ACCION: reintentar sin thinking_budget cuando el modelo da 400")
    sys.exit(1)
print("  ACCION: repetir la prueba con cuota fresca para confirmar")
