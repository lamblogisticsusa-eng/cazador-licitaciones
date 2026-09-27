"""
test_valor.py - Prueba del extractor de valor de contrato.
Sirve para no gastar llamadas de Gemini en avisos que ya estan descartados.
"""
import os
import sys

os.environ.setdefault("SAM_API_KEY", "x")
os.environ.setdefault("DRY_RUN", "1")

import config
import filters

FALLA = 0


def check(nombre, condicion, extra=""):
    global FALLA
    if condicion:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


print("=" * 70)
print("1) EXTRACCION DE VALORES")
print("=" * 70)
casos = [
    # (texto, valor esperado)
    ("Indefinite Delivery Contract: Estimated quantity 2.000 ; Not to Exceed 350,000.00", 350000.0),
    ("The total contract value is $125,000.00 for all items.", 125000.0),
    ("NTE $1,250,000", 1250000.0),
    ("Contract value: 45,000", 45000.0),
    ("Estimated at $99,999", 99999.0),
    ("Firm Fixed Price of $12,500.00", 12500.0),
    ("Ceiling price $2,000,000 per year", 2000000.0),
    ("Contract amount: $85,000", 85000.0),
]
for texto, esperado in casos:
    valor, frag = filters.valor_declarado(texto)
    ok = valor is not None and abs(valor - esperado) < 1
    check(f"{valor!s:>12} (esperado {esperado:,.0f})  <- {texto[:46]}", ok,
          f"| frag: {frag[:60]}")

print()
print("2) NO DEBE INVENTAR MONTOS")
print("=" * 70)
falsos = [
    "This is a 5-year contract with 12 months of effort",
    "Cage 3400, Building 12, Line 3",
    "No hay ningun monto aqui",
    "",
    "ISO 9001:2015 certified facility, 40 employees",
]
for texto in falsos:
    valor, _ = filters.valor_declarado(texto)
    check(f"None <- {texto[:46]!r}", valor is None, f"-> devolvio {valor}")

print()
print("3) EL FILTRO DE TOPE FUNCIONA")
print("=" * 70)
grande = "Indefinite Delivery Contract: Not to Exceed 350,000.00"
chico = "Firm Fixed Price of $45,000.00"
vg, _ = filters.valor_declarado(grande)
vc, _ = filters.valor_declarado(chico)
print(f"  TOPE_USD = {config.TOPE_USD:,.0f} | MIN_USD = {config.MIN_USD:,.0f}")
check("El grande se descarta SOLO, sin llamar a Gemini",
      vg is not None and vg > config.TOPE_USD, f"-> {vg:,.0f}")
check("El chico pasa el filtro",
      vc is not None and config.MIN_USD <= vc <= config.TOPE_USD, f"-> {vc:,.0f}")

print()
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: extractor de valor correcto")
