"""
Capa B: desglose semantico de un LOTE consolidado, solo si lo hay.

QUE ES UN LOTE CONSOLIDADO

SAM.gov agrupa a veces componentes complejos bajo una sola linea de la tabla:
"1 LOT", "1 EA", "1 LO". La tabla es administrativamente correcta y
comercialmente inutil: no dice que hay dentro del lote.

Eso deja una asimetria de informacion: el oficial ve "1 LOT", y el
distribuidor tendria que pedirle a SAM.gov que le detalle el contenido. Nadie
pide eso, y el pricedor no puede cotizar "1 LOT" sin saber que es.

CAPA A, LA BANDERA

    consolidated_lot = (cantidad == 1) and unidad in ("EA", "LO", "LOT")

La define Capa A en pdf_generator, al construir el item del BOM. Ahi es donde ya
se sabe la cantidad y la unidad, y por eso es el sitio correcto: si se definiera
mas arriba, un item con 2 LOT no activaria nada y uno con 1 EA de una pieza
normal lo haria sin querer.

CUANDO consolidated_lot ES False, NADA DE ESTO SE EJECUTA

Ni una llamada, ni una lectura de la cache, ni una comprobacion de cuota. Es lo
que mantiene el principio de no-regresion: la ruta feliz no cambia ni un mili-
metro, y las 39 pruebas que habia siguen dando lo mismo porque nunca entran por
aqui.

CUANDO ES True, Y SOLO ENTONCES

Una llamada al modelo sobre el texto plano de la licitacion, con seis campos.

LAS MISMAS BARRERAS QUE EL RESTO

  - la cuota se comprueba ANTES de gastar, y si no se puede comprobar se
    asume que no hay. Con la cuota agotada, 0 llamadas.
  - el resultado se cachea por notice_id, y el fallo tambien, con la misma
    ventana de 60 minutos.

    La clave de cache lleva sufijo (":lote") para no pisar el parsing normal,
    que devuelve otra cosa y va en la misma tabla.

  - nunca lanza. Si algo falla, se devuelve {} y el PDF sale igual.

LO QUE SE HACE CON LO QUE DEVUELVE EL MODELO

Igual que en el parsing normal: nada se imprime tal cual. Se vacian los campos
vacos, se descartan los que no son datos (una frase entera de "see attached"),
y se marca la fuente de cada uno.

POR QUE ESTO NO VA AL PDF

El PDF lo lee el distribuidor. Un parte de fabricante, una certificacion
requerida y un desglose de unidades son informacion INTERNA de compra: sirven
para saber si el negocio tiene sentido antes de llamar a tres proveedores, no
para el que te esta cotizando. Meterlo en el documento comercial lo convierte en
papel viejo, y el precio en blanco (que si importa, porque lo rellena el
proveedor) queda diluido entre lineas que no le sirven.

Asi que el bloque va solo al mensaje de Telegram. El PDF se mantiene limpio y
formal, que es lo que pide el encargo.
"""
import json
import logging
import re

import config

log = logging.getLogger("kyomoto.lote")

MAX_CARACTERES = 7000
MAX_CORPUS = 18000

# Las unidades con las que un aviso consolida. "LO" es como aparece en algunos
# catalogos; "LOT" y "EA" son las comunes.
UNIDADES_LOTE = frozenset({"EA", "LO", "LOT"})

# Campo que puede contener un numero de piezas, y como se normaliza.
#
# La respuesta del modelo es texto libre ("24 bearings", "100 each", "6 exit
# devices, 4 strikes"), porque esa es la forma en que un comprador humano lo
# diria. Para poder sobrescribir el QTY del PDF hace falta un entero. De ahi
# _unidades_numericas().

CAMPOS = (
    "resolved_product_name",
    "actual_units_required",
    "dimensions_or_specs",
    "manufacturer_brand_name",
    "manufacturer_part_number",
    "certification_requirements",
)

