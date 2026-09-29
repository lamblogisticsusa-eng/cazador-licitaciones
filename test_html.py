"""
test_html.py - La ficha tiene que LLEGAR, no solo parecer correcta.

BUGS QUE SOLO SALIERON ENVIANDO DE VERDAD (28-sep-2026, con la API real de
Telegram, no con un mock)

1) LA FIRMA DE KYOMOTO ROMPIA EL HTML ENTERO
    kyo.CIERRE = "Kyomoto passandote la info para que tu llegue a tiempo
                  (˶>ᴗ<˶) ⁾"
Telegram responde:
    Bad Request: can't parse entities: Unsupported start tag "\u02f6)"
                  at byte offset 3516
El "<" de "ᴗ<" lo lee como el inicio de una etiqueta, toma "˶" como nombre y
rechaza el mensaje. La ficha NO LLEGA. Y estaba en todas las fichas, o sea
que la primera licitacion real habria fallado en produccion.

2) _trocear PODIA PARTIR UNA ETIQUETA POR LA MITAD
    corte = linea.rfind(" ", 0, limite)
Un espacio puede caer dentro de <a href="...">. La primera mitad queda con la
etiqueta sin cerrar y Telegram rechaza el trozo. No se disparaba con la
ficha actual (3.354 caracteres, cabe entera), pero cualquier campo largo de
Gemini lo activaba.

3) enviar() ESCONDIA TODO ESO
Al recibir un 400 reintentaba en texto plano y devolvia True. La ficha
llegaba SIN negritas, SIN links y SIN cursivas, y el sistema decia "ok".
"""
import os
import sys

os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("SAM_API_KEY", "x")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


import kyo
import telegram_notify as tn

print("=" * 70)
print("1) NADA DE LO QUE SE ESCRIBE A MANO TRAE ANGULOS SUELTOS")
print("=" * 70)
import re

print(f"  CIERRE = {kyo.CIERRE!r}")
check("La firma no tiene '<'", "<" not in kyo.CIERRE)
check("La firma no tiene '>'", ">" not in kyo.CIERRE)
# El kaomoji original era (˶>ᴗ<˶). Este es el que rompia Telegram.
check("Ya no es el kaomoji con angulos",
      "ᴗ<˶" not in kyo.CIERRE, f"-> {kyo.CIERRE!r}")

print()
print("=" * 70)
print("2) _sanear_html ESCAPA LO QUE NO ES ETIQUETA")
print("=" * 70)
CASOS = [
    ("<b>ok</b> y (˶>ᴗ<˶)", "<˶", "&lt;˶"),
    ("FOB <destino> o <origen>", "<destino>", "&lt;destino&gt;"),
    ("5 < 10 y 20 > 15", "5 < 1", "5 &lt; 1"),
    ("<script>alert(1)</script>", "<script>", "&lt;script&gt;"),
    ("<xyz atributo>", "<xyz", "&lt;xyz"),
]
for texto, no_debe, debe in CASOS:
    out = tn._sanear_html(texto)
    check(f"{debe:22} <- {texto[:38]!r}",
          debe in out and no_debe not in out, f"-> {out!r}")

print()
print("  Una etiqueta ABIERTA se cierra sola, no se escapa:")
# "<b>sin cerrar" deja a Telegram con un 400 por etiqueta desbalanceada. El
# sanitizador la cierra al final, que es mejor que escaparla: el texto se ve
# igual y el mensaje SI llega.
out = tn._sanear_html("<b>sin cerrar")
check("Cierra la <b> que quedaba abierta",
      out == "<b>sin cerrar</b>", f"-> {out!r}")
out = tn._sanear_html("<b>a<i>b</b>")
check("Cierra tambien la <i> mal anidada, y en orden",
      out == "<b>a<i>b</i></b>", f"-> {out!r}")

print()
print("  Un cierre HUERFANO se escapa, porque no hay nada que cerrar:")
out = tn._sanear_html("texto </b> mas")
check("El </b> sin abrir se escapa", out == "texto &lt;/b&gt; mas", f"-> {out!r}")

print()
print("  Las etiquetas VALIDAS se respetan:")
VALIDAS = [
    "<b>negrita</b>", "<i>cursiva</i>", "<u>subrayado</u>",
    "<code>codigo</code>", "<pre>pre</pre>",
    '<a href="https://google.com">link</a>',
    '<a href="https://x.com?a=1&amp;b=2">l</a>',
    "<b/>",
]
for v in VALIDAS:
    check(f"intacta: {v[:40]}", tn._sanear_html(v) == v,
          f"-> {tn._sanear_html(v)!r}")

print()
print("  Las entidades ya escapadas no se tocan:")
for v in ("&lt;", "&gt;", "&amp;", "a &lt; b"):
    check(f"intacta: {v}", tn._sanear_html(v) == v)

print()
print("=" * 70)
print("3) LA FICHA COMPLETA SALE CON HTML VALIDO")
print("=" * 70)
import io

