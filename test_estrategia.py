"""
test_estrategia.py - La estrategia de oferta se calcula en Python.

QUE SE PIDIO

Que el porcentaje de descuento se calcule en Python y no lo redacte Gemini:

    descuento_absoluto  = presupuesto_govt - precio_a_ofertar
    porcentaje_descuento = round((descuento_absoluto / presupuesto_govt) * 100, 2)

con el texto correspondiente segun el porcentaje, y una regla en el prompt que
obligue al modelo a usar ese texto sin modificarlo.

POR QUE EL CALCULO VA DESPUES DE LA LLAMADA, Y NO ANTES

El requerimiento decia "antes de llamar a Gemini". No se puede, y no es
criterio: precio_a_ofertar es una SALIDA del modelo.

    L566  se construye el prompt
    L606  se llama a la API
    L780  datos = _extraer_json(texto)
    L841  "precio_oferta_sugerido_usd": _num(datos.get(...))

En el momento de construir el prompt ese numero no existe. Poner algo ahi
seria inventarselo a Kyomoto para que el modelo lo copie, que no es
determinismo sino determinismo con la cifra equivocada.

Ademas el modulo RECALCULA los totales con cantidad x precio unitario porque no
confia en los del modelo. Si la frase se calculara con los numeros crudos y la
cuenta los corrigiese despues, la ficha podria decir "12% por debajo" con un
12% que ya no es el de las cifras de arriba. El texto sale de las MISMAS
variables que se imprimen, que es lo unico que garantiza que cuadren.

Lo que si se hizo en el prompt es quitarle al modelo el poder de redactar esa
frase, que era justo de donde salian las incoherencias.

CASOS QUE LA FORMULA NO CUBRE

La formula tal cual revienta con presupuesto 0 (ZeroDivisionError) y con
presupuesto ausente (TypeError). Y con oferta por encima del presupuesto
devuelve un descuento NEGATIVO, que no esta entre los dos casos pedidos
(== 0 y > 0). Ofertar por encima del techo no tiene sentido, y decir
"-8.53% por debajo" en la ficha seria un absurdo, asi que se trata aparte.
"""
import json
import os
import re as _re
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_estrategia.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "x"
os.environ["GEMINI_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"
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


import gemini_analyzer as ga
import telegram_notify as tn


def pct_esperado(tope, oferta):
    return round(((tope - oferta) / tope) * 100, 2)


print("=" * 74)
print("1) LA FORMULA, CASO POR CASO")
print("=" * 74)

CASOS = [
    (85000.0, 74500.0, "caso del ejemplo"),
    (85000.0, 85000.0, "oferta igual al tope"),
    (85000.0, 72250.0, "15% exacto"),
    (100000.0, 99500.0, "0.5% (fraccion pequena)"),
    (250000.0, 125000.0, "50%"),
    (85000.0, 63675.0, "25.09% (no redondo)"),
]
for tope, oferta, etiqueta in CASOS:
    texto, pct = ga.calcular_estrategia_oferta(tope, oferta)
    esperado = pct_esperado(tope, oferta)
    print("  %-28s tope=%9.2f oferta=%9.2f -> %6.2f%%"
          % (etiqueta, tope, oferta, pct))
    check("  %-26s el %% coincide con la formula" % etiqueta,
          pct == esperado, "-> %s vs %s" % (pct, esperado))
    if pct and pct > 0:
        check("  %-26s el texto lleva el %% con 2 decimales" % etiqueta,
              f"{pct:.2f}%" in texto)
        check("  %-26s y lleva el precio con 2 decimales" % etiqueta,
              f"{oferta:,.2f}" in texto)

print()
print("  El texto completo del caso del ejemplo:")
texto, _ = ga.calcular_estrategia_oferta(85000.0, 74500.0)
print("    %s" % texto)
check("Empieza como se pidio: 'Ofertar a {precio} USD.'",
      texto.startswith("Ofertar a 74,500.00 USD."), "-> %r" % texto[:40])
check("Sigue con 'Con este monto nos mantenemos un'",
      "Con este monto nos mantenemos un" in texto)
check("Y termina con 'para asegurar alta competitividad'",
      "para asegurar alta competitividad" in texto)

print()
print("=" * 74)
print("2) LOS DOS CASOS DE LA REGLA")
print("=" * 74)
t0, p0 = ga.calcular_estrategia_oferta(85000.0, 85000.0)
print("  porcentaje == 0:")
print("    %s" % t0)
check("Usa el texto del tope del presupuesto",
      t0.startswith("Ofertar al tope del presupuesto estimado del gobierno"))
check("Y dice que maximiza el margen", "maximizar el margen" in t0)
check("Y el porcentaje es 0", p0 == 0, "-> %s" % p0)
check("Y NO dice 'por debajo', porque no esta por debajo",
      "por debajo" not in t0)

t1, p1 = ga.calcular_estrategia_oferta(85000.0, 74500.0)
print("  porcentaje > 0:")
print("    %s" % t1[:96])
check("Usa la frase del porcentaje", "12.35%" in t1, "-> %s" % p1)
check("Y NO usa el texto del tope", not t1.startswith("Ofertar al tope"))

print()
print("=" * 74)
print("3) CASOS LIMITE QUE LA FORMULA NO CUBRE")
print("=" * 74)
print("  presupuesto 0: la formula daria ZeroDivisionError")
try:
    t, p = ga.calcular_estrategia_oferta(0, 50000)
    check("No revienta con tope 0", True)
    check("Y no inventa un porcentaje", p is None, "-> %s" % p)
    check("Y el texto lo explica", "no publica un presupuesto" in t)
    print("    -> pct=%s, %s..." % (p, t[:58]))
except ZeroDivisionError:
    check("No revienta con tope 0", False, "-> ZeroDivisionError")

print()
print("  oferta por ENCIMA del presupuesto: la formula da negativo")
# (85000 - 92250) / 85000 * 100 = -8.5294 -> round = -8.53
tn_neg, pn = ga.calcular_estrategia_oferta(85000.0, 92250.0)
print("    %s" % tn_neg[:120])
check("El porcentaje sale negativo", pn == -8.53, "-> %s" % pn)
check("Y el texto NO dice 'por debajo' de algo negativo",
      "por debajo" not in tn_neg)
check("Sino que avisa de que se supera el techo",
      "supera el presupuesto" in tn_neg and "ATENCION" in tn_neg)
check("Y da la magnitud del exceso", "8.53%" in tn_neg)

print()
print("  tipos raros (lo que puede devolver una IA mal formada):")
# "cadenas numericas" SI es valido: una IA puede mandar numeros como cadena.
for etiqueta, tope, oferta, debe_pct in (
        ("cadenas numericas", "85000", "74500", True),
        ("None en la oferta", 85000, None, False),
        ("None en el tope", None, 74500, False),
        ("los dos None", None, None, False),
        ("tope vacio", "", 74500, False),
        ("oferta vacia", 85000, "", False),
        ("listas", [], [], False),
        ("texto no numerico", "mucho", "poco", False)):
    try:
        txt, p = ga.calcular_estrategia_oferta(tope, oferta)
        check("Aguanta %-18s" % etiqueta, isinstance(txt, str) and bool(txt),
              "-> devolvio %r" % txt[:40])
        check("  %-18s sin porcentaje inventado" % etiqueta,
              (p is not None) == debe_pct, "-> pct=%s" % p)
        if not debe_pct:
            check("  %-18s lo dice con claridad" % etiqueta,
                  "tope del presupuesto" in txt, "-> %r" % txt[:60])
        print("    %-18s -> pct=%-7s | %s" % (etiqueta, p, txt[:42]))
    except Exception as e:
        check("Aguanta %s" % etiqueta, False, "-> %s: %s" % (type(e).__name__, e))

print()
print("  El caso que se evita: oferta ausente tratada como 0")
_txt, _p = ga.calcular_estrategia_oferta(85000, None)
check("NO dice '100% por debajo' (eso seria regalar el contrato)",
      "100.00%" not in _txt and "100%" not in _txt, "-> %r" % _txt[:80])
check("Y no dice 'Ofertar a 0.00 USD'", "0.00 USD" not in _txt,
      "-> %r" % _txt[:80])

print()
print("=" * 74)
print("4) LA FRASE SOBRESCRIBE A LA DEL MODELO")
print("=" * 74)


class RespuestaFalsa:
    def __init__(self, texto):
        self.text = texto
        self.candidates = []


class ModelsFalso:
    def __init__(self, cuerpo):
        self.cuerpo = cuerpo
        self.prompt = ""

    def generate_content(self, model, contents, config=None, **kw):
        self.prompt = str(contents)
        return RespuestaFalsa(json.dumps(self.cuerpo, ensure_ascii=False))


RESPUESTA = {
    "viable": True, "motivo_descarte": "",
    "producto": "Kits de sellos", "modelo_especifico": "NSN 5330-01-586",
    "unidad_medida": "KIT", "cantidad_total": 50,
    "especificacion_tecnica_clave": "EPDM 70 Shore A",
    "lugar_entrega": "Philadelphia / PA / UNITED STATES",
    "precio_unitario_costo": 1200.0,
    "precio_unitario_oferta": 1490.0,
    # Sin esto, precio_oferta_sugerido_usd cae al "or valor" y la oferta queda
    # IGUAL al presupuesto: descuento 0 y el texto del tope, que por diseno
    # no lleva porcentaje. El codigo estaria bien y el test pediria una frase
    # que en ese caso no existe.
    "precio_oferta_sugerido_usd": 74500.0,
    "valor_contrato_usd": 85000.0,
    "costo_factoring_usd": 2607.5,
    # Lo que el modelo "redacta" y que NO debe ganar:
    "estrategia_oferta": "Ofertar con 40% de descuento para asegurar el contrato",
    "razonamiento_oferta": "Nos mantenemos un 40.00% por debajo del tope",
    "busquedas_distribuidores": ["EPDM seal kit"],
    "query_google_proveedores": "EPDM seal kit wholesale distributor usa",
    "distribuidores_candidatos": [],
    "nivel_riesgo": "medio", "preguntas_criticas": [], "observaciones": "",
}

OPORTUNIDAD = {
    "noticeId": "estr1", "title": "53--PARTS KIT,SEAL",
    "solicitationNumber": "N00019-26-R-0099", "naicsCode": "332999",
    "classificationCode": "5330", "fullParentPathName": "Department of the Navy",
    "responseDeadLine": "2026-11-04T17:00:00-04:00",
    "uiLink": "https://sam.gov/x/view",
}

ga._cliente = type("C", (), {})()
_models = ModelsFalso(RESPUESTA)
ga._cliente.models = _models

salida = ga.analizar(OPORTUNIDAD, "Kits de sellos EPDM para bomba.", "Philadelphia")

print("  Lo que el modelo puso:")
print("    %s" % RESPUESTA["razonamiento_oferta"])
print("  Lo que sale tras el analisis:")
print("    %s" % salida["razonamiento_oferta"][:110])
print()

estr = salida["razonamiento_oferta"]
check("La frase del modelo NO gana", "40" not in estr, "-> %r" % estr[:80])
check("Y sale la calculada por Python", estr.startswith("Ofertar a"))
check("Y los dos campos reciben la misma frase",
      salida["estrategia_oferta"] == estr)

oferta = salida["precio_oferta_sugerido_usd"]
valor = salida["valor_contrato_usd"]
esperado = pct_esperado(valor, oferta)
print()
print("  Cifras finales de la ficha: valor=%s oferta=%s" % (valor, oferta))
print("  El %% que anuncia la frase: %s" % f"{esperado:.2f}%")
check("El %% del texto es el de las cifras que se imprimen",
      f"{esperado:.2f}%" in estr, "-> %r" % estr[:100])
check("Y el precio del texto es el de la ficha", f"{oferta:,.2f}" in estr)

print()
print("=" * 74)
print("5) EL PROMPT YA NO LE PIDE REDACTARLO")
print("=" * 74)
prompt = _models.prompt
check("La regla de no redactar llega al prompt", "NO LA REDACTES" in prompt)
check("Y se le dice que la sobreescribe Python", "sobreescribe" in prompt)
check("Y se le explica por que, para que no lo ignore",
      "no va a cuadrar" in prompt or "dos cifras distintas" in prompt)
check("Y ya no se le pide el metodo para llegar al margen",
      "el metodo para llegar a ese margen" not in prompt,
      "-> sigue pidiendoselo, se contradiria con la regla nueva")
check("El esquema sigue declarando los dos campos", "estrategia_oferta" in prompt)

print()
print("=" * 74)
print("6) EN LA FICHA, SIN REPETIR EL PRECIO")
print("=" * 74)

FICHA = {
    "title": "53--PARTS KIT,SEAL REPLACEMENT",
    "solicitation": "N00019-26-R-0099",
    "agencia": "Department of the Navy", "set_aside": "",
    "limite": "2026-11-04T17:00:00-04:00",
    "producto": "Kits de sellos", "modelo_especifico": "NSN 5330-01-586",
    "unidad_medida": "KIT", "cantidad_total": 50,
    "especificacion_tecnica_clave": "EPDM",
    "lugar_entrega": "Philadelphia / PA / UNITED STATES",
    "valor_contrato_usd": 85000.0, "costo_total_usd": 60000.0,
    "ganancia_neta_usd": 11242.50, "margen_neto_porcentaje": 15.1,
    "precio_oferta_sugerido_usd": 74500.0,
    "query_google_proveedores": "EPDM seal kit wholesale distributor usa",
    "busquedas_distribuidores": ["EPDM seal kit"],
    "distribuidores_candidatos": [], "sin_descripcion": False,
    "ui_link": "https://sam.gov/x/view",
}

estr, _p = ga.calcular_estrategia_oferta(85000.0, 74500.0)

# --- Rama nueva: la frase empieza por "Ofertar a" ---
h = tn.formatear_analisis(dict(FICHA, razonamiento_oferta=estr))
linea = [l for l in h.split("\n") if "12.35%" in l]
print("  Con la frase calculada por Python:")
print("    %s" % (linea[0][:130] if linea else "NO ENCONTRADA"))
check("No sale el patron '(Ofertar a ... (Ofertar a'",
      not _re.search(r"\(Ofertar a", h), "-> sigue duplicado")
check("Y el precio sale dos veces: tabla y estrategia",
      h.count("74,500.00") == 2, "-> %d veces" % h.count("74,500.00"))
check("Y el porcentaje sigue visible", "12.35%" in h)
check("Y no se antepone el precio de la plantilla",
      "Ofertar a <b>$74,500.00 USD</b>" not in h)

# --- Rama vieja: el texto del modelo NO empezaba por "Ofertar a" ---
h2 = tn.formatear_analisis(dict(
    FICHA, razonamiento_oferta="Vamos 12% bajo el tope del gobierno"))
print("  Con el texto viejo, que no empieza por 'Ofertar a':")
print("    %s" % ([l for l in h2.split("\n") if "Ofertar a <b>" in l][0][:130]))
check("Ahi si se antepone el precio de la plantilla",
      "Ofertar a <b>$74,500.00 USD</b>" in h2)
check("Y la explicacion va entre parentesis",
      "(Vamos 12% bajo el tope del gobierno)" in h2)
check("Y tambien sale dos veces: tabla y estrategia",
      h2.count("74,500.00") == 2, "-> %d veces" % h2.count("74,500.00"))

# --- Y sin estrategia ---
h3 = tn.formatear_analisis(dict(FICHA, razonamiento_oferta="", estrategia_oferta=""))
check("Sin estrategia solo sale el precio de la plantilla",
      h3.count("74,500.00") == 2 and "()" not in h3,
      "-> %d veces" % h3.count("74,500.00"))

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
print("RESULTADO: la estrategia la calcula Python y no el modelo")
print("=" * 74)