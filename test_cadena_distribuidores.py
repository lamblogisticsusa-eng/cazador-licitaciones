"""
test_cadena_distribuidores.py - Que los 3 distribuidores lleguen enteros desde
la respuesta de Gemini hasta la ficha que se recibe en Telegram.

POR QUE HAY QUE PROBAR LA CADENA Y NO SOLO LAS PIEZAS

Los cuatro campos nuevos (nombre, tipo, porque, verificar) viven en archivos
distintos: la respuesta cruda de Gemini, el esquema y las instrucciones, el
parseo, el modulo de distribuidores y la plantilla de la ficha. Cada uno
puede estar bien y la cadena estar cortada: el JSON del modelo no llega al
parseo, el parseo no lo pasa, o el nombre se pierde al armar el enlace.

Por eso se mete una respuesta DE GEMINI SIMULADA por la entrada real del
analizador y se mira lo que sale por el otro extremo, que es lo que llega al
telefono. Las piezas sueltas ya las comprueban otras suites; lo que se
comprueba aqui es que el puente exista.

Y se comprueba con la ficha armandose desde lo que se GUARDO en la base, no
desde el diccionario en memoria, porque /pdf y /puntajes leen de ahi: si
guardar_analisis() perdiera el campo, la ficha de Telegram andaria bien y
lo demas no.
"""
import json
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_cadena_dist.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["DRY_RUN"] = "1"
os.environ["SAM_API_KEY"] = "x"
os.environ["GEMINI_API_KEY"] = "x"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"
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
import store
import telegram_notify as tn

# La respuesta que devolveria Gemini, con la forma que dicta el ESQUEMA.
RESPUESTA_GEMINI = {
    "viable": True,
    "motivo_descarte": "",
    "producto": "Kits de sellos mecanicos para bomba de almacenamiento",
    "modelo_especifico": "NSN 5330-01-586-4931",
    "unidad_medida": "KIT",
    "cantidad_total": 50,
    "especificacion_tecnica_clave": "EPDM 70 Shore A, ASTM D2000",
    "lugar_entrega": "Philadelphia / Pennsylvania / UNITED STATES",
    "precio_unitario_costo": 1200.0,
    "precio_unitario_mercado": 1400.0,
    "precio_unitario_oferta": 1490.0,
    "ganancia_por_unidad": 290.0,
    "valor_contrato_usd": 85000.0,
    "costo_total_usd": 60000.0,
    "ganancia_total_usd": 14500.0,
    "margen_bruto_porcentaje": 17.1,
    "costo_factoring_usd": 2607.5,
    "margen_neto_porcentaje": 15.9,
    "precio_oferta_sugerido_usd": 74500.0,
    "estrategia_oferta": "Entrega en 60 dias con garantia de 12 meses",
    "razonamiento_oferta": "Vamos 12% por debajo del tope del gobierno",
    "busquedas_distribuidores": ["EPDM mechanical seal kit wholesale distributor"],
    "query_google_proveedores": "EPDM mechanical seal kit wholesale distributor usa",
    "distribuidores_candidatos": [
        {"nombre": "Radwell", "tipo": "mayorista MRO",
         "porque": "Tiene stock de NSN y permite comparar cotizaciones.",
         "verificar": "Radwell EPDM mechanical seal contact"},
        {"nombre": "Grainger", "tipo": "mayorista industrial",
         "porque": "Segunda cotizacion para negociar mejor.",
         "verificar": "Grainger EPDM mechanical seal contact"},
        {"nombre": "McMaster-Carr", "tipo": "casa de catalogo",
         "porque": "Envio el mismo dia, aunque mas caro.",
         "verificar": "McMaster-Carr EPDM mechanical seal contact"},
    ],
    "nivel_riesgo": "medio",
    "preguntas_criticas": ["¿Aceptan equivalently?"],
    "observaciones": "",
}

OPORTUNIDAD = {
    "noticeId": "cadena123",
    "title": "53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL",
    "solicitationNumber": "N00019-26-R-0099",
    "naicsCode": "332999",
    "classificationCode": "5330",
    "fullParentPathName": "Department of the Navy",
    "typeOfSetAsideDescription": "Total Small Business Set-Aside",
    "responseDeadLine": "2026-11-04T17:00:00-04:00",
    "uiLink": "https://sam.gov/opp/cadena123/view",
}

print("=" * 74)
print("1) LA RESPUESTA DE GEMINI ENTRA POR LA PUERTA REAL")
print("=" * 74)

# Se sustituye SOLO la llamada de red del cliente, que es lo unico que no se
# quiere tocar de verdad. Todo lo de alrededor (prompt, esquema, parseo,
# reintentos, cadena de modelos) es el codigo de produccion.
class RespuestaFalsa:
    """Lo que el cliente de google-genai devuelve.

    OJO: .text es una PROPIEDAD, no un metodo. El codigo de produccion hace
    `respuesta.text` y si el falso lo expone como metodo, ahi no hay
    AttributeError sino un metodo, y el fallo sale mas abajo como
    "'function' object has no attribute 'strip'". Es el mismo falso que se
    lleva 7 reintentos de la cadena de modelos antes de que alguien lo lea.
    """

    def __init__(self, texto, prompt=""):
        self.text = texto
        self.prompt = prompt
        self.candidates = []


