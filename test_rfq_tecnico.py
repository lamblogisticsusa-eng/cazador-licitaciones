# -*- coding: utf-8 -*-
"""
test_rfq_tecnico.py - Los campos tecnicos del RFQ: codigos, anexos e ingles.

Que se comprueba, y por que:

1) TODOS LOS CODIGOS, NO SOLO EL PRIMERO. Un aviso de consumibles o de
   repuestos trae una LISTA de NSN y numeros de parte. Quedarse con el
   primero hace que el mayorista cotice la pieza A sin enterarse de que se
   piden B y C. Se comprueba que salen todos, que el NSN va primero, que
   un codigo repetido en dos campos sale una vez, que la puntuacion final
   no se cuela dentro del codigo, y que "contacto" sigue sin mirarse.

2) LAS REFERENCIAS A ANEXOS, EXACTAS. En un lote consolidado el desglose
   no esta en la tabla, esta en el anexo, y lo que el distribuidor necesita
   es el nombre con el que figura en SAM.gov, no una parrafada sobre "el
   documento adjunto". Dos fuentes: a["anexos"] (los nombres de
   resourceLinks) y lo escrito en los campos oficiales del aviso.

3) LO QUE VIENE EN CASTELLANO NO ENTRA EN EL CORREO. El prompt le pide a
   Gemini "producto en espanol claro" y de la especificacion tecnica no
   fija idioma, asi que esa sale a veces en castellano. El RFQ es un
   documento en ingles comercial: si el campo llega en espanol no se
   inyecta, y se comprueba que no aparece en ningun sitio del <pre>.

4) EL CORTE CAE EN BORDE DE PALABRA. Un [:140] deja "MIL-PRF-" a media
   referencia, y una referencia a media no sirve para buscar nada.

5) LOS ANEXOS LLEGAN AL ANALISIS. resourceLinks viaja ya en la respuesta
   del search: leerlo no cuesta ni una llamada de red, y es lo que permite
   que el RFQ nombre el anexo.

6) ESTRUCTURA: mensaje por debajo de 4096, <pre> equilibrado, ninguna linea
   por encima de 90 columnas y ni un "@" en el correo.

7) RETROCOMPATIBILIDAD: las firmas publicas no cambian de forma ni de
   sentido.

NO toca la red: la llamada de Gemini se sustituye SOLO en la seccion 5,
igual que en test_cadena_distribuidores.
"""

import json
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


# --------------------------------------------------------------- entorno
DB = os.path.join(tempfile.gettempdir(), "kyo_test_rfq_tecnico.db")
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import distribuidores  # noqa: F401,E402
import gemini_analyzer  # noqa: E402
import telegram_notify as tn  # noqa: E402


# ----------------------------------------------------------------- util
def _pre(html: str) -> str:
    """El cuerpo del ULTIMO <pre>, que es el del RFQ, sin el salto inicial."""
    bloques = re.findall(r"<pre>(.*?)</pre>", html, re.S)
    if not bloques:
        return ""
    return bloques[-1].lstrip("\n")


def _campo(cuerpo: str, etiqueta: str) -> str:
    """
    El valor completo de un campo, con los trozos de textwrap unidos.

    campo() pinta dos espacios y la etiqueta con ljust(14), asi que el valor
    empieza SIEMPRE en la columna 16, y una continuacion lleva 16 espacios
    delante. Juntar con un espacio y normalizar devuelve la frase tal cual.
    """
    lineas = cuerpo.split("\n")
    for i, linea in enumerate(lineas):
        if len(linea) < 16 or not linea.startswith("  " + etiqueta):
            continue
        partes = [linea[16:]]
        for sig in lineas[i + 1:]:
            if len(sig) >= 16 and sig.startswith(" " * 16) and sig.strip():
                partes.append(sig[16:])
            else:
                break
        return " ".join(" ".join(partes).split())
    return ""


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

# Una especificacion larga a proposito, montada para que la letra 140 caiga
# DENTRO de una palabra: es lo que un [:140] cortaria por la mitad.
ESPEC_LARGA = ("MIL-PRF-8171 Revision C aluminum housing specification with "
               "neoprene sealing compound aluminum housing specification with "
               "neoprene sealing compound sealing assembly")


# =====================================================================
# 1) TODOS LOS CODIGOS, NO SOLO EL PRIMERO
# =====================================================================
print("=" * 76)
print("1) TODOS LOS CODIGOS, NO SOLO EL PRIMERO")
print("=" * 76)

