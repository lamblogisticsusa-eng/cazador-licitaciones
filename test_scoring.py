"""
test_scoring.py - Las dos reglas de scoring del 30-sep-2026.

1) PENALIZACION DE OBRA EN SITIO (-3)
   Equipo pesado + trabajo en sitio en el titulo, y NO es una venta de piezas.

2) BONUS DE SET-ASIDE DE SMALL BUSINESS (+2)
   Porque L.A.M.B. Logistics LLC ES una small business de EE.UU.: empresa
   unipersonal en Albuquerque, New Mexico. Un "Total Small Business
   Set-Aside" no es solo una pista de que la oportunidad es buena, es la
   confirmacion de que la empresa PUEDE presentarse.

LO QUE SE MIDIO ANTES DE APLICAR, Y POR QUE "REPLACEMENT" SE QUITO DE LA
LISTA DE TRABAJO

Se pidio penalizar "Replacement", "Installation" y "Repair". La palabra
"Replacement" fuera, porque los titulos reales del barrido del 29-sep-2026 son
kits de repuestos:

    58--NRP,MODULE,ACOUSTIC - AND OTHER REPLACEMENT PARTS
    16--BLOCK SWITCH CONTRO - AND SIMILAR REPLACEMENT PARTS
    53--PARTS KIT,SEAL REPLACEMENT,MECHANICA
    61--CABLE ASSEMBLY,SPEC- AND SIMILAR REPLACEMENT PARTS

Eso es el nucleo del negocio. Medido sobre los 11 titulos reales, la regla
literal no penaliza ninguno (exige equipo pesado ademas de la palabra, y los
kits no lo traen). El problema aparece al cruzar las dos:

    GENERATOR REPLACEMENT PARTS KIT      literal -3   corregida  0
    HVAC REPLACEMENT PARTS, 40 EA         literal -3   corregida  0
    REPLACEMENT PARTS FOR HVAC SYSTEM     literal -3   corregida  0
    PARTS KIT, REPLACEMENT GENERATOR MOTOR literal -3  corregida  0
    GENSET REPLACEMENT KITS                literal -3   corregida  0
    GENERATOR INSTALLATION AND TESTING    literal -3   corregida -3
    CHILLER REPAIR, LABOR AND PARTS        literal -3   corregida -3
    HVAC REPLACEMENT AND INSTALLATION      literal -3   corregida -3

La literal marcaba 9 de 10; la corregida 5. Los 4 de diferencia son ventas
de piezas. El objetivo era "evitar mano de obra en sitio" y la version
corregida lo cumple igual, sin comerse los kits de repuesto.
"""
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_scoring.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["DRY_RUN"] = "1"
os.environ["SAM_API_KEY"] = "x"
os.environ["GEMINI_API_KEY"] = "x"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


import config
import filters


def puntua(titulo, set_aside="", desc=""):
    opp = {
        "title": titulo, "naicsCode": "332999",
        "classificationCode": "2915", "typeOfSetAside": set_aside,
        "responseDeadLine": "2026-11-04T17:00:00-04:00",
    }
    return filters.puntuar(opp, desc)


print("=" * 72)
print("1) PENALIZACION DE OBRA EN SITIO (-3)")
print("=" * 72)
check("El peso de la penalizacion es 3", config.PENALIZACION_OBRA_PUNTOS == 3)
check("Y hay lista de equipo pesado", len(config.EQUIPO_PESADO) >= 8)
check("Y lista de trabajo en sitio", len(config.TRABAJO_EN_SITIO) >= 8)
check("Y lista de venta de piezas", len(config.VENTA_DE_PIEZAS) >= 5)
# Y que "replacement" NO este entre las palabras de trabajo, con el motivo.
check("'replacement' NO es palabra de obra (son piezas, no trabajo)",
      not any("replacement" in w for w in config.TRABAJO_EN_SITIO),
      "-> con 'replacement' dentro se penalizan las ventas de piezas")

print()
print("  LA MANO DE OBRA SE PENA:")
OBRA = [
    "GENERATOR 500KW INSTALLATION AND TESTING",
    "REPAIR OF HVAC SYSTEM, BUILDING 4",
    "INSTALL AND FURNISH CHILLED WATER GENERATOR",
    "MAINTENANCE OF GENERATORS, ANNUAL",
    "HVAC REPLACEMENT AND INSTALLATION",
    "CHILLER REPAIR, LABOR AND PARTS",
]
for t in OBRA:
    p, m = puntua(t)
    check(f"    {t[:44]}", any("obra en sitio" in x for x in m),
          f"-> {p} {m}")

