"""
capa_b.py - El extractor estructurado, y su red de seguridad.

QUE ES Y QUE NO ES

Gemini aqui es un LECTOR. Recibe texto y devuelve un diccionario de campos.
No escribe codigo, no ejecuta nada, no toca el PDF, y no puede hacer que el
programa haga otra cosa que lo que ya hace.

Eso no es una convencion social, es la forma de la llamada: se le pide JSON con
response_mime_type y se parsea con json.loads. Aunque devolviera codigo, lo que
acaba en el documento es un string, no una sentencia. No hay eval, ni exec, ni
exec de plantilla. Si alguien cambia el PROMPT para pedirle otra cosa, lo que
consigue es otro texto en una celda.

POR QUE HAY UNA CAPA C

Porque la ruta critica no puede depender de que una API de terceroseste viva.
El escenario real: se agota la cuota a las 15:40, el usuario pulsa /pdf sobre
un lote consolidado, y sin Capa C el documento sale con QTY 1 y nadie sabe por
que. Con Capa C, sale con la cifra leida del propio corpus.

Y aqui hay una diferencia importante con lo que se pidio en un principio, y
conviene dejarla escrita. La Capa C NO "fuerza la inyeccion de la cifra 100".
Fuerza la inyeccion de LO QUE ESTA ESCRITO en el corpus. La diferencia parece
de matiz y no lo es:

    "QUANTITY: 100 EA"          -> la cifra es una cantidad. Se usa.
    "Drawing 1110700278"        -> la cifra es un plano. NO se usa.
    "See sheet 3 of 12"         -> la cifra es una pagina. NO se usa.

Un numero de pieza es la forma mas comun de numero que hay en un documento
tecnico del gobierno. Si la Capa C fuerza sin comprobar, un dia mete
1110700278 en la columna de cantidad de un pedido federal, y eso no se
detecta mirando el PDF: parece un numero normal.

Asi que la Capa C aplica la misma regla que la Capa B: la cifra tiene que
ESTAR ESCRITA en el corpus como cifra suelta, no como parte de un
identificador. Las dos capas pueden discrepar; cuando lo hacen, la mas
estrecha gana.

EL ESQUEMA

Los nombres de campo son los del encargo, y se respetan tal cual porque
cambian el contrato con el prompt. Si se renombran aqui, hay que renombrarlos
alli, y el JSON cacheado de avisos antiguos dejaria deerkennenlos.
"""
import json
import logging
import re

import config

log = logging.getLogger("kyomoto.capa_b")

# El esquema. El orden es el que lee el modelo, asi que empieza por la
# pregunta importante (override_required) y no por la descripcion larga.
# Los seis campos del encargo, tal cual y en el mismo orden.
#
# Y dos mas, OPCIONALES, que no estaban en el encargo pero hacen falta.
#
# El encargo previo pedia en el PDF "Certificado de Conformidad - CoC
# obligatorio, entrega en camion plancha". Sin estos dos campos, esa
# especificacion se queda sin sitio: se pide en el prompt y el JSON no tiene
# donde guardarla, y el modelo la suelta. Un requisito de entrega que no llega
# al papel es un requisito que no se cumple, porque el proveedor no lo lee.
#
# Son opcionales de verdad: si el modelo no las devuelve, van vacias y el PDF
# dibuja una linea menos. Anadirlas no rompe el esquema pedido.
ESQUEMA = {
    "override_required": "bool",
    "target_qty": "int",
    "target_uoi": "str",
    "manufacturer_brand": "str",
    "part_number": "str",
    "product_specification_summary": "str",
    "dimensions_or_specs": "str",
    "certification_requirements": "str",
}

# Las claves del esquema, en el mismo orden.
CLAVES = ("override_required", "target_qty", "target_uoi", "manufacturer_brand",
          "part_number", "product_specification_summary",
          "dimensions_or_specs", "certification_requirements")