LOTE = dict(FICHA,
            especificacion_tecnica_clave=(
                "P/N AB-12345. P/N CD-67890. P/N EF-11223. "
                "NSN 6140-01-234-5678. 12V Group 31."))

ids = tn._identificadores_tecnicos(LOTE)
print("  encontrados: %s" % ids)

check("Lee los tres numeros de parte",
      "P/N AB-12345" in ids and "P/N CD-67890" in ids
      and "P/N EF-11223" in ids, "-> %s" % ids)
check("Y el NSN, que va primero",
      ids[:1] == ["NSN 6140-01-234-5678"], "-> %s" % ids[:1])
check("Cada codigo sale una sola vez",
      len(ids) == len(set(x.lower() for x in ids)), "-> %s" % ids)

# _RE_PARTE se come el punto final de la frase: si no se quita, el correo
# imprime "P/N AB-12345., P/N CD-67890.", que es una referencia que no se
# puede buscar tal cual.
check("La puntuacion final no se cuela dentro del codigo",
      all(not x.endswith((".", ",", ";")) for x in ids), "-> %s" % ids)
check("Pero un punto INTERNO sigue siendo parte del codigo",
      "P/N AB.12345" in tn._identificadores_tecnicos(
          {"especificacion_tecnica_clave": "P/N AB.12345"}),
      "-> %s" % tn._identificadores_tecnicos(
          {"especificacion_tecnica_clave": "P/N AB.12345"}))

cuerpo_lote = _pre(tn.formatear_analisis(LOTE))
part = _campo(cuerpo_lote, "Part / Model:")
print("  Part / Model: %s" % part)
check("Y SALEN EN EL CORREO, no solo en el parser",
      "NSN 6140-01-234-5678" in part and "P/N AB-12345" in part
      and "P/N CD-67890" in part, "-> %r" % part)
check("A los que no caben se les dice, no se les calla",
      "and 1 more" in part, "-> %r" % part)

# El mismo codigo escrito en dos campos es la misma pieza.
dup = tn._identificadores_tecnicos(
    {"especificacion_tecnica_clave": "P/N AB-12345",
     "title": "P/N ab-12345 gaskets"})
check("Un codigo repetido en dos campos sale una vez",
      dup == ["P/N AB-12345"], "-> %s" % dup)

print()
print("  El singular sigue eligiendo al PRIMERO del plural:")
for caso in (LOTE, FICHA,
             {"especificacion_tecnica_clave": "P/N AB-12345"},
             {"especificacion_tecnica_clave": "P/N AB-12345."},
             {"modelo_especifico": "XYZ-1"}, {}):
    uno = tn._identificador_tecnico(caso)
    muchos = tn._identificadores_tecnicos(caso)
    primero = (muchos or [""])[0]
    check("singular == primero de plural (%r)" % uno,
          uno.rstrip(" .,;:") == primero.rstrip(" .,;:"),
          "-> %r vs %r" % (uno, muchos))

check("El plural tampoco mira 'contacto'",
      tn._identificadores_tecnicos(
          {"contacto": "a@b.mil", "especificacion_tecnica_clave": ""}) == [],
      "-> se colo el correo")
check("Y un modelo solo, sin etiqueta inventada",
      tn._identificadores_tecnicos({"modelo_especifico": "XYZ-1"})
      == ["XYZ-1"], "-> no devolvio el modelo")


# =====================================================================
# 2) LAS REFERENCIAS A ANEXOS, EXACTAS
# =====================================================================
print()
print("=" * 76)
print("2) LAS REFERENCIAS A ANEXOS, EXACTAS")
print("=" * 76)

con_anexo = dict(FICHA, anexos=["W519TC27QA012_Attachment0002.pdf"])
pre_an = _pre(tn.formatear_analisis(con_anexo))
valor_an = _campo(pre_an, "Attachment:")
print("  Attachment: %s" % valor_an)
check("Pinta la linea Attachment con el nombre EXACTO del fichero",
      valor_an == "W519TC27QA012_Attachment0002.pdf", "-> %r" % valor_an)
check("Y no lo reinterpreta ni lo traduce",
      "Attachment 0002" not in pre_an, "-> lo reescribio")

en_linea = dict(FICHA, anexos=[],
                especificacion_tecnica_clave=(
                    "Quantities and unit prices are listed per line. "
                    "See Attachment 0002 for the breakdown. P/N AB-12345."))