PROMPT = """\
Eres un comprador experto de adquisiciones de EE.UU. Lee la licitacion de
SAM.gov que describe UN LOTE consolidado y saca lo que hay realmente dentro.

AVISO
---
{aviso}
---

Este aviso consolida varios componentes bajo una sola linea de la tabla de
precio (algo asi como "1 LOT" o "1 EA"). Tu trabajo es deshacer esa
consolidacion.

Devuelve SOLO este JSON, sin texto alrededor ni cercas de markdown:

{{
  "resolved_product_name": "",
  "actual_units_required": "",
  "dimensions_or_specs": "",
  "manufacturer_brand_name": "",
  "manufacturer_part_number": "",
  "certification_requirements": ""
}}

QUE VA EN CADA CAMPO

resolved_product_name
  El nombre COMERCIAL del material: "Timken S-1 Series Insert Bearing",
  "Von Duprin 98A Exit Device". No "1 lot", no "miscellaneous parts".

actual_units_required
  Cuantas unidades REALES hay DENTRO del lote. Si el desglose son varios
  articulos, da el desglose Y el total: "24 bearings, 6 exit devices, 4
  mounting kits, 12 strike plates (46 pieces total)".
  El total importa mas que el desglose, porque es lo que va en la columna de
  cantidad del documento. Si el texto no lo dice, sumalo tu y marcalo como
  estimado. NO copies el "1" de la tabla de precio: ese es el numero de
  lotes, no de piezas.

dimensions_or_specs
  Medidas fisicas exactas: "5.00 x 96 in, 1/4 in thick", tolerancias,
  material, acabado, norma. Si no hay medidas, pon el material y la norma.

manufacturer_brand_name
  La marca o el fabricante que el aviso menciona o que ese material exige.
  Si no se menciona ninguno, pon "" en vez de inventar uno.

manufacturer_part_number
  El parte exacto: "S0594DU0500-S1", "98A/9850". Si no hay, "".

certification_requirements
  Las certificaciones exigidas: "Certificate of Conformance (CoC)",
  "FAA 8130-3", "MIL-STD-1333". Si no se exige ninguna, pon "".

REGLAS

- Nada de texto administrativo: narratives de enmienda, acuses, plazos.
- En ingles de adquisiciones de defensa.
- NO inventes. Si un dato no esta en el aviso, cadena vacia. Es preferible un
  campo vacio a un parte de fabricante equivocado, porque un parte equivocado
  se pide, se espera, no llega, y el tiempo perdido es el del otro.
- No copies el "1" de la tabla como cantidad de piezas.
"""


_RE_TRAMITE = re.compile(
    r"\b(this\s+amendment|is\s+being\s+issued|interested\s+parties"
    r"|point\s+of\s+contact|acknowledge\s+receipt|narrative|amd\s*0*\d"
    r"|see\s+attached|no\s+response\s+is\s+required)\b", re.I)


def es_lote_consolidado(item: dict) -> bool:
    """
    CAPA A: True si la linea consolida componentes.

    QTY == 1 y la unidad es EA, LO o LOT.

    El 1 es EXACTO, no ">=". Una linea con 2 LOT no es un lote consolidado: son
    dos lotes, y el desglose no aplica igual. Y una linea con 1 EA de una
    pieza suelta tampoco, pero no hay forma de distinguirla sin leer el texto, y
    la regla que se ha pedido es esta.
    """
    if not isinstance(item, dict):
        return False
    try:
        q = float(item.get("cantidad") or 0)
    except (TypeError, ValueError):
        return False
    if q != 1.0:
        return False
    return str(item.get("unidad") or "").strip().upper() in UNIDADES_LOTE


def _limpiar(valor) -> str:
    """Un campo de texto, limpio y acotado. "" si no es un dato."""
    t = " ".join(str(valor or "").split())
    if not t or _RE_TRAMITE.search(t):
        return ""
    if len(t) < 2:
        return ""
    return t[:220]


