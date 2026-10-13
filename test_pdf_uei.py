"""
test_pdf_uei.py - UEI y NCAGE, y el patron de rango "2-20 foot".

LO QUE SE COMPRUEBA

  1) UEI y NCAGE en la cabecera, en el pie y en la hoja de cotizacion
  2) El patron de rango: "2-20 foot Containers" -> 2 EA
  3) Que los tramposos NO disparen el patron
  4) Que el titulo NO se parta ("20-FOOT CONTAINER" no puede quedar en
     "FOOT CONTAINER")
  5) Que W519TC27QA012 NO cambie: 100 BG, no 2 EA
  6) El PDF de las dos licitaciones

POR QUE EL UEI ES OBLIGATORIO Y NO DECORATIVO

En un Small Business Set-Aside el oficial de contratacion comprueba el UEI en
SAM.gov para ver que la empresa existe y que su NAICS encaja con el del
contrato. Un documento sin UEI no identifica a nadie, por mas que tenga el
nombre, la direccion y el telefono correctos.

EL PATRON DE RANGO

"2-20 foot Containers" es la forma estandar de EE.UU. para decir "2 piezas,
cada una de 20 pies". El numero ANTES del guion es la cantidad.

Lo que protege de los tramposos no es el ancla sino la palabra de MEDIDA: es lo
que hace que "MIL-STD-5-10", "2024-2025" y "Paragraph 5-10" no casen. Y el
patron exige DOS numeros separados por guion, que es lo que impide que
"20-FOOT CONTAINER" (un numero y un guion) dispare.
"""
import io
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_uei.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "CLAVE-SIMULADA"
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


import pdf_generator as pdfg
import store

store.init_db()

UEI = "NEXGNXGF5E38"
NCAGE = "STYG2"

AVISO_2 = {
    "noticeId": "eee555fff666ggg777hhh888",
    "solicitationNumber": "W912ES27QA004",
    "title": "20-FOOT CONTAINER WITH GANTRY BEAM AND CHAIN LIFT",
    "fullParentPathName": "Department of the Army",
    "postedDate": "2026-10-08 09:00:00",
    "responseDeadLine": "2026-11-30T17:00:00-05:00",
    "placeOfPerformance": {"city": "Fort Lewis", "state": "Washington"},
}
DESC_2 = ("This solicitation is for 2-20 foot Containers with Gantry Beam and "
          "Chain Lift. Containers shall be new, 20 feet long, conforming to "
          "MIL-STD-1333. Drawing 3002345 applies.")

AVISO_1 = {
    "noticeId": "aaa111bbb222ccc333ddd444",
    "solicitationNumber": "W519TC27QA012",
    "title": "53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL",
    "fullParentPathName": "Department of the Navy",
    "postedDate": "2026-10-05 09:00:00",
    "responseDeadLine": "2026-11-20T17:00:00-05:00",
    "placeOfPerformance": {"city": "Groton", "state": "Connecticut"},
}
DESC_1 = ("AMD 0001 Narrative... This amendment is being issued to incorporate "
          "the solicitation number. Interested parties shall acknowledge receipt "
          "in writing. Line Item Number 0001: NSN 5330-01-586-4931, Cage 12345, "
          "Part Number M7A1-100, Quantity: 100 BG. MECHANICAL SEAL KIT, EPDM 70 "
          "SHORE A PER ASTM D2000, PER DRAWING 1110700278.")

print("=" * 76)
print("1) UEI Y NCAGE")
print("=" * 76)
check("El UEI es el de L.A.M.B.", pdfg.UEI == UEI, "-> %r" % pdfg.UEI)
check("Y el NCAGE", pdfg.NCAGE == NCAGE, "-> %r" % pdfg.NCAGE)

import quota
quota.init()
quota.quedan = lambda: 50

import sam_api


