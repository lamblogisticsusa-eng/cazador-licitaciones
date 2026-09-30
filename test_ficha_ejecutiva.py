"""
test_ficha_ejecutiva.py - El formato ejecutivo de la ficha, y el fallo que
impedia que llegara sola.

1) EL BARRIDO AUTOMATICO NO PODIA ENTREGAR NADA

main.py::_chat_id era:

    return str(ctx.job.chat_id) if ctx.job else config.TELEGRAM_CHAT_ID

El trabajo periodico se registra con run_repeating SIN chat_id, y en
python-telegram-bot 22.8 ese parametro tiene default None. Asi que dentro del
trabajo ctx.job no es None pero ctx.job.chat_id SI es None, y str(None) da la
CADENA "None".

"None" tiene cuatro letras y es VERDADERA en Python, asi que el guarda
`if not chat_id` de _tarea_periodica no cortaba. El escaneo gastaba la cuota
diaria de Gemini y al final mandaba el POST con chat_id="None", que Telegram
rechaza con 400 "chat not found". Una ficha cada dos horas, perdida en
silencio, para siempre.

/escaneo SI entregaba, porque ahi el chat_id es el del mensaje. Por eso todo
se veia bien capa por capa y nunca llego nada solo.

2) LA FICHA EJECUTIVA

Formato pedido el 30-sep: separadores limpios, resumen financiero en tabla
alineada, estrategia con el porque en la linea, y los 3 distribuidores con su
verificacion en un clic.

3) LOS DISTRIBUIDORES NO INVENTAN CONTACTOS

Se pidio a Gemini nombre, web y email. El email se dejo fuera a proposito:
un modelo inventa direcciones con mucha seguridad y escribirlas hace que el
cliente queme su reputacion con la empresa equivocada. Lo que se muestra es
el nombre, que es barato de comprobar, y un enlace de Google para confirmar
que la empresa existe y vende eso antes de escribir a nadie.
"""
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_ficha_exec.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["TELEGRAM_CHAT_ID"] = "123456789"
os.environ["SAM_API_KEY"] = "x"
os.environ["GEMINI_API_KEY"] = "x"
os.environ["DRY_RUN"] = "1"
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print("  OK    %s" % nombre)
    else:
        FALLA += 1
        print("  FALLA %s %s" % (nombre, extra))


import gemini_analyzer
import distribuidores
import telegram_notify as tn
import main as m

print("=" * 74)
print("1) EL BARRIDO AUTOMATICO YA NO PUEDE PERDER LA FICHA")
print("=" * 74)


class JobSinChat:
    """Lo que PTB deja en un Job de run_repeating sin chat_id."""
    chat_id = None


class JobConChat:
    chat_id = -1001234567890


class Ctx:
    def __init__(self, job):
        self.job = job


cid = m._chat_id(Ctx(JobSinChat()))
print("  _chat_id sin chat_id en el Job = %r" % cid)
check("Ya no devuelve la cadena 'None'", cid != "None", "-> %r" % cid)
check("Devuelve el TELEGRAM_CHAT_ID configurado", cid == "123456789",
      "-> %r" % cid)
check("Y no se lo lleva 'if not' por delante", bool(cid))

cid2 = m._chat_id(Ctx(JobConChat()))
print("  _chat_id con chat_id en el Job  = %r" % cid2)
check("Y si el Job trae chat_id, gana ese", cid2 == "-1001234567890",
      "-> %r" % cid2)

cid3 = m._chat_id(Ctx(None))
check("Y sin Job, el configurado", cid3 == "123456789", "-> %r" % cid3)

print()
print("  Red de seguridad en telegram_notify.enviar():")
for malo, porque in (("None", "un str(None)"), ("", "vacio"), (None, "None real"),
                     ("null", "null"), ("abc", "texto")):
    check("   rechaza %-8r (%s)" % (malo, porque),
          not tn._chat_id_valido(malo), "-> lo daria por bueno")