# "24 bearings", "100 each", "6 exit devices, 4 mounting kits".
#
# El sustantivo comun CUENTA como unidad, porque es como lo escribe una persona:
# "24 bearings" son 24 bearings, una cada una. Por eso se acepta cualquier
# palabra tras el numero y no solo las siglas de catalogo.
#
# Y eso obliga a filtrar, porque "5 inch pump", "30 year warranty" y "page 12"
# tambien son "numero + palabra". Lo que se descarta esta en _NO_UNIDAD.
_RE_UNIDADES = re.compile(
    r"(?<!\d)(\d{1,9})\s*(?:\+|x)?\s*"
    r"(ea|each|eaches|units?|pieces?|pcs?|kits?|sets?|bags?|boxes|rolls?"
    r"|containers?|cans?|cases?|lots?|pairs?|[a-z]{3,})(?![a-z])", re.I)

# Palabras que PARECEN una unidad y no lo son: medidas, plazos, referencias de
# pagina y conectores. Sin esta lista, "5 inch pump" se leeria como cinco
# pulgadas pedidas, y eso va a la columna QTY de un documento que va a un
# distribuidor.
#
# Y lo que NO esta aqui, aunque en una version anterior si, por decision:
#
#   each, per, total, reference, requirement
#
# No son unidades: son ETIQUETAS de la cifra. Y son precisamente las palabras
# con las que un comprador humano dice la cantidad: "100 each", "46 total",
# "24 pieces, reference drawing 1110". Con "each" y "total" en la lista negra,
# "100 each" daba 0 y se perdia la cantidad mas clara que existe.
_NO_UNIDAD = re.compile(
    r"^(in|inch|inches|ft|foot|feet|yards?|yd|m|met(?:er|re)s?|cm|mm|km|mi|mile"
    r"|miles|kg|kgs|lb|lbs|pounds?|oz|ounce|ounces|g|gal|gallons?|l|liter|"
    r"litre|litres|ml|pt|qt|"
    r"year|years|yr|day|days|week|weeks|month|months|hour|hours|minute|minutes"
    r"|page|pages|pg|section|sections|sec|chapter|annex|"
    r"item|items|line|lines|drawing|drawings|dwg|figure|figures|table|"
    r"amendment|amendments|amd|attachment|attachments|note|notes|"
    r"paragraph|paragraphs|clause|clauses|option|options|"
    r"and|or|of|for|with|from|shall|must|are|is|includes|including|"
    r"all|approximately|about|minimum|maximum|min|max|"
    r"deliverable|deliverables|requirement|requirements|specification|"
    r"specifications|warranty|coverage|assembly|assemblies)$", re.I)


def _unidades_numericas(texto: str) -> int:
    """
    El numero de piezas de un texto libre. 0 si no hay uno claro.

    Devuelve el MAYOR numero que aparece con una unidad de issue, no el
    primero. La razon es que en un desglose tipico el texto va asi:

        "24 bearings, 6 exit devices, 4 mounting kits, 12 strike plates"

    El primero es 24, que es solo los rodamientos. El total que hay que
    cotizar es 46, que es la suma. Y la suma NO esta escrita en ninguna parte,
    asi que hay que calcularla.

    Se devuelve el mayor, que es una cota INFERIOR del total pero nunca mayor,
    y eso es la direccion buena: quedarse corto se corrige con el proveedor,
    mientras que pasarse de cantidad en un pedido federal es un problema de
    verdad. El LLM suele dar la suma correcta en el texto, y entonces el mayor
    coincide con ella.
    """
    limpio = _RE_TRAMITE.sub(" ", str(texto or ""))
    numeros = []
    for m in _RE_UNIDADES.finditer(limpio):
        palabra = (m.group(2) or "").strip()
        if _NO_UNIDAD.match(palabra):
            continue
        try:
            numeros.append(int(m.group(1)))
        except ValueError:
            continue
    return max(numeros) if numeros else 0


