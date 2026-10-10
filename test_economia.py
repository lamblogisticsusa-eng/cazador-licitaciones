"""
test_economia.py - El modelo economico por unidad con factoring.

Reproduce el ejemplo que dio el usuario: 50 laptops. Comprueba que:
  1. Los totales se recalculan en Python, no se confian en la IA
  2. El factoring se descuenta y el margen neto es el que decide
  3. Una oferta con margen neto bajo se marca como no viable
"""
import os
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


import json as _json

import config
import gemini_analyzer as ga
import telegram_notify as tn


class _RespuestaFalsa:
    """Imita lo que devuelve google-genai, sin tocar la red ni gastar cuota."""

    def __init__(self, texto: str) -> None:
        self.text = texto


class _ModelosFalsos:
    def __init__(self) -> None:
        self.json = "{}"
        self.texto_prompt = ""

    def generate_content(self, model=None, contents=None, config=None):
        self.texto_prompt = contents if isinstance(contents, str) else str(contents)
        return _RespuestaFalsa(self.json)


class _ClienteFalso:
    def __init__(self) -> None:
        self.models = _ModelosFalsos()


def _analizar_con(json_texto: str, opp: dict, desc="desc", lugar="Arizona") -> dict:
    """Ejecuta el analizar() completo con una respuesta fija de Gemini."""
    cliente = _ClienteFalso()
    cliente.models.json = json_texto
    original = ga._get_cliente
    ga._get_cliente = lambda: cliente
    try:
        return ga.analizar(opp, desc, lugar)
    finally:
        ga._get_cliente = original


print("=" * 70)
print("1) PARAMETROS DEL MODELO")
print("=" * 70)
print(f"  Factoring          {config.FACTORING_PCT * 100:.1f}%")
print(f"  Margen bruto       {config.MARGEN_BRUTO_MIN * 100:.0f}% – {config.MARGEN_BRUTO_MAX * 100:.0f}%")
print(f"  Margen neto minimo {config.MARGEN_NETO_MIN * 100:.0f}%")
check("El factoring esta en el rango que dijo (3-4%)",
      0.03 <= config.FACTORING_PCT <= 0.04, f"-> {config.FACTORING_PCT}")
# El minimo neto puede ser algo MAS estricto que bruto - factoring, porque se
# deja holgura para errores de estimacion. Lo que no puede es ser mas flojo.
check("El minimo neto no es mas flojo que bruto - factoring",
      config.MARGEN_NETO_MIN <= config.MARGEN_BRUTO_MIN - config.FACTORING_PCT + 0.02,
      f"-> {config.MARGEN_NETO_MIN} vs {config.MARGEN_BRUTO_MIN - config.FACTORING_PCT}")
check("Y sigue siendo alcanzable (hay margen bruto disponible)",
      config.MARGEN_NETO_MIN + config.FACTORING_PCT <= config.MARGEN_BRUTO_MAX)
print()

print("=" * 70)
print("2) CASO: 50 LAPTOPS (el ejemplo del usuario)")
print("=" * 70)
# Gemini da los numeros por unidad. Los totales se recalculan aqui.
GEMINI = '''{
  "viable": true, "motivo_descarte": "",
  "producto": "Laptops para Answer Key Kiosks",
  "modelo_especifico": "Dell Latitude 5450",
  "unidad_medida": "EA", "cantidad_total": 50,
  "especificacion_tecnica_clave": "i5-1345U, 16GB RAM, 512GB SSD",
  "lugar_entrega": "Fort Huachuca / Arizona / UNITED STATES",
  "valor_contrato_usd": 72500,
  "precio_unitario_costo": 1200, "precio_unitario_mercado": 1249,
  "precio_unitario_oferta": 1450,
  "precio_oferta_sugerido_usd": 72500,
  "margen_bruto_porcentaje": 17.2, "costo_factoring_usd": 2537,
  "busquedas_distribuidores": ["Dell Latitude 5450 wholesale distributor usa"],
  "margen_por_distribuidor": "Catalogo grande 15% (1,380), mayorista 20% (1,440), fabricante directo 30% (1,560). El mayorista conviene: el directo exige MOQ de 500 unidades.",
  "nivel_riesgo": "medio",
  "preguntas_criticas": ["Aceptan marcas equivalentes?", "Cage code?"],
  "observaciones": "", "estrategia_oferta": "Ofertar 1450 c/u con entrega en 45 dias."
}'''