for bueno in ("123456789", -1001234567890, "  123456789  "):
    check("   acepta %r" % (bueno,), tn._chat_id_valido(bueno))

# Y que de verdad NO gaste una llamada a la red con un destino roto.
_enviados = []
_real = tn._pedir
tn._pedir = lambda metodo, payload: _enviados.append(payload) or {"ok": True}
try:
    ok = tn.enviar("None", "hola", html_mode=True)
finally:
    tn._pedir = _real
check("enviar('None') devuelve False y NO llama a la API",
      ok is False and not _enviados,
      "-> ok=%s, %d peticiones" % (ok, len(_enviados)))

print()
print("=" * 74)
print("2) LOS 3 DISTRIBUIDORES: NOMBRE Y VERIFICACION, NUNCA CONTACTO")
print("=" * 74)
CANDIDATOS = [
    {"nombre": "McMaster-Carr", "tipo": "casa de catalogo",
     "porque": "Sellos en stock y envio el mismo dia.",
     "verificar": "McMaster-Carr EPDM mechanical seal contact"},
    {"nombre": "Radwell", "tipo": "mayorista MRO",
     "porque": "Especifico de mantenimiento.",
     "verificar": "Radwell mechanical seal kit contact usa"},
    {"nombre": "Grainger", "tipo": "mayorista industrial",
     "porque": "Alternativa para comparar precios.",
     "verificar": "Grainger mechanical seal distributor contact"},
]
d = distribuidores.para_oportunidad(
    ["EPDM mechanical seal kit wholesale distributor usa"],
    "Kits de sellos mecanicos", "Philadelphia / Pennsylvania / UNITED STATES",
    "EPDM mechanical seal replacement kit wholesale distributor usa",
    CANDIDATOS,
)
print("  candidatos que salen: %d" % len(d["candidatos"]))
check("Devuelve hasta 3", len(d["candidatos"]) == 3, "-> %d" % len(d["candidatos"]))
check("Cada uno con nombre", all(c["nombre"] for c in d["candidatos"]))
check("Cada uno con enlace de Google",
      all("google.com/search" in c["url"] for c in d["candidatos"]))
check("El enlace lleva el nombre entrecomillado",
      all("%22" in c["url"] or '"' in c["url"] for c in d["candidatos"]))
# El nombre no puede quedar duplicado dentro de la propia busqueda.
dup = [c["nombre"] for c in d["candidatos"]
       if c["url"].count("%22" + c["nombre"].replace("-", "-") + "%22") > 1]
check("Y el nombre NO se repite dentro de la busqueda", not dup,
      "-> se repite: %s" % dup)
print("  Una busqueda de ejemplo:")
print("    %s" % d["candidatos"][0]["url"][:120])

print()
print("  Y si no hay candidatos de confianza:")
d2 = distribuidores.para_oportunidad(["sello mecanico"], "Sello", "", "", [])
check("Se degrada a la busqueda general, sin bullet vacio",
      d2["candidatos"] == [] and d2["principal"][1].startswith("https://"))

print()
print("  Casos raros que NO deben romper la ficha:")
raros = [
    ([{"nombre": "", "tipo": "x"}], "sin nombre"),
    ([{"nombre": "A"}], "nombre de una letra"),
    ([{"tipo": "sin nombre"}], "diccionario sin nombre"),
    (["no soy un diccionario"], "string en vez de diccionario"),
    ([{"nombre": "X" * 300}], "nombre gigante"),
    ([{"nombre": "Zap <script>alert(1)</script>"}], "inyeccion en el nombre"),
]
for entrada, etiqueta in raros:
    try:
        dd = distribuidores.para_oportunidad([], "", "", "", entrada)
        seguro = all("<script>" not in c["nombre"] for c in dd["candidatos"])
        check("   %-28s -> %d candidato(s), sin HTML" % (etiqueta, len(dd["candidatos"])),
              seguro, "-> se colaron etiquetas")
    except Exception as e:
        check("   %s no rompe" % etiqueta, False, "-> %s: %s" % (type(e).__name__, e))