refs_linea = tn._referencias_anexo(en_linea)
check("Lee la referencia escrita en los campos oficiales",
      "Attachment 0002" in refs_linea, "-> %s" % refs_linea)
pre_lin = _pre(tn.formatear_analisis(en_linea))
check("Y la pinta en el correo",
      _campo(pre_lin, "Attachment:") == "Attachment 0002",
      "-> %r" % _campo(pre_lin, "Attachment:"))
check("El Part / Model sigue con su codigo",
      _campo(pre_lin, "Part / Model:") == "P/N AB-12345",
      "-> %r" % _campo(pre_lin, "Part / Model:"))

romano = {"especificacion_tecnica_clave": "Per Attachment IV and Annex B."}
refs_rom = tn._referencias_anexo(romano)
check("Numero romano", "Attachment IV" in refs_rom, "-> %s" % refs_rom)
check("Annex con letra", "Annex B" in refs_rom, "-> %s" % refs_rom)

otros = {"especificacion_tecnica_clave":
         "Schedule A, Exhibit 3, Addendum 2, Statement of Work"}
refs_otros = tn._referencias_anexo(otros)
check("Las otras formas tambien",
      refs_otros == ["Schedule A", "Exhibit 3", "Addendum 2",
                     "Statement of Work"], "-> %s" % refs_otros)

check("SOW en mayusculas es referencia de documento",
      tn._referencias_anexo({"title": "SOW for Industrial Supplies"})
      == ["SOW"], "-> %s"
      % tn._referencias_anexo({"title": "SOW for Industrial Supplies"}))
check("Pero 'sow' en minusculas es el verbo sembrar y no se toca",
      tn._referencias_anexo({"title": "We will sow the field"}) == [],
      "-> %s" % tn._referencias_anexo({"title": "We will sow the field"}))

check("Un 'Attachments are included' NO inventa anexo",
      tn._referencias_anexo(
          {"especificacion_tecnica_clave":
           "Attachments are included with the solicitation at no charge."})
      == [], "-> se invento")
check("Tolera anexos como diccionario",
      tn._referencias_anexo({"anexos": [{"filename": "x.pdf"}]}) == ["x.pdf"],
      "-> no lo leyo")
check("Las referencias a anexos tampoco miran 'contacto'",
      tn._referencias_anexo(
          {"contacto": "anexo@b.mil", "especificacion_tecnica_clave": ""})
      == [], "-> se colo")

sin_anexo = _pre(tn.formatear_analisis(FICHA))
check("Sin anexos NO hay linea Attachment",
      _campo(sin_anexo, "Attachment:") == ""
      and "Attachment:" not in sin_anexo, "-> la pinto igual")

tres = _campo(_pre(tn.formatear_analisis(
    dict(FICHA, anexos=["a.pdf", "b.pdf", "c.pdf"]))), "Attachment:")
print("  tres anexos: %s" % tres)
check("Tres anexos: nombra dos y dice que hay mas",
      tres == "a.pdf, b.pdf, and 1 more", "-> %r" % tres)


# =====================================================================
# 3) LO QUE VIENE EN CASTELLANO NO ENTRA EN EL CORREO
# =====================================================================
print()
print("=" * 76)
print("3) LO QUE VIENE EN CASTELLANO NO ENTRA EN EL CORREO")
print("=" * 76)

print("  El detector:")
check("Dos palabras bastan",
      tn._en_espanol("Acero fundido, sellos mecanicos"))
check("Y una sola no, no se tira un campo ingles por una palabra",
      not tn._en_espanol("Garantia 12 meses"))
check("Un texto ingles de material industrial pasa",
      not tn._en_espanol("EPDM 70 Shore A, ASTM D2000"))
check("Y uno con 'Revision' tambien",
      not tn._en_espanol("Revision C of the specification"))
check("Vacio no es castellano", not tn._en_espanol(""))
check("Se ignoran las etiquetas HTML", tn._en_espanol("<b>acero</b> fundido"))
check("El titulo de la ficha base pasa",
      not tn._en_espanol(FICHA["title"]))

espec_es = dict(FICHA, especificacion_tecnica_clave="Acero fundido, sellos mecanicos")
pre_es = _pre(tn.formatear_analisis(espec_es))
print("  Specs: %r" % _campo(pre_es, "Specs:"))
check("La especificacion en castellano NO se inyecta",
      _campo(pre_es, "Specs:") == "", "-> %r" % _campo(pre_es, "Specs:"))