def _pdf(aviso, desc):
    _r = sam_api.obtener_descripcion
    sam_api.obtener_descripcion = lambda nid, opp=None, d=desc: d
    try:
        an = pdfg.desde_aviso(aviso)
        return pdfg.generar_po(pdfg.desde_analisis(
            an, {"nombre": "Radwell International"}))
    finally:
        sam_api.obtener_descripcion = _r


from pypdf import PdfReader


def _texto(pdf_bytes):
    return "\n".join(p.extract_text() or "" for p in
                     PdfReader(io.BytesIO(pdf_bytes)).pages)


b2 = _pdf(AVISO_2, DESC_2)
t2 = _texto(b2)
b1 = _pdf(AVISO_1, DESC_1)
t1 = _texto(b1)

for etiqueta, t in (("W912ES27QA004", t2), ("W519TC27QA012", t1)):
    print("  %s:" % etiqueta)
    for lo in (UEI, NCAGE, "UEI", "NCAGE"):
        print("    %-14s %s" % (lo, "si" if lo in t else "NO"))
    check("  %s: el UEI sale en el documento" % etiqueta, UEI in t)
    check("  %s: el NCAGE sale en el documento" % etiqueta, NCAGE in t)
    check("  %s: y salen rotulados, no sueltos" % etiqueta,
          ("UEI " + UEI) in " ".join(t.split())
          and ("NCAGE " + NCAGE) in " ".join(t.split()))
    check("  %s: en la hoja de cotizacion tambien" % etiqueta,
          "Issued under UEI" in t)

print()
print("  Y en el pie, que sale en TODAS las paginas:")
_pies = [l for l in t2.splitlines() if "pagina" in l.lower()]
print("    %r" % (_pies[0][:96] if _pies else "(no hay pie)"))
check("El pie lleva el UEI", bool(_pies) and UEI in _pies[0])
check("Y sigue llevando el telefono y el correo",
      bool(_pies) and "+1 (702) 516-6086" in _pies[0]
      and "lamblogisticsusa@gmail.com" in _pies[0])
check("Y la direccion sigue intacta",
      "1209 Mountain Rd. PL. NE, Ste. H" in t2
      and "Albuquerque, NM 87110" in t2)

print()
print("=" * 76)
print("2) EL PATRON DE RANGO")
print("=" * 76)
CASOS = [
    ("This solicitation is for 2-20 foot Containers with Gantry Beam", 2.0),
    ("2-20 foot Containers with Gantry Beam and Chain Lift", 2.0),
    ("4-10 inch mounting rings, zinc plated", 4.0),
    ("3-6 inch hex bolts ASTM A325", 3.0),
    ("2/20 foot Cargo Containers", 2.0),
    ("6-8 foot Wide Flange Beam", 6.0),
]
for texto, esperado in CASOS:
    r = pdfg._cantidad_de_rango(texto, libre=True)
    check("  %-56r -> %s" % (texto[:56], r), r == (esperado, True),
          "esperaba (%s, True)" % esperado)

print()
print("=" * 76)
print("3) LOS TRAMPOSOS NO DISPARAN EL PATRON")
print("=" * 76)
print("  Lo que protege aqui no es el ancla sino la palabra de MEDIDA.")
TRAMPOSOS = [
    ("20-FOOT CONTAINER WITH GANTRY BEAM", "un numero y un guion, no dos"),
    ("53--PARTS KIT,SEAL REPLACEMENT", "prefijo de clase de SAM.gov"),
    ("See MIL-STD-5-10 for requirements", "numero de norma"),
    ("Contract 2024-2025 for supplies", "rango de anos"),
    ("Paragraph 5-10 of the specification", "referencia de parrafo"),
    ("Drawing 3002345 applies", "numero de plano"),
    ("Line Item Number 0001: NSN 5330-01-586-4931, Quantity: 100 BG.",
     "cantidad normal, que la lee el parser de siempre"),
]
for texto, motivo in TRAMPOSOS:
    r = pdfg._cantidad_de_rango(texto, libre=True)
    check("  %-54r %s" % (texto[:54], motivo), r == (0.0, False), "-> %s" % (r,))