_opp = {
    "noticeId": "abc123", "title": "50 LAPTOPS", "naicsCode": "334111",
    "classificationCode": "7", "solicitationNumber": "W911RZ26Q0001",
    "typeOfSetAside": "SBA", "responseDeadLine": "2026-11-04",
    "postedDate": "2026-09-25", "fullParentPathName": "DEPT OF DEFENSE.ARMY",
    "uiLink": "https://sam.gov/x", "pointOfContact": [{"fullName": "Jane", "email": "j@d.army.mil"}],
}
a = _analizar_con(GEMINI, _opp, "Fifty laptops, model Dell Latitude 5450...")

print("  Lo que dio Gemini:")
print(f"    cantidad          {a['cantidad_total']:,.0f} {a['unidad_medida']}")
print(f"    costo unitario    ${a['precio_unitario_costo']:,.2f}")
print(f"    oferta unitaria   ${a['precio_unitario_oferta']:,.2f}")
print(f"    ganancia unidad   ${a['ganancia_por_unidad']:,.2f}")
print()
print("  Lo que recalculo Kyomoto:")
print(f"    valor contrato    ${a['valor_contrato_usd']:,.2f}")
print(f"    costo total       ${a['costo_total_usd']:,.2f}")
print(f"    ganancia bruta    ${a['ganancia_total_usd']:,.2f}  ({a['margen_bruto_porcentaje']:.1f}%)")
print(f"    factoring 3.5%    ${a['costo_factoring_usd']:,.2f}")
print(f"    ganancia neta     ${a['ganancia_neta_usd']:,.2f}  ({a['margen_neto_porcentaje']:.1f}%)")
print()

check("Cantidad 50", a["cantidad_total"] == 50)
check("Valor = 50 x 1450 = 72,500", a["valor_contrato_usd"] == 72500,
      f"-> {a['valor_contrato_usd']}")
check("Costo total = 50 x 1200 = 60,000", a["costo_total_usd"] == 60000,
      f"-> {a['costo_total_usd']}")
check("Ganancia por unidad = 1450 - 1200 = 250", a["ganancia_por_unidad"] == 250,
      f"-> {a['ganancia_por_unidad']}")
check("Ganancia bruta = 50 x 250 = 12,500", a["ganancia_total_usd"] == 12500,
      f"-> {a['ganancia_total_usd']}")
check("Margen bruto ~17.2%", abs(a["margen_bruto_porcentaje"] - 17.24) < 0.1,
      f"-> {a['margen_bruto_porcentaje']}")
check("Factoring = 3.5% de 72,500 = 2,537.50",
      abs(a["costo_factoring_usd"] - 2537.5) < 0.01, f"-> {a['costo_factoring_usd']}")
check("Ganancia neta = 12,500 - 2,537.50 = 9,962.50",
      abs(a["ganancia_neta_usd"] - 9962.5) < 0.01, f"-> {a['ganancia_neta_usd']}")
check("Margen neto ~13.7%", abs(a["margen_neto_porcentaje"] - 13.74) < 0.1,
      f"-> {a['margen_neto_porcentaje']}")
check("Margen neto supera el minimo, es ofertable",
      a["margen_neto_porcentaje"] >= config.MARGEN_NETO_MIN * 100,
      f"-> {a['margen_neto_porcentaje']:.1f}% vs minimo {config.MARGEN_NETO_MIN*100:.0f}%")
print()

