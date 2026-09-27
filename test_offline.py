"""
test_offline.py - Comprueba la logica sin tocar la red.
Vale lo mismo con la cuota de SAM.gov agotada: el filtro, el puntaje, el
presupuesto, el render y el menu se pueden verificar siempre.
"""
import os
import sys

os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("SAM_API_KEY", "x")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:FAKE")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

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
import kyo
import quota
import scanner
import store
import telegram_notify as tn

print("=" * 70)
print("1) LA API DE SAM.gov RESPETA SU PROPIO TOPE")
print("=" * 70)
import sam_api

sam_api._proximo_acceso = "2026-09-28T00:00:00Z"
check("throttled() lo detecta", sam_api.throttled() is True)
check("proximo_acceso() lo expone", "2026-09-28" in sam_api.proximo_acceso())
try:
    list(sam_api.barrer(1))
    check("barrer() falla rapido si esta throttled", False, "-> no fallo")
except sam_api.SamError as e:
    check("barrer() falla rapido si esta throttled", True)
    check("El error dice cuando vuelve", "2026-09-28" in str(e), f"-> {e}")
sam_api._proximo_acceso = None
check("throttle() se limpia", sam_api.throttled() is False)
print()

print("=" * 70)
print("2) CACHE DE DESCRIPCIONES (ahorra peticiones)")
print("=" * 70)
store.init_db()
store.limpiar_cache()
store.cache_descripcion("abc", "Sellos mecanicos de acero 316L, 500 EA")
check("Guarda la descripcion", store.leer_descripcion("abc").startswith("Sellos"))
check("Un id desconocido devuelve vacio", store.leer_descripcion("xyz") == "")
store.cache_descripcion("vacio", "")
check("No cachea textos vacios (se reintenta)", store.leer_descripcion("vacio") == "")
n = store.limpiar_cache()
check("limpiar_cache devuelve el numero", n == 1, f"-> {n}")
print()

print("=" * 70)
print("3) EL FILTRO SIGUE FUNCIONANDO SIN RED")
print("=" * 70)
buena = {
    "title": "USNS MERCY FIRE PUMP VALVES",
    "naicsCode": "332911", "classificationCode": "5330", "typeOfSetAside": "SBA",
    "responseDeadLine": "2026-11-01T18:00:00-07:00",
    "placeOfPerformance": {"city": {"name": "Norfolk"}, "state": {"code": "VA", "name": "VA"},
                          "country": {"code": "USA", "name": "UNITED STATES"}},
}
mala = dict(buena, title="PARTS KIT with DD2345 security clearance required")
p1, _ = filters.puntuar(buena, "Suministro de valvulas. Contract value $48,000")
p2, _ = filters.puntuar(mala, "Requiere DD2345 y security clearance")
check("La buena pasa el piso", p1 >= config.PUNTAJE_MINIMO, f"-> {p1}")
check("La mala cae bajo el piso", p2 < config.PUNTAJE_MINIMO, f"-> {p2}")
print()

print("=" * 70)
print("4) EL MENU Y LOS TEXTOS NO TOQUAN RED")
print("=" * 70)
import menu

for fn, nombre in ((main_ := __import__("main"), "main"),):
    pass
import main
for nombre, fn in (("estado", main.texto_estado), ("cuota", main.texto_cuota),
                   ("puntajes", main.texto_puntajes)):
    try:
        cuerpo = fn()
        check(f"texto_{nombre}() sin red", isinstance(cuerpo, str) and len(cuerpo) > 20,
              f"-> {len(cuerpo) if isinstance(cuerpo, str) else cuerpo}")
        check(f"  cabe en 4096", len(cuerpo) <= 4096, f"-> {len(cuerpo)}")
    except Exception as e:
        check(f"texto_{nombre}() sin red", False, f"-> {type(e).__name__}: {e}")
kb = menu.teclado(True)
check("El teclado se construye", len(kb.inline_keyboard) == 4)
print()

print("=" * 70)
print("5) EL RENDER NO ROMPE CON TEXTO HOSTIL")
print("=" * 70)
hostil = {
    "title": "<script>alert(1)</script> *negrita* _cursiva_",
    "solicitation": "A&B<C>", "agencia": "US ARMY *CORPS*", "naics": "332911",
    "psc": "5330", "set_aside": "SBA", "producto": "Sellos <b>x</b> & más",
    "cantidad_estimada": "2,000 EA", "especificacion_tecnica_clave": "ISO 9001",
    "lugar_entrega": "Norfolk / VA / USA", "limite": "2026-11-01",
    "sin_descripcion": False, "valor_contrato_usd": 48000.0,
    "costo_proveedor_usd": 32000.0, "ganancia_neta_usd": 16000.0,
    "margen_porcentaje": 33.3, "precio_unitario_referencia_usd": 160.0,
    "precio_unitario_sugerido_usd": 213.0, "precio_oferta_sugerido_usd": 45000.0,
    "estrategia_oferta": "Ofertar 8% bajo.", "busquedas_distribuidores": ["a & b"],
    "nivel_riesgo": "medio", "preguntas_criticas": ["¿<b>x</b>?"],
    "observaciones": "", "contacto": "A & B", "ui_link": "https://x.com/?a=1&b=2",
}
html = tn.formatear_analisis(hostil)
check("No deja <script>", "<script>" not in html)
check("No deja <b> del proveedor", "<b>x</b>" not in html)
check("Escapa el ampersand", "&amp;" in html)
check("El enlace sobrevive", 'href="https://x.com/?a=1&amp;b=2"' in html)
for tag in ("b", "code", "i"):
    check(f"<{tag}> balanceado", html.count(f"<{tag}>") == html.count(f"</{tag}>"),
          f"-> {html.count(f'<{tag}>')} vs {html.count(f'</{tag}>')}")
# <a> lleva atributos, asi que el patron es "<a " y no "<a>".
check("<a> balanceado", html.count("<a ") == html.count("</a>"),
      f"-> {html.count('<a ')} vs {html.count('</a>')}")
check("El mensaje de Kyomoto va al final", kyo.CIERRE in html)
print()

print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: todo funciona aunque SAM.gov este cerrado")
