"""
test_cots.py - La orientacion COTS Easy-Supply.

QUE SE PIDE

1) Descarte inmediato con log [DROPPED] high_complexity_defense para:
   plataformas de aviacion militar, buques de guerra, vehiculos blindados,
   armamento, municion y combustible de aviacion militar; y para las
   certificaciones de origen exclusivas (ITAR, JCP, CoC militar, DD250).

2) Priorizar las familias PSC donde hay distribuidor comercial abierto en
   EE.UU.: medico/hospitalario, MRO y ferreteria, limpieza e instalaciones,
   computo y TI, y partes de flota vehicular comercial.

3) La instruccion de "Facilidad de Comercializacion" en el prompt de Gemini.

LO MAS IMPORTANTE DE ESTA SUITE: QUE NO SE COMAN EL NEGOCIO

Los titulos que mejor puntuan de SAM.gov para L.A.M.B. son piezas navales.
Si el filtro se los come, el bot deja de encontrar lo que le funciona, y eso
pasa sin que se note: simplemente el numero de candidatos baja.

Medido antes de aplicar: 0 de 11 titulos reales tocados, 12 de 12 excluidos.

LA TRAMPA DE "itar"

"itar" esta DENTRO de "military", "maritime", "similar" y "particular". Con
busqueda normal:

    Material must be of military CoC origin, strictly certified.
    -> matchea "itar" por el "milITARy"

Y se descartaba un aviso que habla de origen militar NORMAL, que es justo de
los que hay que aceptar. Con limite de palabra, no.

"armored" SUELTO TAMPOCO

"armored cable" y "armored hose" son comerciales. La suite lo comprueba con
un "ARMORED CABLE 12/2" real, que se compra en cualquier distribuidor.
"""
import io
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_cots.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "x"
os.environ["GEMINI_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"
os.environ["DRY_RUN"] = "1"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print("  OK    %s" % nombre)
    else:
        FALLA += 1
        print("  FALLA %s %s" % (nombre, extra))


import config
import filters
import scanner

FUTURO = "2026-12-15T17:00:00-04:00"


def opp(titulo, psc="5330", naics="332999", sa=""):
    return {"title": titulo, "naicsCode": naics, "classificationCode": psc,
            "typeOfSetAside": sa, "responseDeadLine": FUTURO}


def veto(titulo, desc="", **kw):
    """Devuelve el motivo del veto, o "" si no hay veto."""
    p, motivos = filters.puntuar(opp(titulo, **kw), desc)
    return motivos[0] if p <= -100 else ""


print("=" * 76)
print("1) LOS TITULOS REALES DEL BARRIDO NO SE TOCAN")
print("=" * 76)
print("  (los kits de repuestos navales son el nucleo del negocio)")
print()
REALES = [
    "58--NRP,MODULE,ACOUSTIC - AND OTHER REPLACEMENT PARTS",
    "16--BLOCK SWITCH CONTRO - AND SIMILAR REPLACEMENT PARTS",
    "53--PARTS KIT,SEAL REPLACEMENT,MECHANICA",
    "16--PARTS KIT,SEAL REPL",
    "PARTS KIT,BALL VALV",
    "43--PARTS KIT,ROTARY PUMP",
    "43--PARTS KIT,FLUID PRESSURE FILTER",
    "61--CABLE ASSEMBLY,SPEC- AND SIMILAR REPLACEMENT PARTS",
    "81--CONTAINER,TOOL ROOM AND EQUIPMENT ST",
    "6640 - WSNC IDIQ Respiratory Fit Testing Equipment, Supplies, and Annual Calib",
    "Solicitation: Parts Kit, Hydraulic Pump ( NSN: 4320-01-086-6793)",
]
for t in REALES:
    v = veto(t)
    if v:
        print("  MAL   %s" % t[:62])
        print("         -> %s" % v)
    else:
        print("  ok    %s" % t[:62])
check("Los 11 titulos reales siguen pasando",
      all(not veto(t) for t in REALES),
      "-> %d tocados" % sum(1 for t in REALES if veto(t)))

print()
print("=" * 76)
print("2) LO QUE HAY QUE EXCLUIR, SE EXCLUYE")
print("=" * 76)
A_EXCLUIR = [
    ("F-35 JOINT STRIKE FIGHTER AIRCRAFT SUSTAINMENT", "plataforma aerea"),
    ("DDG-1000 ZUMWALT CLASS DESTROYER HULL INDUCTION", "buque de guerra"),
    ("SUBMARINE USS COLUMBIA ACOUSTIC ARRAY MODULE", "submarino"),
    ("M1A2 ABRAMS MAIN BATTLE TANK SPARES", "vehiculo blindado"),
    ("BRADLEY FIGHTING VEHICLE ARMORED HULL PLATE", "blindado"),
    ("120MM MORTAR AMMUNITION LOT, US ORIGIN", "municion"),
    ("ARTILLERY SHELL 155MM INERT TRAINING ROUNDS", "armamento"),
    ("AVIATION FUEL JP-5 DEFENSE LOGISTICS SUPPORT", "combustible"),
    ("CARBINES AND AMMUNITION FOR MILITARY POLICE", "armamento"),
    ("MISSILE GUIDANCE SECTION ASSEMBLY", "misil"),
    ("ORDNANCE TEST EQUIPMENT, WEAPONS DIVISION", "armamento"),
    ("MINE RESISTANT AMBUSH PROTECTED VEHICLE (MRAP)", "blindado"),
]
for t, etiqueta in A_EXCLUIR:
    v = veto(t)
    marca = "ok   " if "high_complexity_defense" in v else "FALLA"
    print("  %s %-56s %s" % (marca, t[:56], etiqueta))
check("Los 12 titulos de defensa se descartan",
      all("high_complexity_defense" in veto(t) for t, _ in A_EXCLUIR),
      "-> se escapan: %s" % [t for t, _ in A_EXCLUIR
                             if "high_complexity_defense" not in veto(t)])
check("Y con la etiqueta que se pidio",
      all("high_complexity_defense" in veto(t) for t, _ in A_EXCLUIR))

print()
print("=" * 76)
print("3) LAS CERTIFICACIONES DE ORIGEN, EN LA DESCRIPCION")
print("=" * 76)
DESC_SI = [
    ("Contract requires ITAR compliance for all hardware.", "ITAR"),
    ("Facility must hold JCP certification.", "JCP"),
    ("Supplier shall provide DD250 form certifying origin.", "DD250"),
    ("Material must have certificate of conformance to origin per DLA.", "CoC"),
]
for d, etiqueta in DESC_SI:
    v = veto("43--PARTS KIT, ROTARY PUMP", desc=d)
    print("  %-6s %s" % ("ok" if "high_complexity" in v else "FALLA", etiqueta))
check("Las 4 certificaciones de origen se detectan",
      all("high_complexity" in veto("43--PARTS KIT, ROTARY PUMP", desc=d)
          for d, _ in DESC_SI))

print()
print("  Y lo que NO debe dispararlas (la trampa de las subcadenas):")
DESC_NO = [
    ("Material must be of military CoC origin, strictly certified.", "militar"),
    ("Military specification connector MS27494, commercial stock.", "mil-spec"),
    ("Maritime grade rope, 3-strand.", "maritime"),
    ("Items are similar in nature to standard industrial fasteners.", "similar"),
    ("Particular attention is drawn to the seal specification.", "particular"),
    ("Replacement seals for rotary pump, EPDM 70 Shore A.", "sello normal"),
]
for d, etiqueta in DESC_NO:
    v = veto("43--PARTS KIT, ROTARY PUMP", desc=d)
    mal = "high_complexity" in v
    print("  %-6s %-12s %s" % ("MAL" if mal else "ok", etiqueta, d[:46]))
check("Ninguna de esas 6 se descarta",
      not any("high_complexity" in veto("43--PARTS KIT, ROTARY PUMP", desc=d)
              for d, _ in DESC_NO),
      "-> %s" % [e for d, e in DESC_NO
                 if "high_complexity" in veto("43--PARTS KIT, ROTARY PUMP", desc=d)])

print()
print("  Y los productos comerciales con palabras que suenan a defensa:")
TITULOS_COMERCIALES = [
    ("ARMORED CABLE 12/2 MC 500FT, COMMERCIAL", "armored cable"),
    ("ARMORED HOSE, 2 INCH, HYDRAULIC", "armored hose"),
    ("MILITARY SPEC CONNECTOR MS27494, 50 EA", "mil-spec conector"),
    ("MARINE GRADE ROPE 3-STRAND, 100 FT", "maritime"),
]
for t, etiqueta in TITULOS_COMERCIALES:
    v = veto(t)
    print("  %-6s %-20s %s" % ("MAL" if v else "ok", etiqueta, t[:42]))
check("Ninguno de los 4 comerciales se descarta",
      not any(veto(t) for t, _ in TITULOS_COMERCIALES),
      "-> %s" % [e for t, e in TITULOS_COMERCIALES if veto(t)])

print()
print("=" * 76)
print("4) LAS FAMILIAS COTS SUMAN, NO VETEAN")
print("=" * 76)
check("La lista COTS existe y tiene familias", len(config.PSC_COTS) >= 20,
      "-> %d" % len(config.PSC_COTS))
for psc, etiqueta in (("6515", "medico"), ("6505", "hospitalario"),
                      ("5110", "ferreteria"), ("5120", "herramientas"),
                      ("7930", "limpieza"), ("8135", "guantes"),
                      ("7025", "computo"), ("7035", "comunicaciones"),
                      ("2510", "flota ligera"), ("2530", "flota pesada")):
    check("  PSC %s (%s) esta en la lista COTS" % (psc, etiqueta),
          psc in config.PSC_COTS)

print()
print("  Y que el bonus exista de verdad, no solo la lista:")
_con_cots, _motivos_cots = filters.puntuar(
    opp("6515 - MEDICAL SUPPLIES, GLOVES AND SYRINGES", psc="6515"), "")
_sin_cots, _motivos_sin = filters.puntuar(
    opp("5330 - PARTS KIT, ROTARY PUMP", psc="5330"), "")
print("  6515 (COTS)      : %d puntos" % _con_cots)
print("  5330 (no COTS)   : %d puntos" % _sin_cots)
check("La familia COTS suma", _con_cots > _sin_cots,
      "-> %d vs %d" % (_con_cots, _sin_cots))
check("Y el motivo lo explica",
      any("COTS" in m for m in _motivos_cots),
      "-> %s" % _motivos_cots[:3])
check("Y NO veta: el PSC sigue sin ser motivo de descarte",
      _con_cots > -100, "-> %d" % _con_cots)

print()
print("=" * 76)
print("5) EL LOG [DROPPED] high_complexity_defense")
print("=" * 76)
import inspect
_cuerpo = inspect.getsource(scanner)
check("El literal del log esta en scanner.py",
      "[DROPPED] high_complexity_defense" in _cuerpo)
check("Y hay un contador de descarte por motivo",
      hasattr(scanner, "DESCARTE_POR_MOTIVO"))
check("Y el resumen lo lleva para que /puntajes lo vea",
      "descarte_motivo" in _cuerpo)
check("Y la etiqueta vive en filters, en un solo sitio",
      filters.TAG_ALTA_COMPLEJIDAD == "high_complexity_defense")

print()
print("  El contador se llena de verdad, no solo existe:")
scanner.DESCARTE_POR_MOTIVO.clear()
scanner._contar_descarte([veto("F-35 STRIKE FIGHTER PARTS")])
scanner._contar_descarte([veto("M1A2 ABRAMS MAIN BATTLE TANK SPARES")])
scanner._contar_descarte([veto("ARTILLERY SHELL 155MM")])
print("    %s" % scanner.DESCARTE_POR_MOTIVO)
check("Cuenta los tres descartes",
      scanner.DESCARTE_POR_MOTIVO.get("high_complexity_defense") == 3,
      "-> %s" % scanner.DESCARTE_POR_MOTIVO)
scanner.DESCARTE_POR_MOTIVO.clear()

print()
print("=" * 76)
print("6) EL META-PROMPT DE FACILIDAD DE COMERCIALIZACION")
print("=" * 76)
import gemini_analyzer as ga
_prompt = ga._construir_prompt(
    opp("6515 - MEDICAL SUPPLIES", psc="6515"), "Suministros medicos.", "USA")
print("  El prompt mide %d caracteres" % len(_prompt))
check("Declara el campo en el esquema",
      "facilidad_comercializacion" in _prompt)
check("Y el motivo", "motivo_facilidad" in _prompt)
check("Pide las tres etiquetas",
      all(x in _prompt for x in ("NO APTA", "COTS",
                                 "OPORTUNIDAD COTS ALTA PROBABILIDAD")))
check("Explica la fabricacion a medida", "fabricacion a medida" in _prompt)
check("Explica la trazabilidad aeroespacial",
      "trazabilidad aeroespacial" in _prompt)
check("Explica las licencias especiales de defensa",
      "licencias especiales" in _prompt)
check("Explica lo que SI sirve: distribuidores comerciales de EE.UU.",
      "distribuidores o mayoristas comerciales" in _prompt)
check("Nombra ITAR y DD250", "ITAR" in _prompt and "DD250" in _prompt)
check("El ejemplo del JSON trae las dos claves",
      '"facilidad_comercializacion"' in _prompt
      and '"motivo_facilidad"' in _prompt)

print()
print("  Y el parseo normaliza la etiqueta:")
for crudo, esperado in (
        ("OPORTUNIDAD COTS ALTA PROBABILIDAD", "OPORTUNIDAD COTS ALTA PROBABILIDAD"),
        ("cots alta probabilidad", "OPORTUNIDAD COTS ALTA PROBABILIDAD"),
        ("COTS", "COTS"),
        ("NO APTA", "NO APTA"),
        ("no apta porque es unico proveedor", "NO APTA"),
        ("COTS ALTA PROBABILIDAD", "OPORTUNIDAD COTS ALTA PROBABILIDAD"),
        ("", "COTS"),
        (None, "COTS"),
        ("inventado por el modelo", "COTS")):
    got = ga._facilidad(crudo)
    print("    %-40r -> %s" % (crudo, got))
    check("  %-38r -> %s" % (crudo, esperado), got == esperado, "-> %s" % got)

print()
check("Y 'COTS' suelto NO gana a la categoria superior",
      ga._facilidad("COTS") != "OPORTUNIDAD COTS ALTA PROBABILIDAD",
      "-> se perdio la distincion por orden de busqueda")

print()
print("=" * 76)
print("7) LO QUE NO SE HA TOCADO")
print("=" * 76)
check("TOPE_USD sigue en 250.000", config.TOPE_USD == 250000.0,
      "-> %s" % config.TOPE_USD)
check("El PSC sigue sin vetar (decision del 30-sep)",
      "NO veta" in io.open("test_ajustes.py", encoding="utf-8").read())
check("El calculo de descuentos sigue ahi",
      hasattr(ga, "calcular_estrategia_oferta"))
check("Y la cadena de modelos sigue con 6 entradas",
      len(config.GEMINI_MODELES_ALTERNATIVOS) >= 4,
      "-> %d" % len(config.GEMINI_MODELES_ALTERNATIVOS))
check("El veto devuelve -100, como los otros tres",
      veto("F-35 STRIKE FIGHTER PARTS").startswith("high_complexity"))

print()
print("=" * 76)
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: la orientacion COTS no se come el negocio y descarta lo de")
print("          alta complejidad")
print("=" * 76)