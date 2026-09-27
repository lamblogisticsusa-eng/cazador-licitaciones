"""
test_descalificadores.py - Los filtros que=suben la tasa de acierto.
Mide si los casos que Gemini descartaba en los analisis reales ahora se
detectan ANTES de gastar una llamada, y si los buenos siguen pasando.
"""
import os
import sys

os.environ.setdefault("SAM_API_KEY", "SAM-clave-de-prueba")
os.environ.setdefault("DRY_RUN", "1")

import config
import filters

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


def aviso(titulo, naics="332919", pais="USA", estado="OR", set_aside="NONE", dl="2026-11-01"):
    return {
        "title": titulo,
        "naicsCode": naics,
        "classificationCode": "5330",
        "typeOfSetAside": set_aside,
        "responseDeadLine": dl + "T18:00:00-07:00",
        "placeOfPerformance": {
            "city": {"name": "Portland"},
            "state": {"code": estado, "name": estado},
            "country": {"code": pais, "name": pais},
        },
    }


print("=" * 70)
print("1) CASOS QUE GEMINI DESCARTABA (de analyses reales)")
print("=" * 70)
descartables = [
    ("PARTS KIT,BALL VALV with DD2345 certification required", "DD2345"),
    ("COMPRESSOR PARTS OVERHAUL/REBUILD requiring First Article Testing", "First Article Testing"),
    ("CABLE ASSEMBLY with security clearance Level I scope of certification", "security clearance"),
]
for titulo, attendu in descartables:
    a = aviso(titulo)
    p, m = filters.puntuar(a, titulo)
    print(f"    [{p:>3}] {titulo[:60]}")
    print(f"          -> {'; '.join(m[:3])}")
    check(f"  Peniza por {attendu}", p < 4, f"-> puntaje {p} es demasiado alto")

print()
print("  -- destino en el extranjero --")
dubai = {
    "title": "K LOADER PARTS SUPPLY AT AL MINHAD AIRBASE",
    "naicsCode": "333924", "classificationCode": "5330", "typeOfSetAside": "NONE",
    "responseDeadLine": "2026-11-01T18:00:00-07:00",
    "placeOfPerformance": {
        "city": {"name": "Dubai"},
        "state": {"code": "AE", "name": "AE"},
        "country": {"code": "ARE", "name": "ARE"},   # el codigo miente
    },
}
p, m = filters.puntuar(dubai, "Partes para la base Al Minhad")
print(f"    [{p:>3}] {dubai['title'][:60]}")
print(f"          -> {'; '.join(m[:3])}")
check("Detecta Dubai aunque countryCode diga ARE", any("EXTRANJERO" in x for x in m))
check("Dubai cae por debajo del piso (veto practico)", p < config.PUNTAJE_MINIMO, f"-> {p}")

usa = {
    "title": "K LOADER PARTS SUPPLY",
    "naicsCode": "333924", "classificationCode": "5330", "typeOfSetAside": "NONE",
    "responseDeadLine": "2026-11-01T18:00:00-07:00",
    "placeOfPerformance": {
        "city": {"name": "Norfolk"},
        "state": {"code": "VA", "name": "Virginia"},
        "country": {"code": "USA", "name": "UNITED STATES"},
    },
}
p2, m2 = filters.puntuar(usa, "Partes mecanicos para kapal turbines")
check("Un destino en USA NO se penaliza", not any("extranjero" in x for x in m2),
      f"-> {m2}")
print()

print("=" * 70)
print("2) OPORTUNIDADES BUENAS NO DEBEN PERDERSE")
print("=" * 70)
buenas = [
    ("USNS MERCY MAIN MACHINERY ROOM FIRE PUMP VALVES", "332911", "SBA",
     "Supply of bronze gate valves for shipboard fire pump. Quantity 1 lot. Contract value $48,000."),
    ("84--Personal Protective Equipment for Fort Apache Agency", "315990", "SBA",
     "Sizes S-5XL. Estimated 2,400 EA. Total $62,000."),
    ("Printer Consumables for Strategic Sourcing", "325992", "SBA",
     "Toner cartridges and drums. Contract value $88,000 firm fixed price."),
    ("Motor Control Panels", "335313", "NONE",
     "Furnish and deliver 6 motor control panels. $120,000."),
]
for titulo, naics, sa, desc in buenas:
    a = aviso(titulo, naics=naics, set_aside=sa)
    p, m = filters.puntuar(a, desc)
    print(f"    [{p:>3}] {titulo[:60]}")
    check(f"  Sigue sobre el piso ({config.PUNTAJE_MINIMO})", p >= config.PUNTAJE_MINIMO,
          f"-> {p}; motivos: {m[:2]}")

print()
print("=" * 70)
print("3) SIN REGRESSION: NAICS y palabras siguen mandando")
print("=" * 70)
serv = aviso("IT Help Desk Support Services", naics="541512")
p, _ = filters.puntuar(serv, "Managed IT support, help desk, network services")
check("Servicios de IT se rechazan", p == -100, f"-> {p}")

manuf = aviso("53--PARTS KIT,SEAL REPLACEMENT,MECHANICA", naics="339991")
p, _ = filters.puntuar(manuf, "Indefinite Delivery Contract: Not to Exceed 350,000.00")
check("NAICS de manufactura sigue contando", p > 0, f"-> {p}")

print()
print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: los descalificadores funcionan y las buenas sobreviven")
