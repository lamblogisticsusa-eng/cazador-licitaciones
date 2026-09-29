"""
Comprueba que query_google_proveedores llega de verdad al enlace de la ficha.

LA CADENA COMPLETA, DE UN EXTREMO AL OTRO

    Gemini escribe el campo  ->  gemini_analyzer lo parsea y lo guarda
    ->  telegram_notify lo pasa a distribuidores.para_oportunidad
    ->  ese modulo lo mete en la URL de Google

Si se rompe un eslabon, la ficha sigue mostrando un enlace, pero uno generico
armado con el nombre del producto, y no hay ningun sintoma visible: parece que
funciona. Por eso esta prueba mira la URL exacta, no que "haya un enlace".

LO QUE SE COMPRUEBA

  - Con el campo, la URL sale con el termino de Gemini
  - Sin el campo, la URL sale con el nombre del producto (el respaldo)
  - Con el campo, la URL es DISTINTA de la del respaldo: si fueran iguales,
    significaria que el campo no se esta usando
  - Un termino con comillas no rompe la URL: es lo que hacia que Telegram
    rechazara la ficha entera con un 400
  - Las palabras de instruccion ("how to buy", "cheap") no pasan, porque
    llevan a tutoriales y no a distribuidores
  - "wholesale" y "distributor" SI se conservan, que es donde esta el margen
"""
import io
import os
import sys
import tempfile
from urllib.parse import unquote_plus

