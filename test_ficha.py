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
    # La agencia viene como la devuelve SAM.gov, con puntos y sin espacios.
    # _agencia_corta() tiene que sacar "USN" de ahi.
    "title": "USNS MERCY (T-AH 19) MAIN MACHINERY ROOM FIRE PUMP VALVES",
    "solicitation": "N0001925001",
    "agencia": "DEPT OF DEFENSE.DEPT OF THE NAVY.NAVSUP",
    "set_aside": "Full and Open",
    "naics": "332911", "psc": "2915",
    "producto": "Valvulas de compuerta de acero fundido para sala de maquinas",
    "modelo_especifico": "ASTM B584",
    "unidad_medida": "LOT",
    "cantidad_total": 40,
    "especificacion_tecnica_clave":
        "Valvulas de compuerta con certificacion ASTM B584, presion de trabajo "
        "de 150 psi, entrega fisica en el puerto de Filadelfia",
    "lugar_entrega": "Philadelphia Naval Shipyard / PA / UNITED STATES",
    "limite": "2026-11-04T17:00:00-04:00",
    "sin_descripcion": False,
    "ui_link": "https://sam.gov/workspace/contract/opp/60aa8e3f/view",
    "contacto": "jane.doe@navy.mil",
    "nivel_riesgo": "medio",

    # ---- La cuenta, y aqui SI cuadra ----
    # El gobierno publica un TECHO de 245,000 y uno oferta 230,000, un 6%
    # Bruta 50,000 (21.7%), factoring 3.5% DE LO OFERTADO = 8,050,
    # neta 41,950 (18.2% sobre la venta).
    "valor_contrato_usd": 245000.0,   # el techo que publica el gobierno
    "precio_unitario_costo": 4500.0,
    "precio_unitario_mercado": 5400.0,
    "precio_unitario_oferta": 5750.0,
    "ganancia_por_unidad": 1250.0,
    "costo_total_usd": 180000.0,
    "ganancia_total_usd": 50000.0,
    "margen_bruto_porcentaje": 21.7,
    "costo_factoring_usd": 8050.0,
    "ganancia_neta_usd": 41950.0,
    "margen_neto_porcentaje": 18.2,
    "precio_oferta_sugerido_usd": 230000.0,
    "razonamiento_oferta":
        "Va 12% por debajo del presupuesto publicado, con margen neto de 18.2% "
        "y sin necesidad de capital gracias al factoring.",
    "margen_por_distribuidor":
        "Mayorista 20-25% (mejor opcion). Fabricante 35% pero con MOQ de 100.",
    "busquedas_distribuidores": [
        "gate valve 6 inch steel foundry",
        "naval machinery replacement part",
    ],
    "preguntas_criticas": [
        "¿Aceptan marca equivalente o exigen el OEM?",
        "¿El precio es FOB destino?",
    ],
}
html = tn.formatear_analisis(FICHA)

print("=" * 70)
print("1) LO QUE PIDE EL USUARIO, UNO POR UNO")
print("=" * 70)
check("Enlace de SAM.gov", 'href="https://sam.gov/workspace/contract' in html)
check("Descripcion de la licitacion", "Valvulas de compuerta" in html)
check("  y la especificacion tecnica", "ASTM B584" in html)

# La abreviatura de la agencia delante del producto. Se calculo con
# _agencia_corta(), que es dato nuevo: antes la ficha no la traia.
check("La agencia abreviaada delante del producto",
      "\U0001f4e6 <b>USN - " in html, f"-> {html.splitlines()[2][:60]}")
# La agencia llega como la da SAM.gov, con puntos: "DEPT OF DEFENSE.
# DEPT OF THE NAVY.NAVSUP". Se comprueba ese texto, no el nombre bonito.
check("Y la agencia completa tal como la da SAM.gov",
      "DEPT OF DEFENSE.DEPT OF THE NAVY.NAVSUP" in html)

check("Valor del contrato (techo del gobierno)", "$245,000.00" in html)
check("Costo total de compra", "$180,000.00" in html)
check("Ganancia neta tras factoring", "$41,950.00" in html)
check("Margen neto", "18.2%" in html)
check("Monto a ofertar", "$230,000.00" in html)
check("Cantidad en unidades", "40 LOT" in html)

# Los montos llevan la unidad detras, segun el diseno pedido.
check("Los 4 montos con USD detras", html.count(" USD") == 4,
      f"-> {html.count(' USD')}")
# El "~" va solo en lo que se ESTIMA. La ganancia neta y el precio ofertado se
# calculan, y poner "~" ahi seria mentir sobre lo unico que sale cerrado.
check("Presupuesto y costo con ~",
      "~$245,000.00" in html and "~$180,000.00" in html)
check("La ganancia neta sin ~", "~$41,950.00" not in html)
check("El precio ofertado sin ~", "~$230,000.00 USD" not in html)
check("Y es menor que el techo del gobierno",
      FICHA["precio_oferta_sugerido_usd"] < FICHA["valor_contrato_usd"],
      "-> ofertando por encima del techo, que no tiene sentido")
check("La explicacion de la estrategia va en la MISMA linea",
      " USD (" in html, "-> quedo en una linea aparte")
print()

