"""
test_html_telegram.py - Que nada que vaya a Telegram con parse_mode=HTML
lleve una entidad que Telegram no admite.

QUE PASABA

kyo.py:64 ponia "&nbsp;|&nbsp;" como separador del resumen de barrido, y
main.py lo mandaba con parse_mode=ParseMode.HTML.

Telegram, con parse_mode=HTML, admite CUATRO entidades: &lt;, &gt;, &amp; y
&quot;. Cualquier otra hace que el servidor conteste

    400 Bad Request: can't parse entities: Unsupported entity: "nbsp"

y RECHAZA EL MENSAJE ENTERO. No sale un signo raro: no sale nada.

No se notaba porque main.py envuelve la llamada en un try/except y reenvia en
texto plano. El mensaje llegaba igual, pero con la cadena literal escrita en
el chat:

    Cuota de hoy: 14/120 &nbsp;|&nbsp; quedan 106

QUE COMPRUEBA ESTE ARCHIVO

1) Que el resumen de barrido salga sin entidades prohibidas. Se mira la SALIDA
   de la funcion, no el fuente del archivo: el fuente tiene &nbsp; escrito en
   los comentarios que explican el bug, y escanear el fuente daria un falso
   positivo cada vez que alguien documente el problema.

2) Lo mismo con la ficha de la oportunidad y con el mensaje de error, que
   tambien van con parse_mode=HTML.

3) Que las cuatro entidades que Telegram SI admite sigan funcionando: un
   escaneo brutal que prohibiera &lt; y &amp; dejaria pasar justo el
   contenido que rompio el kaomoji del 28-sep.

4) Que pdf_generator conserve su &nbsp;. Ahi es reportlab, que SI la admite,
   y la necesita para separar telefono y email. Si alguien "arregla" ese
   archivo por este mismo error, rompe el PDF.
"""
import os
import re
import sys

os.environ["SAM_API_KEY"] = "x"
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


import kyo
import telegram_notify as tn

# Las cuatro que Telegram admite de verdad con parse_mode=HTML.
PERMITIDAS = {"lt", "gt", "amp", "quot"}
ENTIDAD = re.compile(r"&([a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);")


def prohibidas(texto: str) -> set:
    """Entidades del texto que Telegram rechazaria con un 400."""
    return {
        m.group(1).lower()
        for m in ENTIDAD.finditer(texto or "")
        if m.group(1).lower() not in PERMITIDAS
    }


print("=" * 74)
print("1) EL RESUMEN DE BARRIDO (el que llevaba &nbsp;)")
print("=" * 74)

RESUMEN = {
    "traidas": 2846, "puntuales": 386, "analizadas": 6, "viables": 2,
    "cuota": {"usadas": 14, "presupuesto": 120, "restantes": 106},
}
salida = kyo.resumen_escaneo(RESUMEN)
print("  Linea de cuota tal como sale:")
for l in salida.split("\n"):
    if "Cuota" in l:
        print("    %r" % l)

malas = prohibidas(salida)
print("  Entidades no soportadas por Telegram: %s" % (malas or "ninguna"))
check("El resumen no lleva &nbsp;", not malas, "-> %s" % malas)
check("Y el separador se ve limpio", "·" in salida and "nbsp" not in salida)
check("Y el HTML sigue balanceado",
      salida.count("<b>") == salida.count("</b>"))

print()
print("  Y sin cuota, que es otra rama:")
sin_cuota = kyo.resumen_escaneo({"traidas": 0, "puntuales": 0,
                                  "analizadas": 0, "viables": 0})
check("Tampoco lleva entidades malas", not prohibidas(sin_cuota),
      "-> %s" % prohibidas(sin_cuota))

