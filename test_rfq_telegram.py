# -*- coding: utf-8 -*-
"""
test_rfq.py - El bloque de RFQ listo para enviar (copy-paste).

QUE SE COMPRUEBA

1) EL BLOQUE SALE EN LA FICHA: cabecera nueva, dentro de <pre>, como ultimo
   bloque de contenido (detras del enlace y delante del cierre de Kyomoto),
   sin mover el orden de las secciones que ya estaban.
2) EL CORREO TIENE LO QUE SE PIDIÓ: asunto con la solicitud, distribuidor #1
   en el saludo, solicitud, descripcion tecnica, NSN o Part Number, cantidad,
   cierre en ingles, Ship-To con ciudad/estado/region, y los cinco pedidos
   (precio unitario y total mayorista, stock y lead time, flete FOB
   Destination con envio sin costo por volumen, CoC o trazabilidad OEM, y la
   frase de los dos dias antes del cierre para presentar la oferta federal).
3) EL DISTRIBUIDOR #1 ES EL GEO-MATCHED: el saludo usa el primero de la
   lista que devuelve distribuidores.para_oportunidad, no el primero que
   trajo el aviso.
4) CERO ESPAÑOL EN EL CORREO: el cuerpo del <pre> va en ingles comercial
   estadounidense; el espanol del mensaje es solo de la interfaz, que es
   donde toca.
5) NUNCA INVENTA: ni un "@" en el mensaje entero, ni un telefono, ni un
   numero de parte fabricado, ni un destino que no se pudo leer (pais solo
   = frase honesta), ni una fecha que no se pudo interpretar.
6) FORMATO Y TAMAÑO: el correo va como texto plano dentro del <pre> (nada
   de etiquetas que se peguen al copiar), lineas legibles, HTML balanceado
   y la ficha entera por debajo del limite duro de 4096 de Telegram.
7) RETROCOMPATIBILIDAD: firmas intactas, FIRMA_RFQ tal cual la pidio el
   cliente, y ni un secreto en el modulo.

Este test NO toca la red: no hay ninguna llamada a Gemini en el bloque, que
es justamente una de las cosas que se comprueban.
"""
import inspect
import os
import re
import sys
import tempfile

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print("  OK    %s" % nombre)
    else:
        FALLA += 1
        print("  FALLA %s %s" % (nombre, extra))


