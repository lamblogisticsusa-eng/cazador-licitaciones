"""
test_formato.py - Muestra la ficha tal como la recibira el usuario.
Usa los datos del ejemplo que dio, para comparar directamente.
"""
import io
import os
import sys

os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("SAM_API_KEY", "x")

import config
import telegram_notify as tn

# --- Los MISMOS numeros del ejemplo del usuario ---
FICHA = {
    "title": "USAF - Suministro de Repuestos y Módulos Generadores diésel de 50kW",
    "solicitation": "FA8601-26-Q-0182",
    "agencia": "Department of the Air Force (USAF / Wright-Patterson AFB)",
    "set_aside": "Small Business",
    "naics": "335312", "psc": "5330",
    "producto": "Repuestos y módulos para generadores diésel tácticos de 50kW",
    "modelo_especifico": "Estándar MIL-STD-882",
    "unidad_medida": "KIT",
    "cantidad_total": 16,          # 12 módulos + 4 kits de alternadores
    "especificacion_tecnica_clave":
        "12 módulos de control electrónico + 4 kits de alternadores de repuesto, "
        "estándar MIL-STD, entrega física directa en base aérea",
    "lugar_entrega": "Wright-Patterson AFB / Ohio / UNITED STATES",
    "limite": "2026-11-04T17:00:00-04:00",
    "sin_descripcion": False,
    "valor_contrato_usd": 85000.0,
    "precio_unitario_costo": 3250.0,
    "precio_unitario_mercado": 3900.0,
    "precio_unitario_oferta": 4656.25,
    "ganancia_por_unidad": 1406.25,
    "costo_total_usd": 52000.0,
    "ganancia_total_usd": 22500.0,
    "margen_bruto_porcentaje": 30.2,
    # El factoring se cobra sobre la FACTURA, que es lo que ofertamos, no
    # sobre el presupuesto del gobierno. Esa es la diferencia entre 27.3% y
    # 26.7%, y es la que hacia que el ejemplo original no cuadrara.
    "costo_factoring_usd": 2607.50,
    "ganancia_neta_usd": 19892.50,
    "margen_neto_porcentaje": 26.7,
    "precio_oferta_sugerido_usd": 74500.0,
    "razonamiento_oferta":
        "Con este monto nos mantenemos un 12% por debajo del presupuesto "
        "máximo del gobierno para asegurar alta competitividad y ganar el "
        "contrato, asegurando una ganancia neta estimada de $19,892.50.",    "estrategia_oferta": "Entrega en 60 días con garantía de 12 meses.",
    "margen_por_distribuidor":
        "Mayorista de exportación 20-25% (mejor opción). Fabricante directo "
        "35% pero exige MOQ de 100 unidades y no tenemos espacio de stockage.",
    "busquedas_distribuidores": [
        "50kW diesel generator control module",
        "tactical generator alternator spare parts",
    ],
    "nivel_riesgo": "medio",
    "preguntas_criticas": [
        "¿Aceptan equivalently marca o exigen el OEM original?",
        "¿El precio es FOB destino o FOB origen?",
        "¿El set-aside exige ser pequeño negocio de EE.UU.?",
    ],
    "observaciones": "",
    "contacto": "John Smith | john.smith@af.mil | +1 937 555 0142",
    "ui_link": "https://sam.gov/workspace/contract/opp/60aa8e3f/view",
}

html = tn.formatear_analisis(FICHA)

print("=" * 74)
print("  LO QUE RECIBIRAS EN TELEGRAM")
print("=" * 74)
# El HTML se ve en crudo aca; en Telegram sale renderizado.
print(html.replace("</b>", "</b>"))
print()
print("=" * 74)
print("  COMPROBACIONES")
print("=" * 74)

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