check("Y no aparece en NINGUN sitio del correo",
      "Acero fundido" not in pre_es and "sellos mecanicos" not in pre_es,
      "-> se colo")
check("El resto del correo sale igual",
      pre_es.startswith("Subject: RFQ - 36C24526Q0123 - Batteries, Lead-Acid")
      and _campo(pre_es, "Quantity:") == "500 EA"
      and _campo(pre_es, "Part / Model:") == "S0594DU0500-S1",
      "-> falto algo")

espec_en = _campo(_pre(tn.formatear_analisis(
    dict(FICHA, especificacion_tecnica_clave="12V, Group 31, 1000 CCA"))),
    "Specs:")
check("Y una especificacion en ingles SI entra",
      espec_en == "12V, Group 31, 1000 CCA", "-> %r" % espec_en)

titulo_es = _pre(tn.formatear_analisis(
    dict(FICHA, title="Baterias de arranque, lote de suministro")))
check("Un titulo en castellano tampoco entra como Item",
      _campo(titulo_es, "Item:") == "", "-> %r" % _campo(titulo_es, "Item:"))
check("Ni se pinta a medias en el asunto",
      titulo_es.startswith("Subject: RFQ - 36C24526Q0123\n"),
      "-> %r" % titulo_es.split("\n")[0])
check("Y el correo sigue completo por lo demas",
      _campo(titulo_es, "Solicitation:") == "36C24526Q0123"
      and _campo(titulo_es, "Ship-To:") == "Norfolk, VA (South)",
      "-> falto algo")

check("El espanol del asunto tampoco se cuela",
      not re.search(r"\b(de|la|el|los|las|para|que)\b",
                    titulo_es.split("\n")[0].lower()),
      "-> %r" % titulo_es.split("\n")[0])


# =====================================================================
# 4) EL CORTE CAE EN BORDE DE PALABRA, NUNCA A MEDIA
# =====================================================================
print()
print("=" * 76)
print("4) EL CORTE CAE EN BORDE DE PALABRA, NUNCA A MEDIA")
print("=" * 76)

check("La letra 140 de la especificacion SI estaria a media palabra",
      ESPEC_LARGA[139:140].isalpha(), "-> %r" % ESPEC_LARGA[137:142])

pre_larga = _pre(tn.formatear_analisis(
    dict(FICHA, especificacion_tecnica_clave=ESPEC_LARGA)))
valor = _campo(pre_larga, "Specs:")
print("  Specs: %s" % valor)
prefijo = valor[:-3]

check("Acaba en puntos suspensivos, que avisan del corte",
      valor.endswith("...") and len(valor) > 10, "-> %r" % valor[-20:])
check("Y el corte no es el de la letra",
      valor.rstrip(".") != ESPEC_LARGA[:140].rstrip("."),
      "-> corto igual que un [:140]")
check("Lo impreso es prefijo literal de la fuente",
      ESPEC_LARGA.startswith(prefijo), "-> no lo es")
check("Y el caracter siguiente a lo impreso NO es una letra",
      not ESPEC_LARGA[len(prefijo):len(prefijo) + 1].isalpha(),
      "-> la fuente sigue en %r"
      % ESPEC_LARGA[len(prefijo):len(prefijo) + 8])
check("El corte ocurre antes o en el limite",
      len(prefijo) <= 140, "-> %d" % len(prefijo))


# =====================================================================
# 5) LOS ANEXOS LLEGAN AL ANALISIS
# =====================================================================
print()
print("=" * 76)
print("5) LOS ANEXOS LLEGAN AL ANALISIS")
print("=" * 76)

check("Un aviso sin anexos no tiene nada que nombrar",
      gemini_analyzer._anexos({}) == [], "-> %s"
      % gemini_analyzer._anexos({}))
check("resourceLinks vacio tambien",
      gemini_analyzer._anexos({"resourceLinks": []}) == [], "-> %s"
      % gemini_analyzer._anexos({"resourceLinks": []}))

urls = gemini_analyzer._anexos({"resourceLinks": [
    "https://sam.ejemplo/gov/W519TC27QA012_Attachment0002.pdf?v=123",
    "https://sam.ejemplo/gov/W519TC27QA012_SOW.pdf"]})