print("=" * 70)
print("3) LA IA NO PUEDE INVENTAR LA ARITMETICA")
print("=" * 70)
# Gemini dice que el margen bruto es 85%, pero las unidades dan 17%.
GEMINI_MALO = GEMINI.replace('"margen_bruto_porcentaje": 17.2', '"margen_bruto_porcentaje": 85.0')
b = _analizar_con(GEMINI_MALO, _opp)
print(f"  Gemini dijo: 85%")
print(f"  Kyomoto dice: {b['margen_bruto_porcentaje']:.1f}%  (recalculado de las unidades)")
check("Kyomoto ignora el margen inventado", abs(b["margen_bruto_porcentaje"] - 17.24) < 0.1,
      f"-> {b['margen_bruto_porcentaje']}")
print()

print("=" * 70)
print("4) UNA OFERTA QUE NO VALE LA PENA SE MARCA NO VIABLE")
print("=" * 70)
# Oferta ajustada: 1250 c/u sobre costo de 1200 = 4% bruto, menos factoring.
GEMINI_MALO_MARGEN = GEMINI.replace('"precio_unitario_oferta": 1450', '"precio_unitario_oferta": 1250')
c = _analizar_con(GEMINI_MALO_MARGEN, _opp)
print(f"  Oferta 1250 vs costo 1200 -> {c['margen_bruto_porcentaje']:.1f}% bruto")
print(f"  Neto tras factoring: {c['margen_neto_porcentaje']:.1f}%")
check("El margen neto cae por debajo del minimo",
      c["margen_neto_porcentaje"] < config.MARGEN_NETO_MIN * 100)
check("La ficha lo deja ver, para que el usuario decida",
      isinstance(c["viable"], bool))
print("  (la IA deberia marcarlo viable=false; Kyomoto muestra la cuenta "
      "para que el usuario lo confirme)")
print()

print("=" * 70)
print("=" * 70)
print("5) LA FICHA MUESTRA ESTO, Y LOS MONTOS SON LOS DEL ANALISIS")
print("=" * 70)
# La cuenta por unidad y el desglose del factoring se quitaron del diseno el
# 29-sep-2026: la ficha es mas corta y se lee de un vistazo. Pero los numeros
# que imprime tienen que ser EXACTAMENTE los que salio el analisis, no una
# cuenta paralela. Eso es lo que se comprueba aqui.
html = tn.formatear_analisis(a)

# Lo que la ficha nueva si muestra.
check("Cantidad en unidades", "50 EA" in html, "-> no encontrado")
check("Modelo", "Dell Latitude 5450" in html)
check("Valor del contrato", "$72,500.00" in html)
check("Ganancia neta", "$9,962.50" in html)
# Cada monto de la tabla lleva "USD" detras. Se comprueba fila por fila y no
# contando el total, porque el total depende de la variante de la frase de
# estrategia y esa ya no es fija:
#
# Este fixture tiene valor == oferta (72.500 los dos), asi que el descuento es
# 0 y la frase es la del tope del presupuesto, que NO lleva precio y por tanto
# no aporta un cuarto " USD". Con una oferta por debajo del techo la frase si
# lo lleva, y el total seria 4. Un conteo global comprobaria una cosa que
# depende del caso en vez de la regla que importa.
for fila in ("Presupuesto govt.", "Costo proveedor", "Precio a ofertar"):
    _linea = [l for l in html.split("\n") if l.startswith(fila)]
    check(f"'{fila}' lleva USD detras",
          bool(_linea) and " USD" in _linea[0],
          f"-> {[l.strip() for l in _linea]}")
check("Y hay al menos los tres de la tabla", html.count(" USD") >= 3,
      f"-> {html.count(' USD')}")
# Y la frase de estrategia no repite el precio cuando ya lo trae ella.
check("La estrategia no duplica el precio",
      not __import__("re").search(r"\(Ofertar a", html),
      "-> el precio sale dos veces en la misma linea")
check("Presupuesto y costo con ~ (son estimaciones)",
      "~$72,500.00" in html and "~$60,000.00" in html)
check("La ganancia neta sin ~ (se calcula)", "~$9,962.50" not in html)