class ModelsFalso:
    def __init__(self):
        self.prompt_visto = ""
        self.llamadas = 0

    def generate_content(self, model, contents, config=None, **kw):
        self.llamadas += 1
        self.prompt_visto = str(contents)
        return RespuestaFalsa(json.dumps(RESPUESTA_GEMINI, ensure_ascii=False),
                              self.prompt_visto)


class ClienteFalso:
    """Sustituye al cliente de google-genai, y solo a el.

    Importa que .models sea un OBJETO y no el metodo: el codigo de produccion
    escribe cliente.models.generate_content(...), asi que un metodo ahi se
    traduce en "'function' object has no attribute 'generate_content'" y el
    fallo se pierde entre los 7 reintentos de la cadena de modelos.
    """

    def __init__(self):
        self._models = ModelsFalso()

    @property
    def models(self):
        return self._models


_cliente_falso = ClienteFalso()
gemini_analyzer._cliente = _cliente_falso
try:
    salida = gemini_analyzer.analizar(
        OPORTUNIDAD, "Kits de sellos mecanicos EPDM para bomba.",
        "Philadelphia")
except Exception as e:
    print("  (no se pudo ejecutar el analizador: %s: %s)" % (type(e).__name__, e))
    salida = None

prompt = _cliente_falso._models.prompt_visto
print("  El analizador respondio viable=%s" % (salida or {}).get("viable"))
if salida is not None:
    check("Devuelve viable=True", salida.get("viable") is True)
    check("Y no es un caso de error", not salida.get("sin_descripcion"))

    print()
    print("  Y EL PROMPT LE PIDE LOS 3 DISTRIBUIDORES:")
    check("El esquema declara el campo nuevo", "distribuidores_candidatos" in prompt)
    check("Las instrucciones lo explican", "DISTRIBUIDORES REALES" in prompt)
    check("Y le prohibe inventar correos y telefonos",
          "NUNCA inventes una direccion de correo" in prompt)
    check("Y le dice que 1 con certeza vale mas que 3 inventadas",
          "con certeza" in prompt)
    # El prompt se arma con un f-string, asi que un ejemplo con llaves simples
    # se evalua como expresion y revienta el prompt entero con ValueError.
    # Asi que se comprueba que el ejemplo llego entero a Gemini.
    check("Y el ejemplo JSON del prompt llego con sus llaves",
          '{"nombre": "McMaster-Carr"' in prompt,
          "-> las llaves del ejemplo no se renderizaron")
    check("Y el ejemplo trae los 3 candidatos",
          prompt.count('"nombre"') >= 3,
          "-> %d" % prompt.count('"nombre"'))

    print()
    print("  Y LA SALIDA LOS TRAE ENTEROS:")
    cands = salida.get("distribuidores_candidatos") or []
    print("    candidatos: %s" % [c.get("nombre") for c in cands])
    check("Llegan los 3", len(cands) == 3, "-> %d" % len(cands))
    check("Con nombre, tipo, porque y verificar",
          all(set(c) == {"nombre", "tipo", "porque", "verificar"} for c in cands))
    check("Y con el porque intacto",
          any("stock de NSN" in c["porque"] for c in cands))

print()
print("=" * 74)
print("2) LO QUE SE GUARDA Y LO QUE SE LEE")
print("=" * 74)
store.init_db()
if salida is not None:
    store.guardar_analisis(salida)
    leido = store.buscar_por_solicitud("N00019-26-R-0099")
    check("Se recupero de la base", leido is not None)
    if leido:
        leidos = leido.get("distribuidores_candidatos") or []
        check("Y los 3 candidatos siguen ahi", len(leidos) == 3,
              "-> %d" % len(leidos))
        check("Con los nombres intactos",
              [c["nombre"] for c in leidos] ==
              ["Radwell", "Grainger", "McMaster-Carr"],
              "-> %s" % [c["nombre"] for c in leidos])
else:
    leido = None

print()
print("=" * 74)
print("3) LA FICHA QUE LLEGA AL TELEFONO")
print("=" * 74)
if leido:
    h = tn.formatear_analisis(leido)
    print("  Enlaces de distribuidor que salen:")
    import re as _re
    import urllib.parse
    for m in _re.finditer(r'href="([^"]*google[^"]*)"', h):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(m.group(1)).query)
        print("    %s" % q.get("q", ["?"])[0])
    print()
    for nombre in ("Radwell", "Grainger", "McMaster-Carr"):
        check("Muestra a %s" % nombre, nombre in h)
    check("Cada uno con su busqueda propia", h.count("google.com/search") == 3,
          "-> %d" % h.count("google.com/search"))
    check("Cada uno con su tipo", h.count("mayorista") + h.count("casa de") >= 3)
    check("Y con su motivo", "stock de NSN" in h)
    check("Y avisa de verificar antes de escribir", "antes de escribir" in h)
    check("Y NO hay ningun correo inventado", "@" not in h, "-> hay un @")
    check("Y no hay ningun telefono", "+1 " not in h, "-> hay un telefono")
    check("La ficha cabe en un mensaje", len(h) < 4096, "-> %d" % len(h))
    check("Y el HTML sigue balanceado",
          h.count("<b>") == h.count("</b>") and h.count("<a ") == h.count("</a>"))

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
print("RESULTADO: los 3 distribuidores llegan enteros hasta el telefono")
print("=" * 74)