def _validar(datos: dict) -> dict:
    """
    Devuelve solo los seis campos, limpios, y la fuente de cada uno.

    Se exige que traiga AL MENOS uno de los utiles (producto o unidades). Un
    JSON con los seis vacios no es un resultado, es un fallo lento: sin esto se
    cachearia como exito un "nada que contar" y no se reintentaria nunca.
    """
    salida, fuente = {}, {}
    for campo in CAMPOS:
        limpio = _limpiar(datos.get(campo))
        salida[campo] = limpio
        fuente[campo] = "ia" if limpio else "vacio"
    # El numero que puede corregir el QTY del PDF. Va aparte del texto porque
    # son dos cosas: lo que el modelo DICE ("24 bearings, 6 exit devices") y
    # la cifra que se puede poner en una columna. Si no estan separados, para
    # sobrescribir una cantidad hay que volver a parsear el texto en el sitio
    # que sobrescribe, que es donde no toca tocar.
    salida["unidades_numericas"] = _unidades_numericas(
        datos.get("actual_units_required"))
    if not salida["resolved_product_name"] and not salida["actual_units_required"]:
        return {}
    salida["fuente"] = fuente
    return salida


def _leer_json(bruto: str) -> dict:
    if not bruto:
        return {}
    limpio = re.sub(r"^```(?:json)?|```$", "", bruto.strip(),
                     flags=re.MULTILINE).strip()
    for candidato in (limpio, None):
        if candidato is None:
            a, b = limpio.find("{"), limpio.rfind("}")
            if a == -1 or b <= a:
                return {}
            candidato = limpio[a:b + 1]
        try:
            obj = json.loads(candidato)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            continue
    return {}


def resolver_lote(aviso: dict, descripcion: str,
                  usar_cache: bool = True, corpus: str = "") -> dict:
    """
    CAPA B. Devuelve el desglose del lote, o {} si no hay lote o si falla.

    NUNCA LANZA. La funcion que la llama (desde_aviso) la envuelve ademas en un
    try/except, porque una excepcion aqui no puede impedir que se genere el
    PDF de un documento que ya se sabe generar.
    """
    import store

    nid = str(aviso.get("noticeId") or "")
    texto = str(descripcion or "").strip()
    titulo = str(aviso.get("title") or "")
    if not texto:
        log.info("resolver_lote('%s'): el aviso no trae descripcion", nid)
        return {}
    if not config.GEMINI_API_KEY:
        return {}

    clave = (nid + ":lote") if nid else ""

    # --- Cache. La clave lleva sufijo porque el parsing normal ocupa la misma
    #     tabla con otra cosa. ---
    if usar_cache and clave:
        guardados, modelo, edad = store.leer_estructura(clave)
        if guardados:
            log.info("resolver_lote('%s'): desde cache (%s)", nid, modelo)
            return guardados
        if 0 <= edad < config.MINUTOS_REINTENTO_LOTE:
            log.info("resolver_lote('%s'): fallo cacheado hace %.0f min", nid, edad)
            return {}

    # --- Cuota ANTES de gastar ---
    import quota
    try:
        if quota.quedan() < 1:
            log.info("resolver_lote('%s'): sin cuota", nid)
            return {}
    except Exception as e:
        log.warning("resolver_lote('%s'): no se pudo comprobar la cuota (%s); "
                    "se asume que no hay", nid, e)
        return {}

    try:
        from google import genai
    except ImportError:
        return {}

    # El corpus es la DESCRIPCION MAS los adjuntos. Los adjuntos son donde
    # esta el volumen real de un lote consolidado: la tabla dice "1 LOT" porque
    # eso es lo que se compra, y el desglose esta en el anexo 0002.
    #
    # El reparto de presupuesto entre las dos partes es deliberado: la
    # descripcion va primero y con mas sitio, porque define el item, y los
    # adjuntos despues. Un adjunto es un documento largo y el LLM puede
    # quedarse sin al final, que es justo donde estaria el desglose.
    cuerpo = texto[:MAX_CARACTERES]
    if corpus:
        cuerpo = (cuerpo + "\n\n===== DOCUMENTOS ADJUNTOS DEL AVISO "
                  "(purchase descriptions, drawings, QnA, amendments) =====\n"
                  + corpus[:MAX_CORPUS])
    if titulo:
        cuerpo = f"TITLE: {titulo}\n\n{cuerpo}"
    prompt = PROMPT.format(aviso=cuerpo)

    cola = [config.GEMINI_MODEL] + [
        m for m in config.GEMINI_MODELES_ALTERNATIVOS
        if m and m != config.GEMINI_MODEL
    ]
    salida, modelo_usado = {}, ""
    for modelo in cola[:4]:
        try:
            cliente = genai.Client(api_key=config.GEMINI_API_KEY)
            from google.genai import types
            respuesta = cliente.models.generate_content(
                model=modelo,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                    max_output_tokens=1200,
                ),
            )
            salida = _validar(_leer_json(getattr(respuesta, "text", "") or ""))
            if salida:
                modelo_usado = modelo
            break
        except Exception as e:
            texto_err = str(e)
            if "429" in texto_err or "RESOURCE_EXHAUSTED" in texto_err:
                log.warning("resolver_lote('%s'): 429 en %s, se prueba el "
                            "siguiente", nid, modelo)
                try:
                    quota.gastar_fallo()
                except Exception:
                    pass
                continue
            log.warning("resolver_lote('%s'): %s fallo: %s", nid, modelo,
                        texto_err[:150])
            continue

    if not salida:
        if usar_cache and clave:
            try:
                store.guardar_estructura(clave, {}, "")
            except Exception:
                pass
        return {}

    salida["_modelo"] = modelo_usado
    log.info("resolver_lote('%s'): %s | %s / %s", nid, modelo_usado,
             salida.get("resolved_product_name")[:60],
             salida.get("actual_units_required")[:40])
    try:
        quota.gastar(1)
    except Exception:
        pass
    if usar_cache and clave:
        try:
            store.guardar_estructura(clave, salida, modelo_usado)
        except Exception:
            pass
    return salida