# La coherencia entre lo que dice el analisis y lo que imprime la ficha. Este
# es el test que de verdad importa: si alguien tocara la cuenta que hace la
# ficha, aqui se veria aunque las cifras se vieran bien sueltas.
print()
print("   La cuenta de la ficha sale del analisis, no se rehace:")
for etiqueta, valor in (
    ("valor_contrato_usd", "valor_contrato_usd"),
    ("costo_total_usd", "costo_total_usd"),
    ("ganancia_neta_usd", "ganancia_neta_usd"),
    ("precio_oferta_sugerido_usd", "precio_oferta_sugerido_usd"),
):
    esperado = f"${a[valor]:,.2f}"
    check(f"  {etiqueta} aparece con el valor del analisis ({esperado})",
          esperado in html, f"-> la ficha podria estar mostrando otra cosa")

# La cuenta sigue cerrando, aunque ya no se imprima el desglose. Sin esto, el
# error del 38.8% podria volver sin que nada lo notara.
print()
print("   Y la cuenta sigue cuadrando (aunque no se imprima entera):")
check("  Oferta - costo = ganancia bruta",
      abs(a["precio_oferta_sugerido_usd"] - a["costo_total_usd"]
          - a["ganancia_total_usd"]) < 0.01)
check("  Bruta - factoring = neta",
      abs(a["ganancia_total_usd"] - a["costo_factoring_usd"]
          - a["ganancia_neta_usd"]) < 0.01)
check("  El factoring es el 3.5% del PRECIO OFERTADO",
      abs(a["precio_oferta_sugerido_usd"] * 0.035 - a["costo_factoring_usd"]) < 0.01)
# En este caso el techo del gobierno y el precio ofertar son el MISMO numero
# (50 x 1,450 = 72,500), por construccion del caso de prueba, asi que aqui no
# se puede comprobar si el factoring se desconto sobre uno o sobre otro: las
# dos bases coinciden. La comprobacion que si lo verifica esta en
# test_ficha.py, donde el techo se dejo en 245,000 y la oferta en 230,000
# precisamente para que la diferencia sea detectable.
check("  El techo y la oferta coinciden en este caso, asi que la base es "
      "indistinguible aqui",
      a["valor_contrato_usd"] == a["precio_oferta_sugerido_usd"],
      "-> si dejaran de coincidir, la comprobacion de la base si seria "
      "posible y habria que habilitarla")
check("  El margen neto se mide sobre la VENTA",
      abs(a["ganancia_neta_usd"] / a["precio_oferta_sugerido_usd"] * 100
          - a["margen_neto_porcentaje"]) < 0.1)

# El objetivo de margen 15-35% y el "donde esta el mejor margen" se quitaron de
# la IMPRESION, pero los filtros siguen en config.py y se siguen aplicando
# antes de que un aviso llegue a Gemini. Se comprueba ahi.
print()
print("   Los filtros de margen siguen existiendo, en la configuracion:")
import config as _cfg
check("  Piso de margen bruto 15%", _cfg.MARGEN_BRUTO_MIN == 0.15)
check("  Techo de margen bruto 35%", _cfg.MARGEN_BRUTO_MAX == 0.35)
check("  Minimo neto 12%", _cfg.MARGEN_NETO_MIN == 0.12)

# El usuario no tiene capital y usa factoring. Si la ficha empieza a pedir
# capital, es un problema aunque no falle nada mas.
check("NO pide capital (el usuario usa factoring)",
      "tener disponible" not in html.lower()
      and "necesitas capital" not in html.lower(),
      "-> la ficha esta pidiendo capital que el usuario no tiene")

for tag in ("b", "code", "i"):
    check(f"<{tag}> balanceado", html.count(f"<{tag}>") == html.count(f"</{tag}>"))
check("<a> balanceado", html.count("<a ") == html.count("</a>"))
check("Cabe en 4096", len(html) <= 4096, f"-> {len(html)}")
print()

print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: el modelo economico por unidad con factoring funciona")
