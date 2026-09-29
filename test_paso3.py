"""
test_paso3.py - Comprueba el prompt permisivo, el campo de distribuidores y
la linea de monitoreo.

QUE SE PIDIO EN EL PASO 3 (29-sep-2026)

  1. Que Gemini marque viable = true para cualquier compra o suministro de
     producto fisico por debajo de 250,000 USD.
  2. Que NO descalifique por empaque militar estandar ni por entrega fisica.
  3. Que genere el campo query_google_proveedores.
  4. Que scanner.py imprima al final del ciclo:
     "SAM.gov devueltos: X | Pasaron filtro: Y | Aprobados por Gemini: Z"

POR QUE 1 Y 2 IMPORTAN DE VERDAD

Medido el 29-sep-2026 con la API real: 12 analisis seguidos, 0 viables. Uno de
los rechazos fue:

    "Requiere entrega fisica directa en instalacion naval" -> descartada

Eso es exactamente lo que hace este negocio: comprar en USA y mandar al
destino. Descartar por eso es descartar todas las piezas de repuesto. El
prompt viejo decia "si exige presencia en obra... -> false" y el modelo lo
leia como "entrega fisica", sin distinguir una cosa de la otra.

Igual con el empaque militar: MIL-STD, gradiente militar, packed for
extended storage. Eso es la especificacion del producto, no una barrera.

LO QUE NO SE TOCA A PROPOSITO

La regla del margen neto minimo ({config.MARGEN_NETO_MIN}) sigue ahí. Ese es
el filtro de negocio: sin el, el bot mandaria oportunidades donde se pierde
dinero, y "permissivo" no puede significar "acepta todo".

Tampoco se quitan las condiciones que de verdad impiden vender: servicios sin
producto, instalaciones, proveedores obligatoriamente residentes en EE.UU., y
entregas fuera del pais.
"""
import io
import os
import re
import sys
import tempfile
import textwrap

DB = os.path.join(tempfile.gettempdir(), "kyo_test_p3.db")
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


import config
import gemini_analyzer as ga
import scanner

# El prompt tal cual se le manda a Google, con los numeros ya sustituidos.
PROMPT = ga._construir_prompt(
    {"title": "FIRE PUMP VALVES", "solicitationNumber": "N1",
     "naicsCode": "332911", "agencies": [{"name": "Department of the Navy"}],
     "description": "", "typeOfSetAside": "",
     "responseDeadLine": "2026-11-04T17:00:00-04:00"},
    "Adquisicion de valvulas de compuerta de acero fundido de 6 pulgadas, "
    "ASTM A216, 150 psi, empaque militar MIL-STD, entrega fisica directa en "
    "instalacion naval. Not to exceed USD 180,000.00",
    "United States",
)

print("=" * 70)
print("1) EL PROMPT PIDE Viable=true PARA PRODUCTO FISICO")
print("=" * 70)
plano = PROMPT.lower()
print("  Como empieza la seccion de reglas:")
for linea in PROMPT.split("\n"):
    if "REGLA PRINCIPAL" in linea:
        idx = PROMPT.split("\n").index(linea)
        for l in PROMPT.split("\n")[idx:idx + 5]:
            print(f"    {l}")
        break
check("Hay una regla principal", "REGLA PRINCIPAL" in PROMPT)
check("Dice que producto fisico bajo el tope es viable",
      "producto fisico" in plano and "viable = true" in plano)
check("Y nombra el tope real en USD",
      f"{config.TOPE_USD:,.0f}" in PROMPT,
      f"-> {config.TOPE_USD:,.0f}")
print()
print("  Lo que SIGUE descalificando:")
for clave in ("instalacion en obra", "licencia local",
              "residente o establecido", "fuera de estados unidos"):
    check(f"    {clave}", clave in plano)

print()
print("=" * 70)
print("2) YA NO DESCALIFICA POR EMPAQUE NI POR ENTREGA FISICA")
print("=" * 70)
# Estas dos cadenas tienen que aparecer en la seccion de "lo que ya no
# descalifica", NO en la de "lo que si descalifica". Es la distincion que
# importa: si aparecen en las dos, el modelo seguira descartando.
sec_no = plano.split("lo que ya no descalifica")[-1] if "lo que ya no descalifica" in plano else ""
check("Hay una seccion 'LO QUE YA NO DESCALIFICA'",
      "lo que ya no descalifica" in plano)
for tema in ("entrega fisica directa", "mil-std", "empaque militar",
             "idiq", "iso 9001", "ingles"):
    check(f"    {tema} aparece como 'ya no descalifica'",
          tema in sec_no, "-> no esta en la seccion correcta")

# Y la parte de "lo que SI descalifica" no debe mentionar la entrega fisica
# como motivo, porque eso es justo el error que se corrigio.
sec_si = plano.split("lo que si descalifica")[-1].split("lo que ya no descalifica")[0] \
    if "lo que si descalifica" in plano else ""
check("La entrega fisica NO esta en 'lo que SI descalifica'",
      "entrega fisica" not in sec_si,
      "-> si aparece ahi, el prompt sigue contradiciendose")
print()
print("  La seccion que SI descalifica dice:")
for l in PROMPT.split("\n"):
    s = l.strip().lower()
    if s.startswith(("1.", "2.", "3.", "4.", "5.", "6.")) and "false" in l.lower() or \
       ("instalacion" in s or "consultoria" in s):
        print(f"    {l.strip()[:78]}")