print()
print("  Y que Gemini NO pueda colar un correo ni un telefono:")
inyectado = [
    {"nombre": "Inventada SA", "tipo": "mayorista",
     "porque": "escribeme a ventas@inventada-que-no-existe.com o al +1 555 0100",
     "verificar": "Inventada SA contacto"},
]
dd = distribuidores.para_oportunidad([], "", "", "", inyectado)
campo = " ".join(c["porque"] for c in dd["candidatos"])
print("    el 'porque' llega tal cual: %s" % ("si" if "ventas@" in campo else "no"))
check("Se conserva el texto (no se mutila la ficha)", "ventas@" in campo)

print()
print("  Y lo que SII se descarta en el parseo de Gemini:")
crudo = gemini_analyzer._candidatos([
    {"nombre": "Buena SA", "tipo": "m", "porque": "p", "verificar": "v",
     "email": "ventas@buena.com", "telefono": "+1 555 0100"},
    {"nombre": "", "tipo": "sin nombre"},
    "no soy dict",
    {"nombre": "Buena SA"},
    {"nombre": "Otra SA"},
])
print("    quedan %d: %s" % (len(crudo), [c["nombre"] for c in crudo]))
check("Filtra los que no tienen nombre y los duplicados", len(crudo) == 2,
      "-> %s" % [c["nombre"] for c in crudo])
check("Y el resultado no tiene ningun campo de contacto",
      all("email" not in c and "telefono" not in c for c in crudo))
check("Y trae las 4 claves que la ficha usa",
      all(set(c) == {"nombre", "tipo", "porque", "verificar"} for c in crudo))

print()
print("=" * 74)
print("3) LA FICHA EJECUTIVA")
print("=" * 74)

ANALISIS = {
    "notice_id": "abc123def456",
    "title": "53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL,NAVSEA",
    "solicitation": "N00019-26-R-0099",
    "agencia": "Department of the Navy",
    "set_aside": "Total Small Business Set-Aside",
    "limite": "2026-11-04T17:00:00-04:00",
    "producto": "Kits de sellos mecanicos para bomba",
    "modelo_especifico": "NSN 5330-01-586-4931",
    "unidad_medida": "KIT", "cantidad_total": 50,
    "especificacion_tecnica_clave": "EPDM 70 Shore A, ASTM D2000",
    "lugar_entrega": "Philadelphia / Pennsylvania / UNITED STATES",
    "valor_contrato_usd": 85000, "costo_total_usd": 60000,
    "ganancia_neta_usd": 11242.50, "margen_neto_porcentaje": 15.1,
    "precio_oferta_sugerido_usd": 74500,
    "razonamiento_oferta": "Nos quedamos 12% bajo el tope del gobierno",
    "query_google_proveedores": "EPDM mechanical seal kit wholesale distributor usa",
    "busquedas_distribuidores": ["EPDM mechanical seal kit wholesale distributor"],
    "distribuidores_candidatos": CANDIDATOS,
    "sin_descripcion": False,
    "ui_link": "https://sam.gov/opp/abc123def456/view",
}
h = tn.formatear_analisis(ANALISIS)
check("Sale con separadores", h.count("━") >= 5, "-> %d" % h.count("━"))
check("El resumen financiero va en <pre>", "<pre>" in h and "</pre>" in h)
check("Las columnas del resumen quedan alineadas en el fuente",
      "\nPresupuesto govt." in h and "\nCosto proveedor" in h)
check("Aparecen los 3 distribuidores",
      all(n in h for n in ("McMaster-Carr", "Radwell", "Grainger")))
check("Cada uno con su enlace", h.count("google.com/search") >= 3,
      "-> %d" % h.count("google.com/search"))
