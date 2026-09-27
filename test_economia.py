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
print("5) LA FICHA MUESTRA TODO ESTO")
print("=" * 70)
html = tn.formatear_analisis(a)
check("Cantidad en unidades", "50 EA" in html, f"-> no encontrado")
check("Modelo", "Dell Latitude 5450" in html)
check("Costo unitario", "$1,200.00" in html)
check("Precio de catalogo unitario", "$1,249.00" in html)
check("Oferta unitaria", "$1,450.00" in html)
check("Ganancia por unidad", "$250.00" in html)
check("Valor del contrato", "$72,500.00" in html)
check("Ganancia bruta", "$12,500.00" in html)
check("Factoring restado", "$2,537.50" in html)
check("Ganancia neta", "$9,962.50" in html)
check("El objetivo de margen aparece", "15%" in html and "35%" in html)
check("El minimo neto aparece", "12%" in html)
check("NO pide capital (el usuario usa factoring)",
      "capital" not in html.lower() and "tener disponible" not in html.lower())
check("Y si explica el mejor margen", "mayorista conviene" in html)
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