print()
print("=" * 70)
print("3) EL FILTRO DE MARGEN NETO SIGUE INTACTO")
print("=" * 70)
# "Permisivo" no puede significar "acepta todo". Sin el filtro de margen, el
# bot mandaria oportunidades donde se pierde dinero.
check("El margen neto minimo sigue en el prompt",
      f"{config.MARGEN_NETO_MIN * 100:.0f}%" in PROMPT)
check("Y sigue siendo motivo de descarte",
      re.search(r"margen neto.*?por debajo de", plano, re.S) is not None)
print(f"  Piso: {config.MARGEN_NETO_MIN * 100:.0f}% neto. Sigue en pie.")

print()
print("=" * 70)
print("4) EL CAMPO query_google_proveedores")
print("=" * 70)
check("Esta en el ESQUEMA de salida", "query_google_proveedores" in ga.ESQUEMA)
check("El ESQUEMA lo declara como str",
      ga.ESQUEMA.get("query_google_proveedores") == "str",
      f"-> {ga.ESQUEMA.get('query_google_proveedores')}")
check("Esta explicado en las instrucciones de salida",
      "query_google_proveedores" in PROMPT)
check("Pide que use la especificacion, no el titulo",
      "especificacion" in plano and "titulo" in plano)
check("Pide la palabra de mayorista, que es donde esta el margen",
      "wholesale" in plano or "mayorista" in plano)
check("Pide que filtre por USA", "usa" in plano or "united states" in plano)
check("Pide que sea corto", "3 a 10 palabras" in plano or "3-10 palabras" in plano)
check("Aparece en el ejemplo de JSON del prompt",
      '"query_google_proveedores"' in PROMPT)

# El parseo tiene que limpiar lo que rompe un enlace de Google.
print()
print("  El parseo limpia lo que romperia el enlace:")
# La ULTIMA aparicion de la cadena, no la primera: la primera esta en el
# ESQUEMA, unas 700 lineas antes del parseo, y ahi no hay ni replace() ni
# [:90] que buscar.
_fuente = io.open(ga.__file__, encoding="utf-8").read()
_i = _fuente.rindex('"query_google_proveedores"')
_bloque = _fuente[_i - 200:_i + 500]
print("     (el bloque de parseo que se comprueba empieza asi)")
_primera = [l for l in _bloque.split(chr(10)) if "query_google_proveedores" in l]
for l in _primera[:3]:
    print(f"       {l.strip()[:70]}")
print()
# Sin limpiar, un termino con una coma deja el enlace de Google roto, y ese
# enlace es la unica ayuda que tiene el usuario para localizar un proveedor.
check("Limpia el termino antes de usarlo en el enlace",
      ".replace(" in _bloque and "[:90]" in _bloque,
      "-> el termino llega crudo al enlace de Google")
check("Quita comas, que rompen la URL", '", "' in _bloque)
check("Y acota el largo a 90 caracteres", "[:90]" in _bloque)

print("  roto, y esa es la unica ayuda que tiene el usuario para comprar.")

print()
print("=" * 70)
print("5) LA LINEA DE MONITOREO EN scanner.py")
print("=" * 70)
_f = io.open(scanner.__file__, encoding="utf-8").read()
check("Imprime 'SAM.gov devueltos:'", "SAM.gov devueltos:" in _f)
check("Imprime 'Pasaron filtro:'", "Pasaron filtro:" in _f)
check("Imprime 'Aprobados por Gemini:'", "Aprobados por Gemini:" in _f)
# re.S porque el print() esta partido en varias lineas de codigo por
# legibilidad. En el log de Render sale en una sola: es una cadena.
check("Los tres van juntos, separados por |",
      re.search(r"SAM\.gov devueltos:.*\|.*Pasaron filtro:.*\|.*Aprobados por Gemini:",
                _f, re.S) is not None)
check("Usa print(), no _log(), para que salga tal cual en Render",
      re.search(r'print\(\s*\n?\s*f?"SAM\.gov devueltos:', _f) is not None,
      "-> con _log() el texto no sale como lo pidio")
check("Y calcula la conversion en porcentaje", "conversion" in _f)

# Comprobacion de comportamiento: el texto tiene que salir con numeros de verdad.
print()
print("  Salida real con los tres numeros:")
import io as _io
import contextlib

buf = _io.StringIO()
resumen = {
    "traidas": 2846, "puntuales": 386, "viables": 0, "notificadas": 0,
    "analizadas": 4, "candidatas": 1812, "errores": [],
}
# Se ejecuta solo la linea del print, aislada, para ver el texto exacto.
# Se toma desde "_x = resumen" hasta el cierre del print. Lo que hay en
# medias forma la linea que se imprime, asi que basta con ese trozo.
# Por lineas y hasta el cierre del print(), sin depender de los escapes de
# las comillas ni de contar caracteres. Los dos intentos anteriores fallaron
# justo por eso: se cortaba un caracter antes del final y exec() decia
# "unterminated string literal".
_i = _f.index("    _x = resumen.get")
_j = _f.index("\n    )", _i) + 7  # incluye el ) que cierra el print
# Se le quita la sangria del cuerpo de escanear(): exec() en nivel de modulo no
# acepta sangria suelta. El texto que se imprime es el mismo.
fragmento = textwrap.dedent(_f[_i:_j])
if fragmento:
    ns = {"resumen": resumen}
    with contextlib.redirect_stdout(buf):
        exec(fragmento, {"resumen": resumen})
    salida = buf.getvalue().strip()
    print(f"    {salida}")
    check("Trae los tres numeros",
          all(x in salida for x in ("2846", "386", "0")))
    check("Trae la conversion en porcentaje", "%" in salida)
else:
    check("Se encontro el fragmento del print para probar", False)

print()
print("=" * 70)
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: paso 3 completo y verificado")