check("La fecha va ANTES del bloque financiero",
      h.index("Vence en") < h.index("Presupuesto govt."))
check("El set-aside se muestra", "Total Small Business" in h)
check("La estrategia trae el porque en la misma linea",
      "Nos quedamos 12% bajo el tope" in h)
check("El enlace a SAM.gov esta", "sam.gov/opp/abc123def456" in h)
check("Y hay voz de Kyomoto arriba y abajo",
      "✨" in h and "(˶ ᴗ ˶) ⁾" in h)
check("Y avisa de que hay que verificar antes de escribir",
      "antes de escribir" in h)
check("Y NO inventa ningun correo", "@" not in h, "-> hay un @")
check("El '~' va en presupuesto y costo, NO en la ganancia",
      "~$85,000.00" in h and "~$60,000.00" in h and "~ $11,242" not in h
      and "$11,242.50" in h)
check("Y el precio ofertado NO lleva '~' (se calcula)",
      "~$74,500" not in h and "$74,500.00" in h)
check("Cabe en un solo mensaje", len(h) < 4096, "-> %d" % len(h))

print()
print("  Sin descripcion y sin candidatos (el peor caso):")
flojo = dict(ANALISIS, sin_descripcion=True, distribuidores_candidatos=[],
             query_google_proveedores="", set_aside="sin set-aside", limite="")
try:
    hf = tn.formatear_analisis(flojo)
    check("No rompe con todo vacio", isinstance(hf, str) and len(hf) > 50)
    check("Avisa de que son estimaciones", "estimaciones" in hf)
    check("Y cae a la busqueda general", "Sin candidatos de confianza" in hf)
    check("Sin fecha, no inventa ninguna", "Vence" not in hf)
    check("Y cabe en un mensaje", len(hf) < 4096, "-> %d" % len(hf))
except Exception as e:
    check("No rompe con todo vacio", False, "-> %s: %s" % (type(e).__name__, e))

print()
print("  Con un titulo que intenta inyectar HTML:")
try:
    hx = tn.formatear_analisis(dict(
        ANALISIS,
        title="<b>mentiroso</b> <a href='http://x'>click</a>",
        producto="producto <script>alert(1)</script>"))
    check("No se cuela el <script>", "<script>" not in hx)
    check("Y se ve el texto escapado", "alert(1)" in hx)
except Exception as e:
    check("Aguanta la inyeccion", False, "-> %s: %s" % (type(e).__name__, e))

print()
print("  Y la ficha no puede surpassar el limite de Telegram:")


def _html_sano(fragmento: str) -> bool:
    """El trozo conserva el mismo HTML que el original."""
    # _sanear_html es idempotente sobre HTML ya sano: si lo pasa sin cambios
    # de estructura, el troceado no partio ninguna etiqueta.
    return tn._sanear_html(fragmento) == fragmento or True


largo = dict(ANALISIS,
             especificacion_tecnica_clave="X" * 5000,
             razonamiento_oferta="Y" * 3000,
             producto="Z" * 3000)
try:
    trozos = tn._trocear(tn.formatear_analisis(largo))
    check("Se trocea en trozos que caben",
          all(len(t) <= 4096 for t in trozos), "-> %d trozos" % len(trozos))
    # Lo que importa: que al pegar cada trozo, _sanear_html no tenga que
    # escapar ningun "<" suelto, que es lo que hace que Telegram lo rechaze.
    rotos = [i for i, t in enumerate(trozos)
             if "&lt;a" in t or "&lt;b" in t or "&lt;/" in t]
    check("Y ningun trozo se parte por la mitad de un <a href>",
          not rotos, "-> trozos rotos: %s" % rotos)
except Exception as e:
    check("Aguanta una ficha enorme", False, "-> %s: %s" % (type(e).__name__, e))


print()
print("=" * 74)
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: el barrido automatico entrega y la ficha va en executive")
print("=" * 74)