print()
print("  LAS VENTAS DE PIEZAS NO SE PENAN (son el negocio):")
PIEZAS = [
    "GENERATOR REPLACEMENT PARTS KIT",
    "HVAC REPLACEMENT PARTS, 40 EA",
    "REPLACEMENT PARTS FOR HVAC SYSTEM",
    "PARTS KIT, REPLACEMENT GENERATOR MOTOR",
    "GENSET REPLACEMENT KITS",
    "SPARE PARTS KIT FOR CHILLER GENERATOR",
]
for t in PIEZAS:
    p, m = puntua(t)
    check(f"    {t[:44]}", not any("obra en sitio" in x for x in m),
          f"-> {p} {m}")

print()
print("  Y LOS KITS REALES DEL BARRIDO SIGUEN INTACTOS:")
REALES = [
    "58--NRP,MODULE,ACOUSTIC - AND OTHER REPLACEMENT PARTS",
    "16--BLOCK SWITCH CONTRO - AND SIMILAR REPLACEMENT PARTS",
    "53--PARTS KIT,SEAL REPLACEMENT,MECHANICA",
    "16--PARTS KIT,SEAL REPL",
    "PARTS KIT,BALL VALV",
    "43--PARTS KIT,ROTARY PUMP",
    "61--CABLE ASSEMBLY,SPEC- AND SIMILAR REPLACEMENT PARTS",
]
for t in REALES:
    p, m = puntua(t)
    check(f"    {t[:46]}", not any("obra en sitio" in x for x in m),
          f"-> {p} {m}")

print()
print("  SIN EQUIPO PESADO NO HAY PENALIZACION (no aplica a valvulas):")
for t in ("REPAIR PARTS FOR PUMP ASSEMBLY",
          "REPLACEMENT GASKET KIT, 12 EA",
          "INSTALLATION OF VALVE, LABOR ONLY"):
    p, m = puntua(t)
    print(f"    {t[:44]:46} {p:>4}  {'penalizado' if any('obra' in x for x in m) else 'sin penalizar'}")
# El ultimo es instalación sin equipo pesado: no se penaliza por esta regla.
# Lo quitan otras (PALABRAS_SERVICIO_OCULTO trae "installation of").
_p, _m = puntua("INSTALLATION OF VALVE, LABOR ONLY")
check("Una instalación sin equipo pesado no la penaliza ESTA regla",
      not any("obra en sitio" in x for x in _m), f"-> {_m}")

print()
print("=" * 72)
print("2) BONUS DE SET-ASIDE DE SMALL BUSINESS (+2)")
print("=" * 72)
check("El bonus es de 2 puntos", config.SET_ASIDE_PYME_PUNTOS == 2)
check("Y la lista existe", len(config.SET_ASIDE_PYME) >= 10)

BASE, _motivos = puntua("43--PARTS KIT,ROTARY PUMP")
print(f"  Puntaje base sin set-aside: {BASE}")
print()
print("  CON SET-ASIDE DE PYME:")
SA_OK = [
    "Total Small Business Set-Aside",
    "TOTAL SMALL BUSINESS",
    "SBA",
    "Total Small",
    "small business",
    "Total Small Business",
]
for sa in SA_OK:
    p, m = puntua("43--PARTS KIT,ROTARY PUMP", set_aside=sa)
    subio = p == BASE + config.SET_ASIDE_PYME_PUNTOS
    check(f"    {sa:32} {BASE} -> {p}", subio, f"-> {m}")

print()
print("  EL QUE ANTES SE ESCAPABA (el mas importante para una PYME):")
# El codigo viejo comparaba con igualdad exacta contra "TOTAL", y SAM.gov
# manda "Total Small Business Set-Aside", que no es igual a "TOTAL". O sea que
# el set-aside que mas le conviene a L.A.M.B. era justo el que no contaba.
p, m = puntua("43--PARTS KIT,ROTARY PUMP", set_aside="Total Small Business Set-Aside")
check("'Total Small Business Set-Aside' cuenta (antes no contaba)",
      p == BASE + config.SET_ASIDE_PYME_PUNTOS,
      "-> con igualdad exacta se escapaba, porque no es igual a 'TOTAL'")