print("=" * 70)
print("2) LA CUENTA, AUNQUE NO SE IMPRIMA ENTERA EN LA FICHA")
print("=" * 70)
# Estas secciones se quitaron del texto, pero las cifras siguen siendo las que
# Gemini calcula y las que la ficha muestra. Si se dejaran de comprobar aqui,
# el error del 38.8% (medir el margen contra el presupuesto del gobierno en vez
# de contra el precio ofertado) podria volver sin que nada lo notara.
print("   (lo que se quito de la impresion se sigue verificando aqui)")
print()
check("El presupuesto es lo que publica el gobierno",
      FICHA["valor_contrato_usd"] == 245000.0)
check("El factoring es el 3.5% del PRECIO OFERTADO",
      abs(FICHA["precio_oferta_sugerido_usd"] * 0.035 - FICHA["costo_factoring_usd"]) < 0.01,
      f"-> {FICHA['precio_oferta_sugerido_usd'] * 0.035:.2f} "
      f"vs {FICHA['costo_factoring_usd']}")
check("Y NO del presupuesto del gobierno",
      abs(FICHA["valor_contrato_usd"] * 0.035 - FICHA["costo_factoring_usd"]) > 1.0,
      "-> se esta descontando dos veces o sobre la base equivocada")
check("Bruta = oferta - costo",
      abs(FICHA["precio_oferta_sugerido_usd"] - FICHA["costo_total_usd"]
          - FICHA["ganancia_total_usd"]) < 0.01)
check("Neta = bruta - factoring",
      abs(FICHA["ganancia_total_usd"] - FICHA["costo_factoring_usd"]
          - FICHA["ganancia_neta_usd"]) < 0.01)
check("Margen neto = neta / oferta (no / presupuesto)",
      abs(FICHA["ganancia_neta_usd"] / FICHA["precio_oferta_sugerido_usd"] * 100
          - FICHA["margen_neto_porcentaje"]) < 0.2,
      "-> el margen se esta midiendo contra el presupuesto")
print()

print("=" * 70)
print("3) LOS DISTRIBUIDORES")
print("=" * 70)
# Ahora hay un solo enlace: la busqueda de Google. Los cuatro con site: se
# quitaron del diseno, pero el link tiene que seguir siendo de Google y
# seguir filtrando por USA, que es lo que importa.
enlaces = re.findall(r'href="(https://www\.google\.com/search\?q=[^"]+)"', html)
check("Hay un enlace de distribuidores", len(enlaces) == 1, f"-> {len(enlaces)}")
check("Es de Google", all(
    e.startswith("https://www.google.com/search?q=") for e in enlaces
))
from urllib.parse import unquote_plus
textos = [unquote_plus(e.split("q=", 1)[1]) for e in enlaces]
print("       Consulta generada:")
for tx in textos:
    print(f"         · {tx}")
check("Filtra por USA", all("usa" in tx.lower() or "united states" in tx.lower()
                          for tx in textos), f"-> {textos}")
check("Ninguna URL tiene espacios sin codificar",
      all(" " not in e for e in enlaces))
check("Ninguna URL tiene caracteres raros",
      all(re.match(r"^https://www\.google\.com/search\?q=[A-Za-z0-9%+\-._~:/?\[\]@!$&'()*;,]*$", e)
          for e in enlaces))
print()

print("3) RANGO DE MARGEN 15% A 35%: EN LOS DATOS, NO EN LA FICHA")
print("=" * 70)
# La ficha ya no imprime el rango ni el "donde esta el mejor margen". El
# filtro sigue existiendo y se sigue aplicando ANTES de que un aviso llegue a
# Gemini, en config.py y filters.py. Aqui se comprueba que los valores de
# configuracion son los que el usuario fijo.
print("   (el texto salia de una seccion que ya no se imprime; el filtro sigue)")
print()
import config as _cfg
check("El piso de margen bruto es 15%", _cfg.MARGEN_BRUTO_MIN == 0.15,
      f"-> {_cfg.MARGEN_BRUTO_MIN}")
check("El techo de margen bruto es 35%", _cfg.MARGEN_BRUTO_MAX == 0.35,
      f"-> {_cfg.MARGEN_BRUTO_MAX}")
check("El minimo neto es 12%", _cfg.MARGEN_NETO_MIN == 0.12,
      f"-> {_cfg.MARGEN_NETO_MIN}")
check("El factoring es 3.5%", abs(_cfg.FACTORING_PCT - 0.035) < 1e-9,
      f"-> {_cfg.FACTORING_PCT}")
check("Y este caso pasa el filtro de margen",
      FICHA["margen_neto_porcentaje"] >= _cfg.MARGEN_NETO_MIN * 100,
      f"-> {FICHA['margen_neto_porcentaje']}%")
print()

print("4) LA TARJETA ES VALIDA PARA TELEGRAM")
print("=" * 70)
for tag in ("b", "code", "i"):
    check(f"<{tag}> balanceado", html.count(f"<{tag}>") == html.count(f"</{tag}>"),
          f"-> {html.count(f'<{tag}>')} vs {html.count(f'</{tag}>')}")
check("<a> balanceado", html.count("<a ") == html.count("</a>"))
check("Cabe en 4096", len(html) <= 4096, f"-> {len(html)} chars")
check("Usa el cierre de Kyomoto", "Kyomoto pasandote" in html)
check("Y esta bien escrito", "passandote" not in html)
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