PROMPT = """Eres un extractor de especificaciones para un documento de compra.

Te doy el texto integro de los adjuntos de una licitacion del gobierno de los
Estados Unidos. Tu unico trabajo es devolver un JSON con los campos de la
esquema. No escribas texto, no expliques, no anadas campos.

ESQUEMA (respeta estos nombres y estos tipos, nada mas):
{{
  "override_required": true | false,
  "target_qty": <entero>,
  "target_uoi": "<sigla>",
  "manufacturer_brand": "<nombre del fabricante>",
  "part_number": "<numero de parte del fabricante>",
  "product_specification_summary": "<material y medida, una linea>",
  "dimensions_or_specs": "<medidas y tolerancias, o cadena vacia>",
  "certification_requirements": "<CoC, MIL-STD, ISO, o cadena vacia>"
}}

REGLAS, Y NO SON SUGERENCIAS:

1. override_required ponlo a true SOLO si el texto dice una cantidad de piezas
   o unidades que CONTRADICE la tabla del aviso. La tabla compra "1 LOT" o
   "1 EA" porque eso es lo que se compra como unidad; el volumen real esta
   desglosado en los adjuntos. Si no hay desglose, false.

2. target_qty es el TOTAL de piezas del desglose, no la primera cifra que
   veas y no un numero de plano, pagina, seccion ni dibujo.
   Si el desglose son varios articulos, sumalos y da el total.
   Si no puedes determinarlo con certeza, override_required=false.

3. target_uoi es una sigla de unidad de issue corriente (EA, BG, KT, CA, PR,
   SET). Si no hay sigla en el texto, "EA".

4. manufacturer_brand y part_number: SOLO si aparecen escritos en el texto.
   Si no aparecen, cadena vacia. No los deduzcas, no los completes, no los
   adivines. Un numero de parte inventado es peor que ninguno.

5. product_specification_summary: una sola linea, en ingles tecnico, con el
   material y la medida. Sin el numero de serie, sin el NSN.

6. Si el texto esta en otro idioma, responde igual en las claves de texto:
   en ingles.

AVISO:
{aviso}
"""

# La misma regla de "una cifra es una cifra" que se usa en la Capa C, para que
# las dos capas no papan de forma distinta.
_RE_FABRICANTE = re.compile(
    r"(?:manufactured\s+by|manufacturer|made\s+by|fabricado\s+por)\s*[:#]?\s*"
    r"([A-Z][\w&.\- ]{2,40}?)"
    r"(?=[,.;\n]|\s+(?:co|inc|corp|company|ltd|llc)\b|\s*$)")

_RE_PARTE = re.compile(
    r"\b(?:part\s*(?:no|number|#)|p/?n|model)\s*[:#]?\s*"
    r"([A-Z0-9][A-Z0-9\-/]{3,24})", re.I)

_UOI = ("EA", "BG", "KT", "CA", "PR", "CS", "BX", "PK", "SET", "HD", "CON",
        "LO", "LOT")

# Fallas que significan "no hay Gemini", no "Gemini ha dicho que no".
# La 400 por clave invalida va aqui: no es cuota, es que no hay quien responder,
# y reintentar los cuatro modelos con la misma clave invalida solo gasta
# cuatro segundos.
_FALLA_API = ("429", "RESOURCE_EXHAUSTED", "quota", "API key not valid",
              "INVALID_ARGUMENT", "401", "403", "PERMISSION_DENIED",
              "timed out", "Connection", "Temporary failure", "503", "502")


def _a_int(valor) -> int:
    """Un entero, o 0. Nunca una excepcion."""
    try:
        n = int(str(valor).strip().split(".")[0])
    except (TypeError, ValueError, AttributeError):
        return 0
    return n if n > 0 else 0


def _a_bool(valor) -> bool:
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("true", "1", "yes", "si", "required")


def _limpiar(valor) -> str:
    v = str(valor or "").strip().strip('"').strip()
    # Una sola linea. Un salto de linea dentro de una celda de PDF es un
    # problema de maquetado, y el prompt ya pide una linea.
    return " ".join(v.split())[:300]


def _vacio() -> dict:
    d = {"override_required": False, "target_qty": 0, "target_uoi": "",
         "manufacturer_brand": "", "part_number": "",
         "product_specification_summary": "", "dimensions_or_specs": "",
         "certification_requirements": "", "_capa": ""}
    return d


def validar(objeto) -> dict:
    """
    Del JSON que sea al diccionario del esquema, con los tipos puestos.

    Se ignora cualquier clave que no sea del esquema. Si el modelo devuelve
    algo de mas, se tira, y con razon: lo que no se valida aca lo validara
    quien lea, que sera el PDF.
    """
    salida = _vacio()
    if not isinstance(objeto, dict):
        return salida
    salida["override_required"] = _a_bool(objeto.get("override_required"))
    salida["target_qty"] = _a_int(objeto.get("target_qty"))
    uoi = _limpiar(objeto.get("target_uoi")).upper()
    salida["target_uoi"] = uoi if uoi in _UOI else ""
    for clave in ("manufacturer_brand", "part_number",
                  "product_specification_summary", "dimensions_or_specs",
                  "certification_requirements"):
        salida[clave] = _limpiar(objeto.get(clave))
    # Si no hay cantidad, no puede haber override. Sin esta linea, un modelo
    # que dice {"override_required": true, "target_qty": 0} intenta escribir un
    # 0 en la columna de cantidad de un pedido federal.
    if salida["target_qty"] <= 0:
        salida["override_required"] = False
    salida["_capa"] = "B"
    return salida