def formatear_bloque(detalle: dict) -> str:
    """
    El bloque analitico para el mensaje interno de Telegram.

    Va SOLO aqui, nunca en el PDF. Es informacion de compra interna: sirve para
    decidir si el negocio tiene sentido, no para el proveedor al que se le pide
    precio.

    Los campos que el modelo no devolvio se omiten en vez de salir vacios: seis
    lineas con guiones vacios dan la impresion de que se intento y no se pudo,
    cuando a lo mejor solo no habia parte de fabricante.
    """
    if not detalle:
        return ""
    d = detalle

    def _linea(emoji, etiqueta, valor):
        val = str(valor or "").strip()
        return f"{emoji} {etiqueta}: {val}" if val else ""

    producto = str(d.get("resolved_product_name") or "").strip()
    specs = str(d.get("dimensions_or_specs") or "").strip()
    lineas = []
    if producto:
        lineas.append(_linea("📦", "Producto Real",
                            f"{producto} ({specs})" if specs else producto))
    elif specs:
        lineas.append(_linea("📦", "Producto Real", specs))
    unidades = str(d.get("actual_units_required") or "").strip()
    if unidades:
        lineas.append(_linea("🔢", "Cantidad Desglosada",
                            f"{unidades} (consolidadas en la tabla como 1 Lote)"))
    marca = str(d.get("manufacturer_brand_name") or "").strip()
    if marca:
        lineas.append(_linea("🏭", "Fabricante Sugerido", marca))
    parte = str(d.get("manufacturer_part_number") or "").strip()
    if parte:
        lineas.append(_linea("🏷️", "Part Number exacto", parte))
    cert = str(d.get("certification_requirements") or "").strip()
    if cert:
        lineas.append(_linea("📜", "Certificación obligatoria", cert))

    if not lineas:
        return ""
    cuerpo = "\n".join(f"  <i>•</i> {l}" for l in lineas)
    return ("\n🔍 <b>DETALLE FINO DE COMPRA DESCRIPTIVA</b>\n"
            + cuerpo + "\n\n<i>Solo para uso interno. El PDF que se manda al "
                        "distribuidor no lleva estos datos.</i>")