print("  _anexos -> %s" % urls)
check("Se queda con el nombre del fichero, no con la URL entera",
      urls == ["W519TC27QA012_Attachment0002.pdf",
               "W519TC27QA012_SOW.pdf"], "-> %s" % urls)
check("Y se lleva la query de la URL firmada",
      not any("?" in x for x in urls), "-> %s" % urls)

check("Tolera resourceLinks como objetos con href",
      gemini_analyzer._anexos(
          {"resourceLinks": [{"href": "https://x/y/z.pdf"}]}) == ["z.pdf"],
      "-> %s" % gemini_analyzer._anexos(
          {"resourceLinks": [{"href": "https://x/y/z.pdf"}]}))


# ---- la puerta real de analizar(), con la llamada de red simulada ----
RESPUESTA_GEMINI = {
    "viable": True,
    "motivo_descarte": "",
    "producto": "Baterias 12V Grupo 31",
    "modelo_especifico": "S0594DU0500-S1",
    "unidad_medida": "EA",
    "cantidad_total": 500,
    "especificacion_tecnica_clave": "12V, Group 31, 1000 CCA, 200 min reserve",
    "lugar_entrega": "Norfolk / VA / USA",
    "precio_unitario_costo": 104.0,
    "precio_unitario_mercado": 139.0,
    "precio_unitario_oferta": 149.0,
    "ganancia_por_unidad": 45.0,
    "valor_contrato_usd": 85000.0,
    "costo_total_usd": 52000.0,
    "ganancia_total_usd": 22500.0,
    "margen_bruto_porcentaje": 30.2,
    "costo_factoring_usd": 1820.0,
    "margen_neto_porcentaje": 24.3,
    "precio_oferta_sugerido_usd": 74500.0,
    "estrategia_oferta": "",
    "razonamiento_oferta": "12% bajo el tope.",
    "busquedas_distribuidores": ["baterias 12V wholesale"],
    "query_google_proveedores": "baterias 12V Group 31 wholesale usa",
    "distribuidores_candidatos": [
        {"nombre": "Grainger", "tipo": "mayorista industrial",
         "porque": "stock", "verificar": "Grainger batteries"}],
    "nivel_riesgo": "medio",
    "preguntas_criticas": [],
    "observaciones": "",
}

OPORTUNIDAD = {
    "noticeId": "anexo123",
    "title": "Batteries, Lead-Acid, 12 Volt, Group 31, Heavy Duty",
    "solicitationNumber": "36C24526Q0123",
    "naicsCode": "335911",
    "classificationCode": "6135",
    "fullParentPathName": "Department of the Air Force",
    "typeOfSetAsideDescription": "Total Small Business Set-Aside",
    "responseDeadLine": "2026-11-04T17:00:00-04:00",
    "uiLink": "https://sam.gov/opp/anexo123/view",
    "resourceLinks": [
        "https://sam.ejemplo/gov/36C24526Q0123_Attachment0002.pdf?v=1"],
}


class _RespuestaFalsa:
    """Lo que el cliente de google-genai devuelve. .text es PROPIEDAD."""

    def __init__(self, texto, prompt=""):
        self.text = texto
        self.prompt = prompt
        self.candidates = []


class _ModelsFalso:
    def generate_content(self, model, contents, config=None, **kw):
        return _RespuestaFalsa(
            json.dumps(RESPUESTA_GEMINI, ensure_ascii=False), str(contents))


class _ClienteFalso:
    @property
    def models(self):
        return _ModelsFalso()


_cliente_original = gemini_analyzer._cliente
try:
    gemini_analyzer._cliente = _ClienteFalso()
    salida = gemini_analyzer.analizar(OPORTUNIDAD, "descripcion oficial",
                                      "Norfolk, VA")
finally:
    gemini_analyzer._cliente = _cliente_original

print("  analizar() -> anexos=%r" % salida.get("anexos"))
check("El analisis sale con los anexos",
      salida.get("anexos") == ["36C24526Q0123_Attachment0002.pdf"],
      "-> %r" % salida.get("anexos"))
check("Y con la URL ya reducida a nombre de fichero",
      not any("http" in x for x in (salida.get("anexos") or [])),
      "-> %r" % salida.get("anexos"))
check("El resto del analisis sigue saliendo entero",
      salida.get("solicitation") == "36C24526Q0123"
      and salida.get("title") == FICHA["title"]
      and salida.get("cantidad_total") == 500,
      "-> %r" % {k: salida.get(k) for k in ("solicitation", "title",
                                            "cantidad_total")})