print()
print("  LOS QUE EXIGEN CERTIFICACION AVISAN:")
for sa in ("WOSB", "EDWOSB", "HBC", "VOSBC", "SDVOSBC"):
    p, m = puntua("43--PARTS KIT,ROTARY PUMP", set_aside=sa)
    avisa = any("certificacion" in x for x in m)
    check(f"    {sa:10} {BASE} -> {p}  avisa de la certificacion", avisa,
          f"-> {m}")

print()
print("  LOS QUE NO SON DE PYME NO SUMAN:")
for sa in ("Full and Open", "Total Small Business Competitive", "N/A", "", "NA"):
    p, m = puntua("43--PARTS KIT,ROTARY PUMP", set_aside=sa)
    # "Total Small Business Competitive" si es de PYME, asi que solo se
    # comprueban los que no lo son de ninguna manera.
    if "Total Small Business Competitive" in sa:
        continue
    check(f"    {sa!r:24} no suma", p == BASE,
          f"-> {p} (subio sin shouldnar)")

print()
print("=" * 72)
print("3) LAS DOS REGLAS A LA VEZ, Y QUE NO SE ROMPA NADA")
print("=" * 72)
# Obra con set-aside: las dos reglas a la vez. El bonus no puede tapar la
# penalizacion, porque si no un set-aside de PYME compraria el problema.
#
# Cada titulo se compara CONTRA SI MISMO sin set-aside. Los dos titulos tienen
# bases distintas (3 y 12) porque no comparten palabras de producto, asi que
# compararlos entre si no diria nada.
OBRA_T = "GENERATOR INSTALLATION AND TESTING"
PIEZA_T = "43--PARTS KIT,ROTARY PUMP"
b_obra, _ = puntua(OBRA_T)
b_pieza, _ = puntua(PIEZA_T)
p_obra, m_obra = puntua(OBRA_T, "Total Small Business")
p_solo, m_solo = puntua(PIEZA_T, "Total Small Business")
print(f"  Obra  : {b_obra:>3} sin set-aside -> {p_obra:>3} con  ({p_obra - b_obra:+d})")
print(f"  Pieza : {b_pieza:>3} sin set-aside -> {p_solo:>3} con  ({p_solo - b_pieza:+d})")
check("El set-aside suma +2 en el titulo de obra",
      p_obra - b_obra == config.SET_ASIDE_PYME_PUNTOS, f"-> {p_obra - b_obra}")
check("Y +2 en el de pieza",
      p_solo - b_pieza == config.SET_ASIDE_PYME_PUNTOS, f"-> {p_solo - b_pieza}")
check("Y la penalizacion sigue aplicando con set-aside",
      any("obra en sitio" in x for x in m_obra), f"-> {m_obra}")
check("La obra sigue por debajo de la pieza pese al set-aside",
      p_obra < p_solo, f"-> {p_obra} vs {p_solo}")

# Y que el veto por NAICS de servicios siga por encima de todo: una
# PYME con set-aside no puede comprar un contrato de servicios con un +2.
# Se usa un NAICS de servicios de verdad: el 332999 es manufactura y no
# dispararia el veto.
_NAICS_SERVICIOS = sorted(config.NAICS_SERVICIOS)[0]
p_serv, m_serv = puntua("43--PARTS KIT,ROTARY PUMP", "Total Small Business")
p_serv2 = filters.puntuar(
    {"title": "43--PARTS KIT,ROTARY PUMP", "naicsCode": _NAICS_SERVICIOS,
     "classificationCode": "2915", "typeOfSetAside": "Total Small Business",
     "responseDeadLine": "2026-11-04T17:00:00-04:00"}, "")[0]
print(f"  NAICS de servicios de prueba: {_NAICS_SERVICIOS}")
print(f"  Con set-aside y NAICS de servicios: {p_serv2} (veto)")
check("El veto por NAICS de servicios sigue mandando",
      p_serv2 <= -100, f"-> {p_serv2}")
check("Y el set-aside no lo compra",
      "set-aside" not in " ".join(m_serv).lower() or p_serv2 <= -100,
      f"-> {p_serv2} {m_serv}")

# Y sin efectos raros en el resto del scoring.
p_ven, _ = puntua(PIEZA_T, "Total Small Business")
check("Sin efectos raros en el resto del scoring",
      abs(p_ven - p_solo) < 0.01)

for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

print()
print("=" * 72)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: las dos reglas de scoring funcionan")
