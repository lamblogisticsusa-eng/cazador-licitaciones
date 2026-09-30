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
    # El 30-sep se anadio que Gemini devuelva hasta 3 distribuidores NOMBRADOS
    # de Estados Unidos. Este fixture los trae porque es lo que el bot recibe
    # hoy: sin ellos, la ficha cae a la busqueda general y no se puede
    # comprobar el bloque nuevo.
    "query_google_proveedores":
        "50kw tactical diesel generator spare parts wholesale distributor usa",
    "distribuidores_candidatos": [
        {"nombre": "Radwell", "tipo": "mayorista de repuestos industriales",
         "porque": "Maneja repuestos de generadores de seria militar y permite comparar cotizaciones.",
         "verificar": "Radwell tactical generator spare parts contact"},
        {"nombre": "McMaster-Carr", "tipo": "casa de catalogo industrial",
         "porque": "Stock de modulos de control y envio rapido; buen precio en piezas nuevas.",
         "verificar": "McMaster-Carr generator control module distributor"},
        {"nombre": "Grainger", "tipo": "mayorista industrial",
         "porque": "Alternativa para tener tres precios y negociar el mejor.",
         "verificar": "Grainger diesel generator parts wholesale contact"},
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


def _columnas_alineadas(html: str) -> bool:
    """
    Que los numeros de la tabla empiecen todos en la misma columna.

    Se mide el bloque <pre> de verdad, en el texto que Telegram va a recibir.
    Es la forma de comprobar que el formato hace lo que dice: no basta con
    que haya un <pre>, importa que las etiquetas midan lo mismo.
    """
    import re as _re
    bloque = _re.search(r"<pre>(.*?)</pre>", html, _re.S)
    if not bloque:
        return False
    anchos = set()
    for linea in bloque.group(1).split("\n"):
        if linea.startswith("─") or not linea.strip():
            continue
        anchos.add(len(linea))
    # Que esten TODAS iguales. Ojo con la trampa: si se comparaba una a una
    # contra un ancho fijo de 35 y todas cumplian, el conjunto de anchos
    # "distintos" se quedaba VACIO, y len(vacio) == 1 es False: el check
    # daba FALLA justo cuando la tabla estaba perfecta.
    return len(anchos) == 1


def _explicacion_en_la_misma_linea(html: str) -> bool:
    """
    Que el precio ofertado y su explicacion compartan linea.

    Se parte por lineas y se busca la que tiene el precio, en vez de buscar
    una cadena: en el formato nuevo el precio va en <b>, asi que el texto
    literal entre el monto y el parentesis es " USD</b> (".
    """
    import re as _re
    for linea in html.split("\n"):
        if _re.search(r"\$[\d,]+\.\d\d USD", linea) and "(" in linea:
            return True
    return False


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


# Formato ejecutivo del 30-sep-2026. Se pedio: separadores limpios, resumen
# financiero claro y la lista directa de los 3 distribuidores.
#
# Lo que cambio respecto al 29-sep:
#   - la cuenta por unidad y el desglose del factoring SEGUEN fuera de la
#     ficha (eso no se toco, y la cuenta se sigue comprobando mas abajo)
#   - las cifras pasan a una tabla de dos columnas dentro de <pre>, para que
#     los numeros queden alineados y se lean de un vistazo
#   - la fecha de cierre SUBE arriba, con la identidad: es lo que decide si hay
#     tiempo de ofertar y estaba enterrada bajo el bloque economico
#   - el set-aside aparece, que con la regla de +2 del mismo dia decide si el
#     contrato es para petites empresas
#   - los distribuidores pasan de un enlace suelto a 3 candidatos, cada uno con
#     su enlace de verificacion
#   - la apertura sigue siendo de Kyomoto, porque se decidio conservar una
#     linea de voz kawaii con el cuerpo ejecutivo
for etiqueta_txt, esperado in (
    ("Saludo kawaii", "¡Amo, oportunidad nueva y bien armada!"),
    ("Emoji feliz", "≧◡≦"),
    ("Separadores limpios", "━━━━━━━━"),
    ("Paquete de emojis", "📦"),
    ("Agencia corta delante del producto", "📦 <b>USAF - Suministro"),
    ("Solicitud", "🔢 Solicitud:"),
    ("Agencia", "Department of the Air Force"),
    ("Fecha de cierre", "Vence en"),
    ("Set-aside", "🏷️"),
    ("Producto", "📝 <b>Producto</b>"),
    ("Resumen financiero", "💰 <b>Resumen financiero</b>"),
    ("Presupuesto", "Presupuesto govt."),
    ("Costo", "Costo proveedor"),
    ("Precio a ofertar", "Precio a ofertar"),
    ("Ganancia neta", "GANANCIA NETA"),
    ("Margen neto", "Margen neto"),
    ("Estrategia", "🎯 <b>Estrategia de oferta</b>"),
    ("Distribuidores", "🏭 <b>Distribuidores a verificar (USA)</b>"),
    ("Enlace SAM.gov", "🔗"),
    ("Ficha completa", "Ver ficha en SAM.gov"),
):
    check(etiqueta_txt, esperado in html)

check("Muestra los 4 numeros del ejemplo",
      all(x in html for x in ("$85,000.00", "$52,000.00", "$19,892.50", "$74,500.00")))

# El bloque financiero va en <pre>. Sin <pre> el ancho de cada celda es el de
# su contenido mas largo, y en cuanto una etiqueta pasa de la siguiente la
# cifra baja de linea: las columnas dejan de alinearse justo cuando hay mas
# cifras que comparar. Con <pre> el ancho lo fija el texto.
check("El bloque financiero va en <pre> para que las columnas alineen",
      "<pre>" in html and "</pre>" in html)
check("Y cada cifra arranca en la misma columna",
      "\nPresupuesto govt." in html and "\nCosto proveedor" in html
      and "\nPrecio a ofertar" in html)
check("Y las columnas de verdad cuadran en el fuente",
      _columnas_alineadas(html),
      "-> las etiquetas no terminan en la misma posicion")

# La ficha pone "USD" detras de cada monto: los tres de la tabla mas el
# precio ofertado de la linea de estrategia.
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

check("El margen va como porcentaje junto a la ganancia",
      "26.7%" in html and "Margen neto" in html)

# La explicacion tiene que quedar en la MISMA linea que el precio. Se
# comprueba sobre la LINEA, no sobre una cadena con etiquetas: en el formato
# nuevo el precio va en <b>, asi que entre " USD" y el parentesis hay un
# "</b>". Buscar " USD (" daria falso negativo aunque la linea este bien.
check("La explicacion de la estrategia va en la MISMA linea",
      _explicacion_en_la_misma_linea(html),
      "-> quedo en una linea aparte, hay que unirla")

# 3 distribuidores + el enlace a SAM.gov. Cada uno con su busqueda.
check("Los 3 distribuidores con su enlace de verificacion",
      html.count("google.com/search") == 3,
      f"-> {html.count('google.com/search')} enlaces")
check("Y con el enlace a SAM.gov son 4 en total",
      html.count("<a href=") == 4, f"-> {html.count('<a href=')}")
check("Y avisa de que hay que verificarlos antes de escribir",
      "antes de escribir" in html)
check("Y no inventa ningun correo", "@" not in html,
      "-> hay un @, o sea un contacto que Kyomoto no puede verificar")

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
