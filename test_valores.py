"""
test_valores.py - Los montos de SAM.gov se leen bien.

EL BUG MAS GRAVE QUE HA TENIDO KYOMOTO (28-sep-2026)
La clase de caracteres de la regex de montos era [0-9,.]{3,}, que admite
puntos. En un texto real de SAM.gov:

    "Not to exceed USD 85,000.00. Material MIL-STD"

la regex se comia el punto que cierra la frase y capturaba '85,000.00.'.
Al partirlo por puntos salian TRES trozos en vez de dos, no caia en la rama
de los centavos, y el monto terminaba en 8500000: cien veces el real.

    85,000.00  ->  8,500,000  ->  > 250,000  ->  DESCARTADA

O sea que el filtro de rango de USD descartaba precisamente las licitaciones
buenas, y casi todas: en SAM.gov los montos se escriben con centavos seguidos
de punto final. Este era el motivo de que el embudo llegara a cero viables.

Segundo bug, en la misma funcion: la palabra "quantity" se buscaba solo en
los 20 caracteres previos al INICIO del match, pero el match empieza en la
palabra clave. En "Estimated quantity 2,000 units" la palabra "quantity" cae
DENTRO del match, asi que no se veia y el 2,000 se tomaba por dinero.
"""
import os
import sys

os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("SAM_API_KEY", "x")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import filters

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


print("=" * 70)
print("1) LOS MONTOS CON CENTAVOS SE LEEN EXACTOS")
print("=" * 70)
CASOS = [
    # (texto, monto esperado)  -- el caso del bug primero
    ("Not to exceed USD 85,000.00. Material MIL-STD", 85_000),
    ("Not to exceed USD 40,000.00. Material", 40_000),
    ("Not to exceed USD 30,000.00. Entrega en USA", 30_000),
    ("Not to Exceed USD 350,000.00", 350_000),
    ("Not to exceed USD 850,000.00. Beyond this", 850_000),
    ("NTE 85,000.00 USD", 85_000),
    ("up to 250,000.00", 250_000),
    ("ceiling 45,000", 45_000),
    ("total 12,000.50", 12_000.5),
    ("award 5,000", 5_000),
    ("Contract value 5,000.00", 5_000),
    ("Budget: $1,250,000.00", 1_250_000),
    ("estimated at $7,500", 7_500),
    ("$99,999.99", 99_999.99),
    # Los avisos de SAM.gov vienen en ingles: las palabras clave del extractor
    # ("estimated value", "not to exceed", "NTE") son ingleses a proposito.
    ("estimated value 6,000.00 USD", 6_000),
    ("Contract value 275,000", 275_000),
]
for txt, esperado in CASOS:
    v, frag = filters.valor_declarado(txt)
    ok = v is not None and abs(v - esperado) < 0.01
    check(f"{v!s:>12} = {esperado:>12}   {txt[:44]}", ok,
          f"-> leyo {v}")

print()
print("=" * 70)
print("2) NUNCA SE INFLA 100 VECES (el bug concreto)")
print("=" * 70)
# Reproduccion exacta de lo que pasaba antes del arreglo.
for base in (5_000, 30_000, 85_000, 120_000, 250_000):
    txt = f"Not to exceed USD {base:,}.00. Material"
    v, _ = filters.valor_declarado(txt)
    check(f"{base:,}.00 se lee {base:,} y no {base * 100:,}",
          v is not None and abs(v - base) < 0.01, f"-> leyo {v}")

# Y el efecto que tenia: un contrato de 85,000 caia por encima del tope.
TOPE = 250_000
v, _ = filters.valor_declarado("Not to exceed USD 85,000.00. Material")
check("Un contrato de 85,000 NO se toma por encima del tope",
      v is not None and v < TOPE, f"-> leyo {v}")

print()
print("=" * 70)
print("3) LOS NUMEROS QUE NO SON CONTRATO SE DESCARTAN")
print("=" * 70)
NO_MONTOS = [
    "120 days after award of contract",
    "ISO 9001 certified manufacturer required",
    "NAICS 332999 manufacturing",
    "The contractor shall deliver 30 days after order",
]
for txt in NO_MONTOS:
    v, _ = filters.valor_declarado(txt)
    check(f"sin monto: {txt[:44]}", v is None, f"-> leyo {v}")

# El bug del "quantity": la palabra cae DENTRO del match, no antes.
print()
print("  Cantidades que NO son dinero:")
for txt, esperado_none in (
    ("Estimated quantity 2,000 units required", True),
    ("estimated quantity 5,000 each", True),
    ("Quantity: 500 units delivered", True),
):
    v, _ = filters.valor_declarado(txt)
    check(f"no toma cantidad por dinero: {txt[:40]}", v is None, f"-> leyo {v}")

# Y cuando hay un monto real al lado, gana el monto.
print()
print("  Con un monto real al lado, gana el monto:")
v, frag = filters.valor_declarado(
    "Estimated quantity 2,000 units. Not to Exceed 350,000.00. Entrega en USA."
)
check("Toma el NTE, no la cantidad", v == 350_000, f"-> leyo {v}")

# Un monto grande con "quantity" cerca sigue valiendo: es dinero.
v, _ = filters.valor_declarado("quantity of items, Not to Exceed 85,000.00 USD")
check("Un monto grande gana aunque diga 'quantity'", v == 85_000, f"-> leyo {v}")

print()
print("=" * 70)
print("4) LO QUE EL ESCANER HACE CON ESOS MONTOS")
print("=" * 70)
import config

for txt, esperado in (
    ("Not to exceed USD 85,000.00. Material", "dentro"),
    ("Not to exceed USD 350,000.00. Too big", "sobre el tope"),
    ("Not to exceed USD 3,000.00. Too small", "bajo el minimo"),
):
    v, _ = filters.valor_declarado(txt)
    if esperado == "dentro":
        ok = v is not None and config.MIN_USD <= v <= config.TOPE_USD
    elif esperado == "sobre el tope":
        ok = v is not None and v > config.TOPE_USD
    else:
        ok = v is not None and v < config.MIN_USD
    check(f"{esperado:>14}: {txt[:40]}", ok, f"-> leyo {v}")

print()
print("=" * 70)
print("5) EL FILTRO NO DESCARTA LAS LICITACIONES BUENAS")
print("=" * 70)
# El sintoma que veia el usuario: cero viables. Con el bug, todo monto con
# centavos quedaba inflado y se iba. Ahora deben entrar.
BUENAS = [
    "Adquisicion de 12 modulos de control y 4 kits de alternadores. "
    "Not to exceed USD 85,000.00. Entrega fisica en base.",
    "Suministro de repuestos para generadores 50kW. "
    "Not to exceed USD 45,000.00. Distribuidor con stock en USA.",
    "Printer consumables for the Navy. NTE 32,500.00 USD. "
    "Award to authorized distributor.",
]
for txt in BUENAS:
    v, _ = filters.valor_declarado(txt)
    ok = v is not None and config.MIN_USD <= v <= config.TOPE_USD
    check(f"sobrevive al rango: {v!s:>10}  {txt[:40]}", ok, f"-> leyo {v}")

print()
print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: los montos se leen como son")
