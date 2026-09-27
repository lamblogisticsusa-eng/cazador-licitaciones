"""
test_ficha.py - La ficha completa: capital, margenes y enlaces de distribuidores.
No toca la red. Comprueba que los enlaces generados sean validos y que la
tarjeta tenga todo lo que necesita el usuario para ofertar.
"""
import os
import re
import sys

os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("SAM_API_KEY", "x")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


import config
import distribuidores
import telegram_notify as tn

FICHA = {
    "notice_id": "60aa8e3f9222455eab2e94bf4be98e70",
    "title": "USNS MERCY (T-AH 19) MAIN MACHINERY ROOM FIRE PUMP VALVES",
    "solicitation": "N0010426QFF21",
    "agencia": "DEPT OF DEFENSE.DEPT OF THE NAVY.NAVSUP",
    "naics": "332911", "psc": "5330", "set_aside": "SBA",
    "producto": "Valvulas de compuerta de bronce para bomba contraincendios naval",
    "especificacion_tecnica_clave": "ASTM B584, 150 psi, PN20",
    "cantidad_estimada": "1 LOTE (conjunto de reemplazo)",
    "lugar_entrega": "Norfolk / Virginia / UNITED STATES",
    "limite": "2026-11-04T18:00:00-05:00",
    "posted": "2026-09-25", "sin_descripcion": False,
    "valor_contrato_usd": 48000.0,
    "costo_proveedor_usd": 32000.0,
    "capital_necesario_usd": 36800.0,
    "precio_unitario_referencia_usd": 6400.0,
    "precio_unitario_sugerido_usd": 8000.0,
    "ganancia_neta_usd": 16000.0,
    "margen_porcentaje": 33.3,
    "precio_oferta_sugerido_usd": 45000.0,
    "estrategia_oferta": "Ofertar 8% bajo estimado con entrega en 45 dias.",
    "margen_por_distribuidor": (
        "Distribuidor de catalogo grande 15-20%; mayorista 20-25%; "
        "fabricante directo 25-35%. Mejor margen en el fabricante."
    ),
    "busquedas_distribuidores": [
        "bronze gate valve marine supplier usa",
        "naval fire pump valve distributor",
    ],
    "nivel_riesgo": "medio",
    "preguntas_criticas": ["¿Aceptan marcas equivalentes?", "¿Cage code?"],
    "observaciones": "",
    "contacto": "Jane Doe | jane.doe@navy.mil",
    "ui_link": "https://sam.gov/workspace/contract/opp/60aa8e3f/view",
}

html = tn.formatear_analisis(FICHA)

print("=" * 70)
print("1) LO QUE PIDE EL USUARIO, UNO POR UNO")
print("=" * 70)
check("Enlace de SAM.gov", 'href="https://sam.gov/workspace/contract' in html)
check("Descripcion de la licitacion", "Valvulas de compuerta" in html)
check("  y la especificacion tecnica", "ASTM B584" in html)
check("Cantidad", "LOTE" in html)
check("Valor del contrato", "$48,000.00" in html)
check("Dinero para comprar", "$32,000.00" in html)
check("Dinero necesario para ofertar", "$36,800.00" in html)
check("  con la nota del colchon", "colch" in html.lower())
check("Ganancia neta", "$16,000.00" in html)
check("Margen porcentual", "33.3%" in html)
check("Precio unitario de catalogo", "$6,400.00" in html)
check("Precio unitario a ofertar", "$8,000.00" in html)
check("Monto a ofertar", "$45,000.00" in html)
check("Contacto", "jane.doe@navy.mil" in html)
check("Limite para ofertar", "2026-11-04" in html)
print()

print("=" * 70)
print("2) LOS DISTRIBUIDORES")
print("=" * 70)
enlaces = re.findall(r'href="(https://www\.google\.com/search\?q=[^"]+)"', html)
check("Hay enlaces de distribuidores", len(enlaces) >= 3, f"-> {len(enlaces)}")
check("Todos son de Google (los que respondieron 200)", all(
    e.startswith("https://www.google.com/search?q=") for e in enlaces
))
from urllib.parse import unquote_plus
textos = [unquote_plus(e.split("q=", 1)[1]) for e in enlaces]
print("       Consultas generadas:")
for t in textos:
    print(f"         · {t}")
check("Todas filtran por USA", all("usa" in t.lower() or "united states" in t.lower()
                                or "site:" in t.lower() for t in textos))
check("Usan site: para trayectoria a un distribuidor",
      sum(1 for t in textos if "site:" in t) >= 2,
      f"-> {sum(1 for t in textos if 'site:' in t)}")
check("Ninguna URL tiene espacios sin codificar",
      all(" " not in e for e in enlaces))
check("Ninguna URL tiene caracteres raros",
      all(re.match(r"^https://www\.google\.com/search\?q=[A-Za-z0-9%+\-._~:/?\[\]@!$&'()*;,]*$", e)
          for e in enlaces))
print()

print("=" * 70)
print("3) RANGO DE MARGEN 15% A 35%")
print("=" * 70)
check("La ficha explica donde esta el mejor margen",
      "margen" in html.lower() and "fabricante" in html.lower())
check("Menciona el rango 15%", "15" in html)
check("Menciona 35%", "35" in html)
print()

print("=" * 70)
print("4) LA TARJETA ES VALIDA PARA TELEGRAM")
print("=" * 70)
for tag in ("b", "code", "i"):
    check(f"<{tag}> balanceado", html.count(f"<{tag}>") == html.count(f"</{tag}>"),
          f"-> {html.count(f'<{tag}>')} vs {html.count(f'</{tag}>')}")
check("<a> balanceado", html.count("<a ") == html.count("</a>"))
check("Cabe en 4096", len(html) <= 4096, f"-> {len(html)} chars")
check("Usa el cierre de Kyomoto", "Kyomoto passandote" in html)
print()

print("=" * 70)
print("5) CASOS BORDEOS DE distribuidores.para_oportunidad")
print("=" * 70)
for nombre, terminos, producto, lugar in (
    ("Sin terminos", [], "", ""),
    ("Solo producto", [], "válvulas de bronce", "USA"),
    ("Con basura", ["<script>x</script>", "a" * 200], "válvulas", "USA"),
    ("Lugar raro", ["valve"], "valve", "No especificado"),
    ("Ciudad utilis", ["valve"], "valve", "Norfolk / Virginia / UNITED STATES"),
):
    try:
        d = distribuidores.para_oportunidad(terminos, producto, lugar)
        ok = d["principal"][1].startswith("https://www.google.com/search?q=") and len(d["sitios"]) >= 3
        check(nombre, ok, f"-> {d['principal'][1][:70]}")
        check(f"  {nombre}: sin HTML crudo",
              "<script" not in d["principal"][1] and "<" not in d["principal"][1])
    except Exception as e:
        check(nombre, False, f"-> {type(e).__name__}: {e}")
print()

print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: la ficha tiene todo lo que necesitas para ofertar")