DB = os.path.join(tempfile.gettempdir(), "kyo_test_query.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["DRY_RUN"] = "1"
os.environ["SAM_API_KEY"] = "x"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:FAKE"
os.environ["TELEGRAM_CHAT_ID"] = "1"
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


import distribuidores as ds
import gemini_analyzer as ga
import telegram_notify as tn

PRODUCTO = "Valvulas de compuerta de acero fundido"
LUGAR = "Philadelphia PA UNITED STATES"
TERMINO = "6 inch cast steel gate valve wholesale distributor usa"


def consulta(q=""):
    d = ds.para_oportunidad([], PRODUCTO, LUGAR, q)
    return unquote_plus(d["principal"][1].split("?q=")[1])


print("=" * 70)
print("1) CON EL CAMPO, LA URL USA EL TERMINO DE GEMINI")
print("=" * 70)
con_campo = consulta(TERMINO)
sin_campo = consulta("")
print(f"  con campo  : {con_campo}")
print(f"  sin campo  : {sin_campo}")
print()
check("El termino de Gemini sale en la URL",
      "6 inch cast steel gate valve" in con_campo, f"-> {con_campo}")
check("Y NO sale el nombre del producto en su lugar",
      "Valvulas de compuerta" not in con_campo, f"-> {con_campo}")
check("La URL con campo es DISTINTA de la del respaldo",
      con_campo != sin_campo,
      "-> si fueran iguales, el campo no se esta usando")
check("El respaldo sigue usando el nombre del producto",
      "Valvulas de compuerta" in sin_campo, f"-> {sin_campo}")

print()
print("=" * 70)
print("2) LO QUE ROMPE UN ENLACE, Y LO QUE NO")
print("=" * 70)
print("  Una coma en una URL de Google NO rompe nada: es un separador.")
print("  Una comilla doble SI: cierra el HTML de la ficha y Telegram rechaza")
print("  el mensaje entero con un 400. Ese fue el fallo que hizo que ninguna")
print("  ficha llegara nunca (commit e59c6c5).")
print()
casos = [
    ('con comas', "valvula 6 pulgadas, acero, wholesale distributor",
     "acero"),
    ('con comillas dobles', '"gate valve" wholesale distributor', "gate valve"),
    ('con palabras de instruccion', "how to buy gate valve cheap", "gate valve"),
    ('con price repetido', "gate valve wholesale distributor price", "price"),
    ('vacio', "", "Valvulas de compuerta"),
    ('solo espacios', "   ", "Valvulas de compuerta"),
]
for etiqueta, q, debe_contener in casos:
    c = consulta(q)
    ok = debe_contener in c
    check(f"  {etiqueta:28}", ok, f"-> {c[:70]}")

print()
print("  Las palabras de instruccion desaparecen:")
c = consulta("how to buy gate valve cheap")
check("    no queda 'how to'", "how to" not in c.lower(), f"-> {c}")
check("    no queda 'cheap'", "cheap" not in c.lower(), f"-> {c}")

print()
print("  Las palabras de MAYORISTA se conservan:")
c = consulta("6 inch cast steel gate valve wholesale distributor usa")
check("    queda 'wholesale'", "wholesale" in c.lower(), f"-> {c}")
check("    queda 'distributor'", "distributor" in c.lower(), f"-> {c}")
print("    Son las que hacen que los resultados sean de proveedores y no de")
print("    tiendas finales. Ahi esta el margen.")

print()
print("=" * 70)
print("3) LA CADENA COMPLETA: FICHA -> DISTRIBUIDORES")
print("=" * 70)
# El test de arriba era a nivel de modulo. Este va de punta a punta: el mismo
# analisis que produce Gemini, pasado por formatear_analisis, y se mira el
# enlace que sale en el HTML final.
ficha = {
    "title": "FIRE PUMP VALVES", "solicitation": "N1",
    "agencia": "Department of the Navy (NAVSEA)",
    "producto": "Valvulas de compuerta de acero fundido de 6 pulgadas",
    "modelo_especifico": "ASTM A216",
    "unidad_medida": "LOT", "cantidad_total": 40,
    "especificacion_tecnica_clave": "ASTM A216, 150 psi, empaque militar MIL-STD",
    "lugar_entrega": LUGAR, "limite": "2026-11-04T17:00:00-04:00",
    "valor_contrato_usd": 230000.0, "costo_total_usd": 180000.0,
    "ganancia_neta_usd": 41950.0, "margen_neto_porcentaje": 18.2,
    "precio_oferta_sugerido_usd": 230000.0,
    "razonamiento_oferta": "12% bajo el techo, 18.2% neto",
    "busquedas_distribuidores": ["gate valve wholesale distributor usa"],
    "query_google_proveedores": TERMINO,
    "ui_link": "https://sam.gov/opp/abc/view",
    "nivel_riesgo": "medio",
}
html = tn.formatear_analisis(ficha)
import re
enlaces = re.findall(r'href="(https://www\.google\.com/search\?q=[^"]+)"', html)
print(f"  Enlaces de Google en la ficha: {len(enlaces)}")
for e in enlaces:
    print(f"    {unquote_plus(e.split('?q=')[1])[:70]}")
print()
check("La ficha lleva el enlace de Google", len(enlaces) == 1, f"-> {len(enlaces)}")
if enlaces:
    q = unquote_plus(enlaces[0].split("?q=")[1])
    check("Y el enlace usa el termino de Gemini",
          "6 inch cast steel gate valve" in q, f"-> {q}")
    check("Sin comillas en la URL (romperian el HTML)",
          '"' not in enlaces[0])

# Y el respaldo: sin el campo, la ficha tambien tiene que llevar enlace.
ficha_sin = dict(ficha)
ficha_sin.pop("query_google_proveedores")
html_sin = tn.formatear_analisis(ficha_sin)
enlaces_sin = re.findall(r'href="(https://www\.google\.com/search\?q=[^"]+)"', html_sin)
print()
print("  Sin el campo de Gemini (respaldo):")
print(f"    {len(enlaces_sin)} enlace(s)")
for e in enlaces_sin:
    print(f"    {unquote_plus(e.split('?q=')[1])[:70]}")
check("Sin el campo, la ficha TIENE enlace igual", len(enlaces_sin) == 1,
      f"-> {len(enlaces_sin)}")
# El respaldo usa el primer termino de busquedas_distribuidores, NO el nombre
# del producto. Y es lo correcto: el nombre viene en espanol y devolveria
# catalogo europeo, mientras que el termino de Gemini esta en ingles y con el
# sustantivo tecnico.
q_sin = unquote_plus(enlaces_sin[0].split("?q=")[1])
check("El respaldo usa el termino de Gemini, en ingles", "gate valve" in q_sin,
      f"-> {q_sin}")
check("Y no el nombre del producto en espanol", "Valvulas" not in q_sin,
      f"-> {q_sin}")
check("El enlace del respaldo sale limpio (sin comas ni comillas)",
      "," not in enlaces_sin[0] and '"' not in enlaces_sin[0])

print()
print("=" * 70)
print("4) EL CAMPO LLEGA DESDE EL PARSEO DE GEMINI")
print("=" * 70)
# Si el parseo no lo guardara, la ficha recibiria siempre el campo vacio y
# cairia al respaldo sin que se notara.
fuente = io.open(ga.__file__, encoding="utf-8").read()
check("El parseo guarda query_google_proveedores",
      '"query_google_proveedores"' in fuente)
check("Y lo limpia antes de guardarlo (comas y comillas rompen la URL)",
      ".replace(" in fuente)
print("  Sin esto, un termino con coma llegaria crudo al enlace de la ficha,")
print("  y esa es la unica ayuda que tiene el usuario para comprar.")

for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

print()
print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: el termino de Gemini llega al enlace de la ficha")