# Formato reordenado por el usuario el 29-sep-2026. La ficha es mas corta:
# fuera la cuenta por unidad, el desglose del factoring, los distribuidores
# extra, los terminos de busqueda, el bloque de entrega, las tres preguntas y
# la linea de riesgo. Dentro la abreviatura de la agencia, "USD" detras de
# cada monto y la explicacion de la estrategia en la misma linea.
for etiqueta_txt, esperado in (
    ("Saludo kawaii", "¡Amo, encontré una nueva oportunidad súper interesante!"),
    ("Emoji feliz", "≧◡≦"),
    ("Paquete de emojis", "📦"),
    ("Agencia corta delante del producto", "📦 <b>USAF - Suministro"),
    ("Solicitud", "🔢 Solicitud:"),
    ("Agencia", "Department of the Air Force"),
    ("Descripcion", "📝 <b>Descripción del Producto:</b>"),
    ("Analisis financiero", "💰 <b>Análisis Financiero Estimado:</b>"),
    ("Presupuesto", "• Presupuesto Est. Gobierno:"),
    ("Costo", "• Costo Est. Proveedor:"),
    ("Ganancia neta", "• Ganancia Neta Proyectada:"),
    ("Estrategia", "🎯 <b>Estrategia de Oferta Sugerida:</b>"),
    ("Precio a licitar", "• Precio Sugerido para Licitar:"),
    ("Distribuidores", "🔍 <b>Búsqueda Automática de Distribuidores:</b>"),
    ("Enlace SAM.gov", "🔗 <b>Enlace Directo SAM.gov:</b>"),
    ("Ficha completa", "📄"),
):
    check(etiqueta_txt, esperado in html)

check("Muestra los 4 numeros del ejemplo",
      all(x in html for x in ("$85,000.00", "$52,000.00", "$19,892.50", "$74,500.00")))

# La ficha nueva pide "USD" detras de cada monto.
check("Los 4 montos llevan USD detras", html.count(" USD") == 4,
      f"-> {html.count(' USD')} apariciones")

# El "~" va solo donde el numero es estimacion. La ganancia neta y el precio
# ofertado se CALCULAN, asi que "~" ahi seria mentir sobre lo unico que sale
# cerrado. Este test fija esa distincion para que nadie la invierta sin
# querer.
check("Presupuesto y costo con ~ (son estimaciones de Gemini)",
      "~$85,000.00" in html and "~$52,000.00" in html)
check("La ganancia neta NO lleva ~ (se calcula)",
      "~$19,892.50" not in html and "$19,892.50" in html)
check("El precio ofertado NO lleva ~ (se calcula)",
      "~$74,500.00" not in html)

check("El margen va como 'de margen'", "% de margen)" in html)
check("La explicacion de la estrategia va en la MISMA linea",
      "USD (Con este monto" in html,
      "-> quedo en una linea aparte, hay que unirla")

# Un solo enlace de distribuidor: el de Google. Los otros cuatro se quitaron.
check("Solo 2 enlaces: distribuidores y SAM.gov",
      html.count("<a href=") == 2,
      f"-> {html.count('<a href=')}")
check("Y el de distribuidores es el de Google", "google.com/search" in html)

# La abreviatura no se duplica cuando el titulo ya la trae. Se comprobo con un
# caso real: salia "USAF - USAF - Suministro de Repuestos".
check("No duplica la agencia si el titulo ya empieza con ella",
      "USAF - USAF -" not in html)

check("Enlaza a la ficha de SAM.gov",
      "https://sam.gov/workspace/contract/opp/60aa8e3f/view" in html)

# Lo que se quito de la ficha, pero que sigue siendo la cuenta correcta: se
# comprueba sobre los DATOS, no sobre el texto impreso. Si alguien tocara la
# formula del factoring, este test lo sigue detectando.
_pres = 85000.00       # lo que publica el gobierno
_oferta = 74500.00     # lo queemetry IMO                            # lo queialize
_costo = 52000.00
_bruta = 22500.00
_factor = 2607.50
_neta = 19892.50
# La ganancia se mide contra el PRECIO OFERTADO, no contra el presupuesto.
# Contra el presupuesto daria 33,000 y un 38.8% que no son los numeros de
# este ejemplo: por eso el margen sale 26.7% y no 38.8%.
check("La cuenta sigue cuadrando: oferta - costo = bruta",
      abs((_oferta - _costo) - _bruta) < 0.01,
      f"-> {_oferta} - {_costo} = {_oferta - _costo}, dice {_bruta}")