# ------------------------------------------------------------------ CAPA C
def capa_c(corpus: str) -> dict:
    """
    La red de seguridad: expresiones regulares sobre el corpus.

    NO fuerza una cifra. Sale del texto, y solo si es una cifra suelta.
    """
    texto = str(corpus or "")
    salida = _vacio()
    if not texto.strip():
        return salida

    import integral
    import lote_consolidado as lc

    qty = lc._unidades_numericas(texto)
    # La MISMA comprobacion que usa la Capa B: la cifra tiene que estar
    # escrita, y como cifra, no como parte de "ABC-100" ni de "100-A".
    if qty <= 0 or not integral._numero_en_corpus(qty, texto):
        log.info("capa_c: hay una cifra (%s) pero no como cantidad; no se "
                 "corrige", qty or "(ninguna)")
        salida["_capa"] = "C"
        return salida

    salida["target_qty"] = qty
    salida["override_required"] = True
    salida["target_uoi"] = "EA"
    salida["_capa"] = "C"

    m = _RE_FABRICANTE.search(texto)
    if m:
        salida["manufacturer_brand"] = _limpiar(m.group(1))
    m = _RE_PARTE.search(texto)
    if m:
        salida["part_number"] = _limpiar(m.group(1))
    # Sin resumen de especificacion: la Capa C no sabe escribir una frase
    # tecnica sin inventarla, y el PDF tiene hueco de sobra para que la
    # rellene el distribuidor.
    log.info("capa_c: %s EA desde el corpus, parte=%r, marca=%r", qty,
             salida["part_number"], salida["manufacturer_brand"])
    return salida


# ------------------------------------------------------------------ CAPA B
def extraer(corpus: str, titulo: str = "") -> dict:
    """
    Capa B. El extractor estructurado.

    Devuelve el diccionario del esquema. NUNCA lanza y NUNCA devuelve algo
    que no sea el esquema: si Gemini no esta, cae a la Capa C y lo dice en el
    log, con el texto exacto que pidio el encargo.
    """
    texto = str(corpus or "")
    if not texto.strip():
        log.info("capa_b: sin corpus; no hay nada que extraer")
        return _vacio()

    if not config.GEMINI_API_KEY:
        log.warning("[WARNING] Capa B no ejecutada debido a fallo de API")
        return capa_c(texto)

    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        log.warning("[WARNING] Capa B no ejecutada debido a fallo de API (%s)",
                    e)
        return capa_c(texto)

    aviso = texto
    if titulo:
        aviso = "TITLE: %s\n\n%s" % (titulo, texto)
    prompt = PROMPT.format(aviso=aviso)

    for modelo in [config.GEMINI_MODEL] + [
            m for m in config.GEMINI_MODELES_ALTERNATIVOS
            if m and m != config.GEMINI_MODEL][:3]:
        try:
            cliente = genai.Client(api_key=config.GEMINI_API_KEY)
            respuesta = cliente.models.generate_content(
                model=modelo,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                    max_output_tokens=900,
                ),
            )
            crudo = str(getattr(respuesta, "text", "") or "")
            limpio = re.sub(r"^```(?:json)?|```$", "", crudo.strip(),
                            flags=re.MULTILINE).strip()
            a = limpio.find("{")
            b = limpio.rfind("}")
            if a == -1 or b <= a:
                log.info("capa_b: %s no devolvio JSON utilizable", modelo)
                continue
            salida = validar(json.loads(limpio[a:b + 1]))
            if salida["_capa"]:
                log.info("capa_b: %s -> override=%s qty=%s uoi=%s",
                         modelo, salida["override_required"],
                         salida["target_qty"], salida["target_uoi"] or "-")
                try:
                    import quota
                    quota.gastar(1)
                except Exception:
                    pass
                return salida
        except Exception as e:
            texto_err = str(e)
            if any(s in texto_err for s in _FALLA_API):
                # Quota, red o clave. Aqui es donde cae el encargo: log con el
                # texto pedido y a la Capa C, para que el documento no salga
                # vacio de cantidad.
                log.warning("[WARNING] Capa B no ejecutada debido a fallo "
                            "de API (%s: %s)", modelo, texto_err[:120])
                try:
                    import quota
                    quota.gastar_fallo()
                except Exception:
                    pass
                return capa_c(texto)
            log.warning("capa_b: %s fallo: %s", modelo, texto_err[:150])
            continue

    # Se agoto la cola sin respuesta buena. No es un fallo de API, es que el
    # modelo no sirvio. La Capa C tambien sirve aqui: el corpus esta a mano y
    # no depende de nadie.
    log.warning("[WARNING] Capa B no devolvio nada utilizable; se aplica la "
                "Capa C sobre el corpus")
    return capa_c(texto)