print()
print("=" * 76)
print("4) EL TITULO NO SE PARTE")
print("=" * 76)
print("  Con la regla anterior de quitar el prefijo con 1 o 2 guiones, el titulo")
print("  '20-FOOT CONTAINER WITH GANTRY BEAM' salia como 'FOOT CONTAINER WITH")
print("  GANTRY BEAM', que no es el nombre de nada.")
cab = pdfg._cabecera_producto("20-FOOT CONTAINER WITH GANTRY BEAM AND CHAIN LIFT",
                             "", "3002345")
print("    %s" % cab)
check("Conserva el '20-FOOT' del principio", cab.startswith("20-FOOT CONTAINER"),
      "-> %r" % cab[:40])
check("Y no sale 'FOOT CONTAINER' a secas", not cab.startswith("FOOT CONTAINER"))
cab53 = pdfg._cabecera_producto("53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL", "", "")
check("Y el prefijo de clase '53--' sigue quitandose",
      not cab53.startswith("53--") and cab53.startswith("PARTS KIT"),
      "-> %r" % cab53[:40])

print()
print("=" * 76)
print("5) W519TC27QA012 NO CAMBIA")
print("=" * 76)
print("  El patron de rango no puede tocar lo que ya funcionaba.")
bom1 = pdfg._bom_desde_aviso(AVISO_1, DESC_1)
print("    W519TC27QA012 -> cantidad=%s unidad=%s"
      % (bom1[0]["cantidad"], bom1[0]["unidad"]))
check("Sigue dando 100", bom1[0]["cantidad"] == 100.0,
      "-> %r" % bom1[0]["cantidad"])
check("Y BG, no EA", bom1[0]["unidad"] == "BG", "-> %r" % bom1[0]["unidad"])
check("Y no se ha colado 'AMD' en la celda",
      "AMD" not in bom1[0]["descripcion"]
      and "AMD" not in bom1[0]["especificacion"])

print()
print("=" * 76)
print("6) LOS DOS PDF, CON SUS CIFRAS")
print("=" * 76)
bom2 = pdfg._bom_desde_aviso(AVISO_2, DESC_2)
print("  W912ES27QA004 -> cantidad=%s unidad=%s"
      % (bom2[0]["cantidad"], bom2[0]["unidad"]))
print("    descripcion: %s" % bom2[0]["descripcion"][:76])
check("W912ES27QA004 da QTY 2", bom2[0]["cantidad"] == 2.0,
      "-> %r" % bom2[0]["cantidad"])
check("Y UNIT EA", bom2[0]["unidad"] == "EA", "-> %r" % bom2[0]["unidad"])
check("Y el titulo del contenedor sale limpio",
      "20-FOOT CONTAINER WITH GANTRY BEAM AND CHAIN LIFT" in bom2[0]["descripcion"],
      "-> %r" % bom2[0]["descripcion"][:60])

print()
celda2 = " ".join(t2.split())
_celda = _celda[_celda.index("ITEMS ORDERED"):_celda.index("BILL OF MATERIALS")]
print("  En el PDF de W912ES27QA004:")
for lo, debe in ((UEI, True), (NCAGE, True), ("20-FOOT CONTAINER", True),
                 ("CON", False), ("$0.00", False)):
    got = lo in _celda
    check("    %-24s %s" % (lo[:24], "si" if debe else "no"), got == debe)
check("    Y sale QTY 2 con UNIT EA",
      "2" in _celda and "EA" in _celda)
check("    Y la descripcion del contenedor, sin 'CON'",
      "Gantry Beam and Chain Lift" in _celda)

print()
print("=" * 76)
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
    except OSError:
        pass

if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: UEI y NCAGE en el papel, y el rango da 2 EA")
print("=" * 76)