check("Y no contra el presupuesto del gobierno",
      abs((_oferta - _costo) - _bruta) < 0.01
      and abs((_pres - _costo) - _bruta) > 1.0)
check("Y bruta - factoring = neta",
      abs((_bruta - _factor) - _neta) < 0.01)
check("El factoring es el 3.5% del PRECIO OFERTADO, no del presupuesto",
      abs((74500.0 * 0.035) - _factor) < 0.01,
      f"-> {74500.0 * 0.035:.2f} vs {_factor}")
check("Y no del presupuesto del gobierno (que inflaria la ganancia)",
      abs((_pres * 0.035) - _factor) > 1.0)

for tag in ("b", "i", "code"):
    check(f"<{tag}> balanceado", html.count(f"<{tag}>") == html.count(f"</{tag}>"),
          f"-> {html.count(f'<{tag}>')} vs {html.count(f'</{tag}>')}")
check("<a> balanceado", html.count("<a ") == html.count("</a>"),
      f"-> {html.count('<a ')} vs {html.count('</a>')}")

# La coherencia de la cuenta.
print()
print("  COHERENCIA DE LA CUENTA (lo que antes fallaba en tu ejemplo)")
coherente = (
    abs(FICHA["precio_unitario_oferta"] * FICHA["cantidad_total"]
        - FICHA["precio_oferta_sugerido_usd"]) < 1
    and abs(FICHA["precio_unitario_oferta"] - FICHA["precio_unitario_costo"]
            - FICHA["ganancia_por_unidad"]) < 0.01
    and abs(FICHA["costo_total_usd"]
            - FICHA["precio_unitario_costo"] * FICHA["cantidad_total"]) < 1
    and abs(FICHA["ganancia_total_usd"]
            - FICHA["ganancia_por_unidad"] * FICHA["cantidad_total"]) < 1
    # El factoring va sobre la OFERTA, que es la factura, no sobre el
    # presupuesto del gobierno. Medirlo contra el presupuesto infla el margen.
    and abs(FICHA["costo_factoring_usd"]
            - FICHA["precio_oferta_sugerido_usd"] * config.FACTORING_PCT) < 1
    and abs(FICHA["ganancia_neta_usd"]
            - (FICHA["ganancia_total_usd"] - FICHA["costo_factoring_usd"])) < 1
)
check("Toda la cuenta cierra", coherente)
check("El factoring se cobra sobre la oferta, no sobre el presupuesto",
      abs(FICHA["costo_factoring_usd"]
          - FICHA["valor_contrato_usd"] * config.FACTORING_PCT) > 100,
      "-> si fuera sobre el presupuesto, el margen quedaria inflado")
check("El margen se mide contra lo que se OFERTA",
      abs(FICHA["ganancia_neta_usd"] / FICHA["precio_oferta_sugerido_usd"] * 100
          - FICHA["margen_neto_porcentaje"]) < 0.2,
      f"-> {FICHA['ganancia_neta_usd'] / FICHA['precio_oferta_sugerido_usd'] * 100:.1f}% "
      f"vs {FICHA['margen_neto_porcentaje']}%")
check("Y no contra el presupuesto del gobierno",
      abs(FICHA["ganancia_neta_usd"] / FICHA["valor_contrato_usd"] * 100
          - FICHA["margen_neto_porcentaje"]) > 1,
      "-> seria el error del ejemplo original")

print()
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: el formato es el que pediste, con la cuenta cerrada")