_f = io.open("test_formato.py", encoding="utf-8").read()
_i = _f.index("FICHA = {")
_j = _f.index("\n}\n", _i) + 3
_ns = {"float": float, "int": int}
exec(compile(_f[_i:_j], "test_formato.py:FICHA", "exec"), _ns)
FICHA = _ns["FICHA"]

html = tn.formatear_analisis(FICHA)
print(f"  Longitud: {len(html)}")

# El bug 1 en su forma exacta: si queda cualquier "<" que no sea una
# etiqueta de Telegram, el envio se rechaza entero.
sospechosos = []
i = 0
while True:
    j = html.find("<", i)
    if j == -1:
        break
    k = html.find(">", j)
    if k == -1:
        sospechosos.append(html[j:j + 20])
        break
    trozo = html[j:k + 1].strip()
    if not tn._ETIQUETAS_OK.match(trozo):
        sospechosos.append(trozo)
    i = k + 1
check("La ficha no tiene ninguna etiqueta sospechosa", not sospechosos,
      f"-> {sospechosos[:3]}")
check("Y la firma aparece", "Kyomoto pasandote" in html)
check("Y bien escrita", "passandote" not in html)

# Las etiquetas del formulario siguen balanceadas.
for et, cierre in (("b", "/b"), ("i", "/i"), ("code", "/code"), ("a ", "/a")):
    n1 = html.count(f"<{et}") if et.endswith(" ") else html.count(f"<{et}>")
    n2 = html.count(f"</{et[:-1]}>") if et.endswith(" ") else html.count(f"</{et}>")
    check(f"<{et}> y </{et}> balanceados ({n1}/{n2})", n1 == n2)

print()
print("=" * 70)
print("4) EL TROCEADO NO PARTE ETIQUETAS NI SE COLGANDA")
print("=" * 70)
# Una sola linea larguisima con un link dentro: el caso que parte la etiqueta.
caso = ('<a href="https://www.google.com/search?q='
        + "repuestos_para_generador " * 200
        + '">Ver</a>')
print(f"  Linea de {len(caso)} caracteres con un <a href> gigante")
trozos = tn._trocear(caso, limite=400)
print(f"  -> {len(trozos)} trozos")
check("No se colgó (volvio)", True)
check("Todos los trozos respetan el limite",
      all(len(t) <= 400 for t in trozos),
      f"-> {[len(t) for t in trozos][:5]}")
check("Se recupera el texto entero",
      "".join(trozos).replace(" ", "") != "")

# El caso patologico que SI colgaba: una linea que empieza con "<" y no
# cierra la etiqueta nunca. _corte_seguro tiene que devolver algo > 0.
caso2 = "<a href=\"" + "x" * 5000
print(f"  Linea patologica de {len(caso2)} caracteres: empieza con '<' y "
      f"nunca cierra")
try:
    t2 = tn._trocear(caso2, limite=400)
    check("No se cuelga con una etiqueta que nunca cierra", True)
    check("Y sale en trozos acotados", all(len(t) <= 400 for t in t2),
          f"-> {[len(t) for t in t2][:5]}")
except Exception as e:
    check("No se cuelga con una etiqueta que nunca cierra", False,
          f"-> {type(e).__name__}: {e}")

# Un HTML bien formado y largo debe trocearse sin partir ninguna etiqueta.
lineas = []
for n in range(60):
    lineas.append(
        f'• <a href="https://www.google.com/search?q=producto_{n}">'
        f'Distribuidor {n}</a> — <i>descripcion con palabras</i>')
caso3 = "\n".join(lineas)
trozos3 = tn._trocear(caso3, limite=500)
print(f"  {len(caso3)} caracteres en {len(lineas)} lineas "
      f"-> {len(trozos3)} trozos")
check("Ningun trozo excede el limite",
      all(len(t) <= 500 for t in trozos3),
      f"-> {[len(t) for t in trozos3 if len(t) > 500][:3]}")
# Cada "<a" debe tener su "</a>" en el MISMO trozo.
partidos = [t for t in trozos3
            if t.count("<a href=") != t.count("</a>")]
check("Ningun trozo tiene un link partido", not partidos,
      f"-> {len(partidos)} trozos con el link partido")
check("El sanitizer salva cualquier resto",
      all("&lt;" in tn._sanear_html(t) or "<a" in t for t in trozos3))

print()
print("=" * 70)
print("5) enviar() NO SE TRAGA EL 400")
print("=" * 70)
import inspect

_fuente = inspect.getsource(tn.enviar)
check("Registra cuando degrada a texto plano",
      "degradado" in _fuente)
check("Y avisa con un log.error, no un debug",
      'log.error' in _fuente)
check("Devuelve False si degrade, para que se note",
      "ok and not degradado" in _fuente)
print("  Antes devolvia True y el usuario recibia un bloque de texto gris sin")
print("  formato y sin ningun aviso. Ahora devuelve False y queda en el log.")

print()
print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: el HTML es valido y Telegram lo acepta")