# Y ese analisis ya renderizado tiene que nombrar el anexo.
pre_an2 = _pre(tn.formatear_analisis(salida))
check("La ficha pinta el anexo del analisis",
      _campo(pre_an2, "Attachment:") == "36C24526Q0123_Attachment0002.pdf",
      "-> %r" % _campo(pre_an2, "Attachment:"))


# =====================================================================
# 6) ESTRUCTURA
# =====================================================================
print()
print("=" * 76)
print("6) ESTRUCTURA: 4096, <pre>, COLUMNAS, CERO ARROBA")
print("=" * 76)

# Un lote realista con codigos y anexos a la vez: es el peor caso que se
# puede esperar de verdad, y tiene que caber.
LOTE_REAL = dict(FICHA,
                 anexos=["W519TC27QA012_Attachment0002.pdf",
                         "W519TC27QA012_SOW.pdf"],
                 especificacion_tecnica_clave=(
                     "NSN 6140-01-234-5678. P/N AB-12345. "
                     "12V, Group 31, 1000 CCA, 200 min reserve"))

casos = [("base", FICHA),
         ("multi-codigo", LOTE),
         ("anexo", con_anexo),
         ("lote real (codigos + anexos)", LOTE_REAL),
         ("espec en castellano", espec_es),
         ("titulo en castellano", dict(FICHA,
                                       title="Baterias de arranque")),
         ("espec larga", dict(FICHA, especificacion_tecnica_clave=ESPEC_LARGA))]

for nombre, ficha in casos:
    h = tn.formatear_analisis(ficha)
    p = _pre(h)
    check("%s: mensaje por debajo de 4096" % nombre, len(h) < 4096,
          "-> %d" % len(h))
    check("%s: <pre> equilibrado" % nombre,
          h.count("<pre>") == h.count("</pre>"),
          "-> %d vs %d" % (h.count("<pre>"), h.count("</pre>")))
    check("%s: el </pre> cierra en el EIN" % nombre,
          p.rstrip().endswith("EIN: 32-0868469"),
          "-> %r" % p.rstrip()[-40:])
    largas = [x for x in p.split("\n") if len(x) > 90]
    check("%s: ninguna linea por encima de 90 columnas" % nombre,
          not largas, "-> %r" % (largas[:1],))
    check("%s: ni un '@' en el correo" % nombre,
          "@" not in p, "-> %r" % re.findall(r"\S*@\S*", p)[:2])

p_real = _pre(tn.formatear_analisis(LOTE_REAL))
print("  lote real: Part / Model=%r" % _campo(p_real, "Part / Model:"))
print("  lote real: Attachment  =%r" % _campo(p_real, "Attachment:"))
check("El lote real nombra los codigos Y el anexo a la vez",
      "NSN 6140-01-234-5678" in _campo(p_real, "Part / Model:")
      and "P/N AB-12345" in _campo(p_real, "Part / Model:")
      and "Attachment0002" in _campo(p_real, "Attachment:"),
      "-> %r / %r" % (_campo(p_real, "Part / Model:"),
                      _campo(p_real, "Attachment:")))

# ---- un caso que SI pasa de 4096 -------------------------------------
# El prompt le permite a Gemini hasta 700 caracteres de especificacion y
# _frase_producto la mete entera en la linea de producto (asi es desde
# siempre, no es algo nuevo de este cambio), asi que un aviso con una
# especificacion minuciosa pasa del limite. Lo que hay que comprobar NO es
# que quepa, es que al no caber NO se rompa: _trocear tiene que llevarse el
# bloque <pre> entero a un trozo, o el correo saldria partido por la mitad
# con el </pre> escapado como texto.
PASA_LIMITE = dict(FICHA,
                   anexos=["W519TC27QA012_Attachment0002.pdf"],
                   especificacion_tecnica_clave=(
                       "NSN 6140-01-234-5678. P/N AB-12345. "
                       + ESPEC_LARGA + " " + ESPEC_LARGA))

h_largo = tn.formatear_analisis(PASA_LIMITE)
cuerpo_largo = _pre(h_largo)
print("  caso largo: %d caracteres" % len(h_largo))
check("El caso largo SI pasa del limite de troceo",
      len(h_largo) > 4064, "-> %d" % len(h_largo))

troceos = tn._trocear(h_largo)
check("Se trocea en vez de cortarse a lo bruto",
      len(troceos) > 1, "-> %d trozos" % len(troceos))