DB = os.path.join(tempfile.gettempdir(), "kyo_test_rfq.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "x"
os.environ["TELEGRAM_BOT_TOKEN"] = "x"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"

import distribuidores
import kyo
import telegram_notify as tn


# =====================================================================
# 1) EL BLOQUE SALE EN LA FICHA
# =====================================================================
print("=" * 76)
print("1) EL BLOQUE SALE EN LA FICHA, AL FINAL")
print("=" * 76)

FICHA = {
    "title": "Batteries, Lead-Acid, 12 Volt, Group 31, Heavy Duty",
    "solicitation": "36C24526Q0123",
    "agencia": "Department of the Air Force",
    "naics": "335911", "psc": "6135",
    "set_aside": "Total Small Business",
    "producto": "Baterias 12V Grupo 31",
    "lugar_entrega": "Norfolk / VA / USA",
    "limite": "2026-11-04T17:00:00-04:00",
    "sin_descripcion": False,
    "valor_contrato_usd": 85000.0,
    "modelo_especifico": "S0594DU0500-S1",
    "unidad_medida": "EA", "cantidad_total": 500,
    "especificacion_tecnica_clave": "12V, Group 31, 1000 CCA, 200 min reserve",
    "precio_unitario_costo": 104.0,
    "precio_unitario_mercado": 139.0,
    "precio_unitario_oferta": 149.0,
    "ganancia_por_unidad": 45.0,
    "costo_total_usd": 52000.0,
    "ganancia_total_usd": 22500.0,
    "margen_bruto_porcentaje": 30.2,
    "costo_factoring_usd": 1820.0,
    "ganancia_neta_usd": 20680.0,
    "margen_neto_porcentaje": 24.3,
    "precio_oferta_sugerido_usd": 74500.0,
    "razonamiento_oferta": "Ofertar 12% bajo el presupuesto del gobierno.",
    "estrategia_oferta": "", "busquedas_distribuidores": ["baterias 12V"],
    "nivel_riesgo": "medio", "preguntas_criticas": [], "observaciones": "",
    "contacto": "john.smith@af.mil",
    "ui_link": "https://sam.gov/workspace/contract/opp/60aa8e3f/view",
    "distribuidores_candidatos": [
        {"nombre": "Grainger", "tipo": "mayorista industrial",
         "porque": "stock de NSN", "verificar": "Grainger batteries"},
        {"nombre": "McMaster-Carr", "tipo": "casa de suministros",
         "porque": "stock", "verificar": "McMaster batteries"},
        {"nombre": "Radwell", "tipo": "mayorista MRO",
         "porque": "stock", "verificar": "Radwell batteries"},
    ],
}

h = tn.formatear_analisis(FICHA)

check("La cabecera nueva esta en la ficha",
      "✉️ <b>RFQ Listo para Enviar (Copy-Paste)</b>" in h, "-> no esta")

pos_rfq = h.find("RFQ Listo para Enviar")
pos_enlace = h.find("Ver ficha en SAM.gov")
pos_cierre = h.find(kyo.CIERRE)
check("Va detras del enlace a SAM.gov", pos_rfq > pos_enlace,
      "-> rfq=%d enlace=%d" % (pos_rfq, pos_enlace))
check("Y delante del cierre de Kyomoto", 0 < pos_rfq < pos_cierre,
      "-> rfq=%d cierre=%d" % (pos_rfq, pos_cierre))
check("El cierre sigue siendo lo ultimo",
      h.rstrip().endswith("<i>%s</i>" % tn._esc(kyo.CIERRE)), "-> %r" % h[-60:])
check("Las secciones de antes siguen ahi",
      all(x in h for x in ("💰 <b>Resumen financiero</b>",
                           "Escenarios de oferta (sensibilidad)",
                           "🏭 <b>Distribuidores a verificar (USA)</b>",
                           "Ver ficha en SAM.gov")))

pres = re.findall(r"<pre>(.*?)</pre>", h, re.S)
check("El correo va dentro de un <pre>", len(pres) >= 2
      and "Subject: Wholesale RFQ" in pres[-1], "-> %d bloques" % len(pres))
check("El PRIMER <pre> sigue siendo el resumen financiero",
      "Presupuesto govt." in pres[0], "-> %r" % pres[0][:60])
check("El de escenarios sigue en el medio",
      "Escenario" in pres[1], "-> %r" % pres[1][:60])

print()
print("  Aislamiento total del bot dentro del <pre>:")

check("El </pre> cierra justo detras de la firma corporativa",
      "Albuquerque, NM 87110\n</pre>" in h, "-> no cierra ahi")
check("Y la firma NO arrastra el EIN", "EIN" not in pres[-1],
      "-> sigue el EIN en el correo")
check("El rotulo de la seccion queda FUERA del <pre>",
      h.index("RFQ Listo para Enviar") < h.index("<pre>\nSubject:"),
      "-> esta dentro")
for frase_bot in ("Kyomoto", "Amo, oportunidad", "(˶", "antes de escribir",
                  "Dirigido al distribuidor", "pasandote la info",
                  "Distribuidores a verificar"):
    check("No se cuela dentro del <pre>: %r" % frase_bot,
          frase_bot not in pres[-1], "-> se colo")
check("Dentro solo hay texto de correo, plano y sin etiquetas",
      all(x not in pres[-1] for x in ("<b>", "<i>", "<a href", "<code>")),
      "-> hay etiqueta dentro")
check("Y el bloque empieza en Subject y acaba en la ciudad de la firma",
      pres[-1].lstrip("\n").startswith("Subject:")
      and pres[-1].rstrip().endswith("Albuquerque, NM 87110"),
      "-> %r ... %r" % (pres[-1][:30], pres[-1][-30:]))


# =====================================================================
# 2) EL CORREO TIENE LO QUE SE PIDIÓ
# =====================================================================
print()
print("=" * 76)
print("2) EL CORREO TIENE LO QUE SE PIDIÓ")
print("=" * 76)

cuerpo = pres[-1]
# textwrap parte las frases largas en varias lineas: para comprobar una
# frase hay que mirarla con los espacios normalizados, o "closing date
# above" nunca se encontraria aunque este escrito completo.
plano = " ".join(cuerpo.split())

check("Asunto SIN el numero de solicitud (confidencialidad de la oferta)",
      plano.startswith("Subject: Wholesale RFQ - Batteries"), "-> %r"
      % plano[:70])
check("Y el numero de solicitud no aparece NI en el asunto NI en el cuerpo",
      "36C24526Q0123" not in cuerpo, "-> se colo")
check("Pero el correo si dice de donde viene el trabajo",
      "in support of a U.S. federal government solicitation" in plano,
      "-> no esta")
check("El equipo SI sigue viendo la solicitud en la ficha (fuera del pre)",
      "36C24526Q0123" in h, "-> desaparecio")
check("Descripcion tecnica del producto (Item)",
      "  Item:         Batteries, Lead-Acid" in cuerpo, "-> no esta")
check("Cantidad con su unidad", "  Quantity:     500 EA" in cuerpo,
      "-> no esta")
check("Fecha de cierre en ingles, con anio y hora",
      "  Closing:      November 4, 2026, 17:00 UTC-04" in cuerpo,
      "-> no esta")
check("Ship-To con ciudad, estado y region",
      "  Ship-To:      Norfolk, VA (South)" in cuerpo, "-> no esta")
check("Modelo exacto en Part / Model",
      "  Part / Model: S0594DU0500-S1" in cuerpo, "-> no esta")

print()
print("  Los cinco pedidos, en viñetas y en ingles comercial:")
check("1) Costo unitario y total mayorista, con descuentos por volumen",
      "Unit and total wholesale pricing for the quantity above" in plano
      and "volume or contract discounts that may apply" in plano,
      "-> no esta")
check("2) Disponibilidad en stock y lead time hasta el envio",
      "Current stock availability and the estimated lead time from order "
      "confirmation to shipment" in plano, "-> no esta")
check("3) Flete terrestre detallado bajo FOB Destination al destino",
      "Itemized ground freight charges under FOB Destination to "
      "Norfolk, VA (South)" in plano, "-> no esta")
check("   Y pidiendo el envio sin costo por volumen",
      "if freight is waived at this volume" in plano, "-> no esta")
check("4) CoC o trazabilidad OEM para pieza tecnica o militar",
      "Certificate of Conformity (CoC)" in plano
      and "OEM traceability" in plano, "-> no esta")
check("5) Los cinco dias antes del cierre para presentar la oferta federal",
      ("five (5) days before the closing date above" in plano
       or re.search(r"by [A-Z][a-z]+ \d+, \d{4}, five \(5\) days before",
                    plano)) and "federal offer on time" in plano,
      "-> no esta")

check("La carta de apertura explica el encargo con tono formal",
      "We are requesting a formal quotation for the item detailed below "
      "in support of a U.S. federal government solicitation" in plano,
      "-> no esta")
check("La lista de requerimientos va en viñetas, no numerada",
      "  • Unit and total wholesale pricing" in cuerpo
      and not re.search(r"^\s*1\. Unit", cuerpo, re.M), "-> no esta")
check("Y con sangria en las lineas que siguen",
      "\n    any volume or contract discounts" in cuerpo, "-> no esta")
check("Despedida formal antes de la firma",
      "Thank you for your consideration." in cuerpo
      and "Respectfully," in cuerpo, "-> no esta")

print()
print("  La firma, tal cual la pidio el cliente:")
for linea in ("Bastian Cordero",
              "Managing Member | L.A.M.B Logistics LLC",
              "Albuquerque, NM 87110"):
    check("  %s" % linea, cuerpo.rstrip().endswith(linea)
          or ("\n" + linea) in cuerpo, "-> no esta")
check("Y SIN el EIN: es un dato fiscal que al distribuidor no le hace "
      "falta para cotizar y este correo se pega en bandejas ajenas",
      "32-0868469" not in cuerpo, "-> se colo el EIN")


# =====================================================================
# 3) EL DISTRIBUIDOR #1 ES EL GEO-MATCHED
# =====================================================================
print()
print("=" * 76)
print("3) EL DISTRIBUIDOR #1 ES EL PRIMERO DE LA LISTA GEO-MATCHED")
print("=" * 76)

d = distribuidores.para_oportunidad(
    FICHA["busquedas_distribuidores"], FICHA["producto"],
    FICHA["lugar_entrega"], FICHA.get("query_google_proveedores", ""),
    FICHA["distribuidores_candidatos"])

primero = (d.get("candidatos") or [{}])[0].get("nombre")
print("  Orden geo-matched:", [c["nombre"] for c in d.get("candidatos") or []])
check("La lista esta ordenada por cercania", d.get("ordenado_por_cercania"),
      "-> no")
check("Hay candidatos", bool(primero), "-> la lista salio vacia")
check("El saludo usa al #1 de ESA lista",
      "\nDear %s Sales Team,\n" % primero in cuerpo, "-> no usa %r" % primero)

otros = [c["nombre"] for c in (d.get("candidatos") or [])[1:]]
for nombre in otros:
    check("Y no saluda al que viene despues (%s)" % nombre,
          "\nDear %s Sales Team," % nombre not in cuerpo, "-> se colo")

# Sin candidatos, el saludo degrada a generico: nunca un nombre inventado.
h_vacio = tn.formatear_analisis(dict(FICHA, distribuidores_candidatos=[],
                                     query_google_proveedores=""))
check("Sin candidatos, el saludo es generico",
      "\nDear Sales Team,\n" in h_vacio, "-> no degrada")
check("Y el RFQ sigue saliendo igual",
      "Subject: Wholesale RFQ" in h_vacio, "-> desaparece el bloque")


# =====================================================================
# 4) CERO ESPAÑOL EN EL CORREO
# =====================================================================
print()
print("=" * 76)
print("4) CERO ESPAÑOL EN EL CUERPO DEL CORREO")
print("=" * 76)

# El espanol del mensaje es de la INTERFAZ (Telegram, comentarios, notas),
# que es donde toca. Lo que no puede llevar espanol es el correo, porque
# eso es un documento que llega a un proveedor de EE.UU.
palabras = (" de ", " la ", " el ", " los ", " las ", " que ", " para ",
            " por ", " con ", " una ", " un ", " del ", " al ", " en ")
malas = [p.strip() for p in palabras if p in plano.lower()]
check("Ni una palabra en espanol dentro del correo", not malas,
      "-> %s" % malas)
check("Los rotulos son ingleses",
      all(x in plano for x in ("Subject:", "Item:", "Part / Model:",
                               "Quantity:", "Closing:", "Ship-To:",
                               "please provide the following:",
                               "Thank you for your consideration.")))
check("Y NO esta la de solicitud: el numero se protege",
      "Solicitation:" not in plano, "-> se colo")
check("La nota en espanol queda FUERA del <pre>",
      "confirma que vende el producto antes de escribir" not in cuerpo)


# =====================================================================
# 5) NUNCA INVENTA
# =====================================================================
print()
print("=" * 76)
print("5) NUNCA INVENTA: NI CORREO, NI NUMERO, NI DESTINO, NI FECHA")
print("=" * 76)

check("Ningun '@' en el mensaje entero", "@" not in h, "-> hay un @")
check("Ningun telefono", "+1 " not in h, "-> hay un telefono")
check("Ningun correo dentro del RFQ", "@" not in cuerpo, "-> hay un @")
check("El contacto del oficial NO se cuela en el correo",
      "john.smith" not in cuerpo, "-> se colo el contacto")

# El contacto con '@' del fixture es trampa a proposito: el analisis lo
# trae, pero el RFQ no lo lee jamas.
check("Se comprueba con un aviso que si trae '@'",
      "@" in str(FICHA["contacto"]))

print()
print("  Un NSN escrito en el aviso se muestra; uno que no esta, no:")
con_nsn = tn._identificador_tecnico(
    {"especificacion_tecnica_clave": "NSN 6140-01-234-5678, 1000 CCA"})
check("Lee el NSN", con_nsn == "NSN 6140-01-234-5678", "-> %r" % con_nsn)
con_pn = tn._identificador_tecnico(
    {"especificacion_tecnica_clave": "P/N AB-12345, 12V"})
check("Lee el Part Number", con_pn == "P/N AB-12345", "-> %r" % con_pn)
sin_nada = tn._identificador_tecnico(
    {"especificacion_tecnica_clave": "12V, Group 31, 1000 CCA",
     "modelo_especifico": ""})
check("Sin NSN ni PN no inventa ninguno", sin_nada == "",
      "-> %r" % sin_nada)
modelo = tn._identificador_tecnico(
    {"especificacion_tecnica_clave": "12V", "modelo_especifico": "XYZ-1"})
check("Si hay modelo lo pone tal cual, sin etiqueta inventada",
      modelo == "XYZ-1", "-> %r" % modelo)
check("El campo 'contacto' jamas se mira (ahi vive el '@')",
      tn._identificador_tecnico(
          {"contacto": "a@b.mil", "especificacion_tecnica_clave": ""}) == "",
      "-> se colo el contacto")

print()
print("  Pais solo nunca es destino de entrega:")
h_pais = tn.formatear_analisis(dict(FICHA, lugar_entrega="UNITED STATES"))
pre_pais = re.findall(r"<pre>(.*?)</pre>", h_pais, re.S)[-1]
plano_pais = " ".join(pre_pais.split())
check("Usa la frase honesta en vez del pais",
      "to the delivery location stated in the solicitation" in plano_pais,
      "-> no degrada")
check("Y no pinta 'UNITED STATES' como Ship-To",
      "Ship-To:      UNITED STATES" not in pre_pais, "-> se colo el pais")

print()
print("  Sin fecha leida no se pinta ninguna:")
h_sin_fecha = tn.formatear_analisis(dict(FICHA, limite="no es una fecha"))
pre_sf = re.findall(r"<pre>(.*?)</pre>", h_sin_fecha, re.S)[-1]
plano_sf = " ".join(pre_sf.split())
check("No aparece la linea Closing", "  Closing:" not in pre_sf,
      "-> se colo una fecha")
check("No aparece 'Vence'", "Vence" not in h_sin_fecha, "-> se colo")
check("La frase de los cinco dias sigue, sin apuntar a una fecha inventada",
      "at least five (5) days before the solicitation closing date"
      in plano_sf, "-> no esta")

print()
print("  El plazo del distribuidor son cinco dias de calendario antes del cierre:")
check("  Cierre ilegible -> no se pinta fecha",
      tn._fecha_cotizacion("no es una fecha") == "")
check("  Sin cierre -> no se pinta fecha",
      tn._fecha_cotizacion("") == "")
# El cierre tiene que ser a mas de cinco dias para que la fecha limite
# salga impresa: si ya habria pasado, es una mentira que el distribuidor
# descubre al responder. Se construye una fecha futura a proposito para
# que el test no dependa del dia en que se ejecute.
from datetime import datetime, timedelta, timezone
_futuro = (datetime.now(timezone.utc) + timedelta(days=20))
_fecha_txt = "%04d-%02d-%02dT12:00:00+00:00" % (
    _futuro.year, _futuro.month, _futuro.day)
check("  Cierre a 20 dias -> sale la fecha limite, cinco dias antes",
      tn._fecha_cotizacion(_fecha_txt)
      == "%s %d, %d" % (tn._MESES_EN[(_futuro - timedelta(days=5)).month - 1],
                        (_futuro - timedelta(days=5)).day,
                        (_futuro - timedelta(days=5)).year),
      "-> %r" % tn._fecha_cotizacion(_fecha_txt))
_cerca = (datetime.now(timezone.utc) + timedelta(days=3))
check("  Cierre a 3 dias -> no se pinta una fecha limite que ya habria pasado",
      tn._fecha_cotizacion("%04d-%02d-%02dT12:00:00+00:00" % (
          _cerca.year, _cerca.month, _cerca.day)) == "")

print()
print("  Fechas en ingles:")
for bruto, esperado in (("2026-11-04T17:00:00-04:00",
                         "November 4, 2026, 17:00 UTC-04"),
                        ("2026-11-04", "November 4, 2026"),
                        ("", ""), ("no es una fecha", "")):
    got = tn._fecha_ingles(bruto)
    check("  %-26s -> %r" % (bruto, esperado), got == esperado,
          "-> %r" % got)

check("El corte del asunto corta en borde de palabra",
      tn._cortar_palabra("Batteries, Lead-Acid, 12 Volt, Group 31, "
                         "Heavy Duty", 40)
      == "Batteries, Lead-Acid, 12 Volt, Group...",
      "-> %r" % tn._cortar_palabra("Batteries, Lead-Acid, 12 Volt, "
                                   "Group 31, Heavy Duty", 40))
check("Un texto corto no se toca",
      tn._cortar_palabra("Corto", 40) == "Corto")


# =====================================================================
# 6) FORMATO Y TAMAÑO
# =====================================================================
print()
print("=" * 76)
print("6) FORMATO: TEXTO PLANO DENTRO DEL <pre> Y CABE EN 4096")
print("=" * 76)

check("El correo va como texto plano (nada de etiquetas que se peguen al "
      "copiar)", "<b>" not in cuerpo and "<i>" not in cuerpo
      and "<code>" not in cuerpo, "-> hay etiquetas dentro")
mas_larga = max((len(l) for l in cuerpo.split("\n")), default=0)
check("Ninguna linea se pasa de 90 columnas (dentro de <pre> no hay wrap)",
      mas_larga <= 90, "-> %d chars" % mas_larga)
check("El HTML del mensaje entero sigue balanceado",
      h.count("<b>") == h.count("</b>") and h.count("<i>") == h.count("</i>")
      and h.count("<pre>") == h.count("</pre>"),
      "-> b=%d/%d i=%d/%d pre=%d/%d" % (h.count("<b>"), h.count("</b>"),
                                        h.count("<i>"), h.count("</i>"),
                                        h.count("<pre>"), h.count("</pre>")))
check("Cabe en un solo mensaje", len(h) < 4096, "-> %d" % len(h))

# El peor caso que se llega a construir en la suite: sin descripcion, sin
# candidatos, sin fecha y con el aviso de estimaciones encima.
flojo = tn.formatear_analisis(dict(
    FICHA, sin_descripcion=True, distribuidores_candidatos=[],
    query_google_proveedores="", set_aside="sin set-aside", limite="",
    lugar_entrega=""))
check("El peor caso tambien cabe", len(flojo) < 4096, "-> %d" % len(flojo))
check("Y en ese peor caso el RFQ sigue saliendo",
      "Subject: Wholesale RFQ" in flojo, "-> desaparece")
check("Sin candidatos, sin fecha y sin destino no se inventa nada",
      "Dear Sales Team," in flojo and "  Closing:" not in flojo
      and "  Ship-To:" not in flojo and "@" not in flojo,
      "-> se invento algo")

print()
print("  Troceo: si un dia crece, el bloque viaja ENTERO.")

# Mensaje sintetico afinado para que el corte de 4064 caiga JUSTO dentro
# del <pre>: sin la regla de _trocear, el primer trozo se quedaria con el
# bloque abierto y el segundo llegaria con un </pre> huerfano que
# _sanear_html escapa como texto literal "&lt;/pre&gt;", que es el correo
# impreso con las dos barras a la vista.
sintetico = ("relleno para pasar el limite de troceo " * 70).strip()
sintetico += "\n<pre>\n"
sintetico += "\n".join(
    "  linea %02d del correo que no se debe partir" % n for n in range(40))
sintetico += "\n</pre>"
check("El sintetico pasa de 4096 (si no, no prueba nada)",
      len(sintetico) > 4096, "-> %d" % len(sintetico))
trozos_s = tn._trocear(sintetico)
con_bloque = [t for t in trozos_s if "<pre>" in t]
check("El corte caeria dentro del bloque y aun asi no se parte",
      len(con_bloque) == 1 and con_bloque[0].count("<pre>") == 1
      and con_bloque[0].count("</pre>") == 1
      and "<pre>\n  linea 00" in con_bloque[0]
      and con_bloque[0].rstrip().endswith("</pre>"),
      "-> %d trozos con <pre>" % len(con_bloque))
check("Ningun trozo escapa un cierre de etiqueta al saneado",
      not any("&lt;/" in tn._sanear_html(t) for t in trozos_s),
      "-> algun trozo sale roto")
check("Y todos siguen cabiendo",
      all(len(t) <= 4096 for t in trozos_s),
      "-> %s" % [len(t) for t in trozos_s])

print()
print("  Y con la ficha entera, rellenada hasta pasarse:")
relleno = ("Relleno para pasar el limite de Telegram. " * 120).strip()
check("El relleno pasa de 4096", len(relleno) > 4096, "-> %d" % len(relleno))
trozos = tn._trocear(relleno + "\n" + h)
con_rfq = [t for t in trozos if "Subject: Wholesale RFQ" in t]
check("El RFQ viaja en UN solo trozo", len(con_rfq) == 1,
      "-> %d trozos con el correo" % len(con_rfq))
if con_rfq:
    check("Con el </pre> justo detras de la ciudad de la firma",
          "Albuquerque, NM 87110\n</pre>" in con_rfq[0], "-> no cierra ahi")
    check("Y el rotulo de la seccion no se queda huerfano dentro",
          "RFQ Listo para Enviar" not in con_rfq[0], "-> se colo")
check("Se trocea sin romper ningun <pre> del mensaje",
      sum(t.count("<pre>") for t in trozos) == h.count("<pre>")
      and sum(t.count("</pre>") for t in trozos) == h.count("</pre>"),
      "-> se perdio un bloque")
check("Ningun trozo pasa de 4096", all(len(t) <= 4096 for t in trozos),
      "-> %s" % [len(t) for t in trozos])


# =====================================================================
# 7) LO NUEVO DE LA FICHA: COMPLEJIDAD, CODIGOS Y LOGISTICA
# =====================================================================
print()
print("=" * 76)
print("7) COMPLEJIDAD, NSN/P-N, LOGISTICA Y EMPAQUE")
print("=" * 76)

pos_sa = h.find("Total Small Business")
pos_cx = h.find("Nivel de Complejidad")
check("La bandera de complejidad existe y es obligatoria",
      pos_cx > 0, "-> no esta")
check("Y va JUSTO debajo del Set-Aside, antes del producto",
      0 < pos_sa < pos_cx < h.find("<b>Producto</b>"),
      "-> sa=%d cx=%d" % (pos_sa, pos_cx))
check("Trae su motivo, porque una bandera sin motivo no se discute",
      "—" in h[pos_cx:h.find("\n", pos_cx)], "-> no trae motivo")
check("Trae uno de los tres niveles conocidos",
      any(x in h[pos_cx:h.find("\n", pos_cx)]
          for x in ("ALTA COMPLEJIDAD", "COMPLEJIDAD MEDIA", "PTIMA")),
      "-> nivel desconocido")

print()
print("  Los codigos exactos dentro del bloque de Producto:")
pos_prod = h.find("<b>Producto</b>")
pos_fin = h.find("<b>Resumen financiero</b>")
prod = h[pos_prod:pos_fin]
check("NSN y P/N salen siempre, con su rotulo",
      ("NSN:" in prod and "P/N:" in prod) or "NSN / P/N:" in prod,
      "-> no salen")
# Sin descripcion cacheada no hay codigo que leer, y ahi se manda al
# pliego en vez de inventar uno.
check("Sin ningun codigo, dice 'No especificado - Verificar pliego'",
      "No especificado - Verificar pliego" in prod, "-> no degrada")
# Con un NSN escrito en el aviso, sale el NSN y no el modelo.
con_nsn_ficha = tn.formatear_analisis(dict(
    FICHA, especificacion_tecnica_clave="NSN 6140-01-234-5678, 1000 CCA"))
prod_nsn = con_nsn_ficha[con_nsn_ficha.find("<b>Producto</b>"):
                         con_nsn_ficha.find("<b>Resumen financiero</b>")]
check("Con NSN escrito, sale el NSN exacto",
      "NSN: 6140-01-234-5678" in prod_nsn, "-> no salio")
check("Y el NSN de la ficha es el MISMO que el del Part / Model del RFQ",
      "6140-01-234-5678" in "\n".join(
          tn._bloque_rfq(dict(FICHA, especificacion_tecnica_clave=
                              "NSN 6140-01-234-5678, 1000 CCA"),
                         {"candidatos": [{}], "nombre": "X"})),
      "-> se contradicen")

print()
print("  La linea de logistica:")
check("Aparece con su rotulo", "Logística:" in h, "-> no esta")
check("Trae el empaque, aunque sea 'No especificado'",
      "Empaque:" in h, "-> no trae empaque")
check("Nunca promete 'Entrega Unica' sin conocer el recuento",
      ("Entrega Única" in h) == bool(FICHA.get("entregas")),
      "-> promete sin saber")
check("Y el Ship-To del correo sigue intacto",
      "  Ship-To:      Norfolk, VA (South)" in cuerpo, "-> cambio")

print()
print("  Los dos pedidos de cumplimiento, obligatorios:")
check("U.S. origin / Buy American Act (FAR 52.225-1) o TAA",
      "U.S. origin / Buy American Act (FAR 52.225-1) or TAA"
      in plano, "-> no esta")
check("Y el estandar de empaque (Comercial o MIL-STD)",
      "Packaging compliance: Commercial or MIL-STD-2073 / MIL-STD-129."
      in plano, "-> no esta")

print()
print("  El empaque conocido se inyecta en el RFQ y cambia el punto:")
con_emp = dict(FICHA, empaque="MIL-STD-2073")
pre_emp = "\n".join(tn._bloque_rfq(con_emp,
                                   {"candidatos": [{}], "nombre": "X"}))
check("La linea Packaging sale con el estandar",
      "  Packaging:    MIL-STD-2073" in pre_emp, "-> no sale")
check("Y el punto nombra ese estandar",
      "Packaging to MIL-STD-2073 with military markings" in pre_emp,
      "-> no lo nombra")
pre_com = "\n".join(tn._bloque_rfq(dict(FICHA, empaque="COMERCIAL"),
                                   {"candidatos": [{}], "nombre": "X"}))
check("Si es comercial, se dice comercial",
      "  Packaging:    Standard Commercial Packaging" in pre_com
      and "Standard commercial packaging as required." in pre_com,
      "-> no lo dice")
pre_noemp = "\n".join(tn._bloque_rfq(dict(FICHA, empaque="NO ESPECIFICADO"),
                                     {"candidatos": [{}], "nombre": "X"}))
check("Si no se sabe, no se pinta la linea (nunca se inventa)",
      "  Packaging:" not in pre_noemp, "-> se colo")

print()
print("  El nivel de complejidad se calcula con reglas, no con el modelo:")
import complejidad
check("  DLA Aviation + ITAR -> ALTA",
      complejidad.evaluar({"agencia": "DLA Aviation",
                           "cumple": ["ITAR"]})[0] == complejidad.ALTA)
check("  DLA Land and Maritime + FAT -> ALTA",
      complejidad.evaluar(
          {"agencia": "Defense Logistics Agency > DLA Land and Maritime",
           "cumple": ["First Article Testing"]})[0] == complejidad.ALTA)
check("  DLA Aviation sin gatillo -> NO es ALTA",
      complejidad.evaluar({"agencia": "DLA Aviation",
                           "cumple": []})[0] != complejidad.ALTA)
check("  USDA sin nada -> OPTIMA",
      complejidad.evaluar({"agencia": "USDA", "cumple": []}
                          )[0] == complejidad.OPTIMA)
check("  Empaque militar -> MEDIA",
      complejidad.evaluar({"agencia": "USDA",
                           "cumple": ["MIL-STD-2073"]})[0]
      == complejidad.MEDIA)
check("  Dos o mas destinos -> MEDIA",
      complejidad.evaluar({"agencia": "USDA", "cumple": [],
                           "entregas": ["Norfolk, VA", "San Diego, CA"]}
                          )[0] == complejidad.MEDIA)
check("  'NOT ITAR' no cuenta como gatillo (no se anula a si mismo)",
      "ITAR" not in complejidad.canonizar(["NOT ITAR"]))
check("  Analisis antiguo sin campos -> OPTIMA y lo dice",
      complejidad.evaluar({"agencia": "GSA"})[0] == complejidad.OPTIMA
      and "analisis anterior" in complejidad.evaluar({"agencia": "GSA"})[1])


# =====================================================================
# 8) RETROCOMPATIBILIDAD
# =====================================================================
print()
print("=" * 76)
print("8) RETROCOMPATIBILIDAD Y QUE NO HAYA SECRETOS")
print("=" * 76)

check("formatear_analisis conserva su firma",
      list(inspect.signature(tn.formatear_analisis).parameters) == ["a"])
check("_bloque_rfq(a, d) es el del bloque",
      list(inspect.signature(tn._bloque_rfq).parameters) == ["a", "d"])
check("devuelve una lista de lineas, como el resto de los bloques",
      isinstance(tn._bloque_rfq(FICHA, d), list))
check("_fecha_ingles(bruto) devuelve str",
      isinstance(tn._fecha_ingles(FICHA["limite"]), str))
check("_identificador_tecnico(a) devuelve str",
      isinstance(tn._identificador_tecnico(FICHA), str))

check("La firma es exactamente la que pidio el cliente",
      tn.FIRMA_RFQ == ("Bastian Cordero\n"
                       "Managing Member | L.A.M.B Logistics LLC\n"
                       "Albuquerque, NM 87110"),
      "-> %r" % (tn.FIRMA_RFQ,))
check("Y sin el EIN, que el cliente pidio quitar",
      "EIN" not in tn.FIRMA_RFQ, "-> sigue el EIN")

with open(tn.__file__, encoding="utf-8") as _f:
    _fuente = _f.read()
check("Ningun secreto en el modulo",
      all(x not in _fuente for x in ("AIza", "sk-")),
      "-> se colo una clave")
check("El bloque por si solo no trae ningun '@'",
      "@" not in "\n".join(tn._bloque_rfq(FICHA, d)), "-> hay un @")

print()
print("=" * 76)
if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: el RFQ sale listo para copiar y pegar, en verde")
print("=" * 76)