print()
print("=" * 74)
print("2) LOS OTROS MENSAJES QUE VAN CON parse_mode=HTML")
print("=" * 74)
FICHA = {
    "title": "53--PARTS KIT,SEAL REPLACEMENT <URGENTE>",
    "solicitation": "N00019-26-R-0099",
    "agencia": "Department of the Navy & Marines",
    "set_aside": "Total Small Business Set-Aside",
    "limite": "2026-11-04T17:00:00-04:00",
    "producto": "Sellos EPDM & repuestos",
    "modelo_especifico": "NSN 5330-01-586-4931",
    "unidad_medida": "KIT", "cantidad_total": 50,
    "especificacion_tecnica_clave": "ASTM D2000, 70 Shore A",
    "lugar_entrega": "Philadelphia / PA / USA",
    "valor_contrato_usd": 85000, "costo_total_usd": 60000,
    "ganancia_neta_usd": 11242.50, "margen_neto_porcentaje": 15.1,
    "precio_oferta_sugerido_usd": 74500,
    "razonamiento_oferta": "Vamos 12% bajo el tope",
    "query_google_proveedores": "EPDM seal kit wholesale distributor usa",
    "busquedas_distribuidores": ["EPDM seal kit"],
    "distribuidores_candidatos": [
        {"nombre": "Radwell", "tipo": "mayorista", "porque": "Stock.",
         "verificar": "Radwell EPDM seal contact"},
    ],
    "sin_descripcion": False,
    "ui_link": "https://sam.gov/x/view",
}
h = tn.formatear_analisis(FICHA)
check("La ficha tampoco lleva entidades malas", not prohibidas(h),
      "-> %s" % prohibidas(h))
check("Y el '&' del titulo llega escapado como &amp;",
      "&amp;" in h, "-> el ampersand se perdio")
check("Y el '<' del titulo llega escapado como &lt;",
      "&lt;URGENTE&gt;" in h, "-> los angulos se perdieron")

print()
print("  Y el mensaje de error, que tambien va con HTML:")
err = tn.aviso_error.__doc__ or ""
try:
    cuerpo = ("<b>Error</b>\n\n<pre>" + tn._esc("boom & <crash> &nbsp; bad") + "</pre>")
except Exception as e:
    print("  (no se pudo componer: %s)" % e)
    cuerpo = ""
if cuerpo:
    check("Y con contenido escapado a mano tampoco se cuela nada",
          not prohibidas(cuerpo), "-> %s" % prohibidas(cuerpo))

print()
print("=" * 74)
print("3) QUE LAS CUATRO QUE TELEGRAM SI ADMITE SIGUAN PASANDO")
print("=" * 74)
# Un escaneo que prohibiera estas cuatro dejaria pasar el contenido que rompio
# el kaomoji del 28-sep, que era justamente un "&lt;" mal colocado.
for ent in ("&lt;", "&gt;", "&amp;", "&quot;"):
    check("%-8s se considera permitida" % ent, ent[1:-1].lower() not in
          prohibidas("x" + ent + "y"))
check("Y &nbsp; sigue siendo rechazada", "nbsp" in prohibidas("a&nbsp;b"))
# La forma numerica tambien vale: &#160; es el mismo caracter que &nbsp; y
# Telegram tambien la rechaza. El guardia la devuelve con la almohadilla
# puesta, porque es lo que.regex captura del nombre de la entidad.
check("Y &#160; (su forma numerica) tambien se rechaza",
      "#160" in prohibidas("a&#160;b"),
      "-> %s" % prohibidas("a&#160;b"))
check("Y &#xA0; en hexadecimal tambien",
      "#xa0" in prohibidas("a&#xA0;b"),
      "-> %s" % prohibidas("a&#xA0;b"))

print()
print("=" * 74)
print("4) PDF: QUE &nbsp; NO SE TOQUE EN REPORTLAB")
print("=" * 74)
import io
P = io.open("pdf_generator.py", encoding="utf-8").read()
check("pdf_generator.py conserva su &nbsp;", "&nbsp;|&nbsp;" in P,
      "-> se toco por error y el PDF pierde la separacion telefono/email")
check("Y no se metio &nbsp; en la ficha de Telegram", "&nbsp;" not in h)
print()
print("  &nbsp; aparece ahora solo en:")
for ruta in ("kyo.py", "pdf_generator.py"):
    txt = io.open(ruta, encoding="utf-8").read()
    for i, l in enumerate(txt.splitlines(), 1):
        if "&nbsp;" in l:
            print("    %s L%d  %s" % (ruta, i, l.strip()[:64]))

print()
print("=" * 74)
if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: nada que vaya a Telegram lleva una entidad prohibida")
print("=" * 74)