check("Cada trozo tiene el <pre> equilibrado",
      all(t.count("<pre>") == t.count("</pre>") for t in troceos),
      "-> %s" % [t.count("<pre>") - t.count("</pre>") for t in troceos])
check("Ningun trozo trae el </pre> escapado como texto",
      all("&lt;/" not in t for t in troceos), "-> se colo")
check("El bloque RFQ aparece VERBATIM en UN solo trozo",
      sum(1 for t in troceos if cuerpo_largo in t) == 1,
      "-> %d trozos lo contienen"
      % sum(1 for t in troceos if cuerpo_largo in t))
# Y cierra bien. OJO con lo que NO se comprueba aqui: ese trozo acaba en el
# cierre de Kyomoto, no en el EIN, porque el cierre va detras y FUERA del
# <pre>. Separar el rotulo de la seccion del cuerpo le pasa al rotulo, que
# es una linea de interfaz y no el correo: el correo es lo que tiene que
# salir entero, y sale entero.
con_cuerpo = [t for t in troceos if cuerpo_largo in t]
despues = (con_cuerpo[0][con_cuerpo[0].index(cuerpo_largo)
                         + len(cuerpo_largo):] if con_cuerpo else "")
check("Y ese trozo cierra el </pre> justo despues del EIN",
      len(con_cuerpo) == 1 and despues.lstrip().startswith("</pre>"),
      "-> despues del bloque viene %r" % despues[:40])
check("El bloque completo termina en la firma corporativa",
      cuerpo_largo.rstrip().endswith("EIN: 32-0868469"),
      "-> %r" % cuerpo_largo.rstrip()[-40:])

palabras = (" de ", " la ", " el ", " los ", " las ", " que ", " para ",
            " por ", " con ", " una ", " un ", " del ", " al ", " en ")
malas = [w.strip() for w in palabras if w in _pre(
    tn.formatear_analisis(espec_es)).lower()]
check("Ni una palabra en castellano en el correo de la ficha en espanol",
      not malas, "-> %s" % malas)


# =====================================================================
# 7) RETROCOMPATIBILIDAD
# =====================================================================
print()
print("=" * 76)
print("7) RETROCOMPATIBILIDAD")
print("=" * 76)

check("formatear_analisis(a) sigue con un solo argumento",
      tn.formatear_analisis(FICHA) != "", "-> no devolvio nada")
check("_identificador_tecnico(a) sigue devolviendo str",
      isinstance(tn._identificador_tecnico(FICHA), str), "-> no es str")
check("y sigue devolviendo lo mismo de siempre",
      tn._identificador_tecnico(FICHA) == "S0594DU0500-S1",
      "-> %r" % tn._identificador_tecnico(FICHA))
check("_identificadores_tecnicos(a) devuelve lista de str",
      isinstance(tn._identificadores_tecnicos(FICHA), list)
      and all(isinstance(x, str) for x in tn._identificadores_tecnicos(FICHA)),
      "-> no es lista de str")
check("_referencias_anexo(a) devuelve lista de str",
      isinstance(tn._referencias_anexo(FICHA), list)
      and all(isinstance(x, str) for x in tn._referencias_anexo(FICHA)),
      "-> no es lista de str")
check("_en_espanol(x) devuelve bool",
      isinstance(tn._en_espanol("acero fundido"), bool), "-> no es bool")
check("_anexos(opp) devuelve lista",
      isinstance(gemini_analyzer._anexos({}), list), "-> no es lista")

fuente = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "telegram_notify.py"), encoding="utf-8").read()
check("Ningun secreto en el modulo",
      "TELEGRAM_BOT_TOKEN=" not in fuente
      and "SAM_API_KEY=" not in fuente
      and "GEMINI_API_KEY=" not in fuente, "-> hay una clave pegada")

check("El orden de secciones no se mueve",
      "✉️ <b>RFQ Listo para Enviar (Copy-Paste)</b>" in tn.formatear_analisis(FICHA)
      and tn.formatear_analisis(FICHA).find("🔗")
      < tn.formatear_analisis(FICHA).find("✉️"), "-> se movio")


# ----------------------------------------------------------------- fin
print()
print("=" * 76)
if FALLA:
    print("RESULTADO: %d FALLO(S)" % FALLA)
    sys.exit(1)
print("RESULTADO: todo en verde")
sys.exit(0)
