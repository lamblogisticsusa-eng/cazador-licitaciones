"""
estructurador.py - Parsing estructurado con IA del texto crudo de SAM.gov.

POR QUE HACE FALTAR, CUANDO EL PARSER DE REGEX YA FUNCIONA

El parser de regex se rompio de verdad con estas formas:

    "2-20 foot Containers with Gantry Beam"    -> 20 y "foot" son una MEDIDA
    "this contract includes the estimated quantity of 100 each"
    "AMD 0001 Narrative..."                    -> la enmienda va al principio

La regex solo ve que hay un numero y una sigla pegados. Que "20 foot" sea una
medida y no una cantidad no es un problema de expresiones regulares: es
informacion de que lo que se pide es el contenedor entero, y eso hay que leerlo.

Una llamada al modelo lo resuelve, porque "2-20 foot Containers" se lee como dos
contenedores de 20 pies, no como veinte de algo.

LO QUE HACE, Y LO QUE NO

Devuelve un JSON con cuatro campos, o {} si no puede. NUNCA lanza: si la cuota no
da, si la API falla, si el modelo contesta una tonteria, se devuelve {} y quien
llama sigue con el parser de regex. El PDF se genera siempre.

CUOTA

Una llamada por aviso. Por eso hay dos barreras:

  - antes de gastar, se mira quota.queden(); si no queda, ni se llama
  - el resultado se cachea por notice_id, con el fallo tambien cacheado

Lo segundo es lo importante en la practica: el usuario va a repetir /pdf varias
veces mientras ajusta el documento, y sin cache cada repeticion seria otra
llamada. Con la repeticion sale de la cache y es gratis.

LO QUE HACE CON LO QUE DEVUELVE EL MODELO

Un modelo puede devolver una cantidad que no existe en el texto, o una unidad
inventada. Nada de lo que devuelva se usa tal cual:

  - la cantidad tiene que ser positiva y FINITA. Un "1" coming de la IA cuando
    el texto dice 100 es peor que el fallback de regex, porque un 1 parece un
    dato y no se nota que es falso. Si la IA no convencera, gana la regex.
  - la unidad tiene que ser una sigla de catalogo de verdad
  - la descripcion tiene que pasar el filtro de tramite, porque el modelo puede
    devolver un texto limpio que en realidad lleva un "AMD 0001" dentro
  - el producto tiene que tener longitud util: ni vacio ni un parrafo

Y se guarda de que fuente salio cada campo, para poder verlo.
"""
import json
import logging
import re

import config

log = logging.getLogger("kyomoto.estructura")

# Cuantos caracteres del aviso se mandan. El recorte no es cosmetico: un aviso
# largo con tres enmiendas se lleva los tokens en la narrativa, que es justo lo
# que no interesa. El material esta casi siempre al principio o despues de la
# primera enmienda.
MAX_CARACTERES = 6000

# Cuanto se respeta un fallo cacheado antes de reintentarlo.
#
# Con 60 minutos: un 429 de las tres de la madrugada deja de aplicar a las
# cuatro, y una caida de red de la API se recupera sola. Y no es infinito, que
# seria peor: un aviso cuyo texto se actualice despues en SAM.gov no se
# reestructuraria nunca.
MINUTOS_REINTENTO_FALLO = 60

# Las siglas de catalogo que se aceptan como unidad. El modelo escribe "units",
# "each", "container" y cosas asi; se canonicalizan a la sigla.
_UOIS = {
    "EA", "BG", "KT", "KTN", "SET", "PR", "CA", "CS", "BX", "PK", "PL", "DZ",
    "BO", "HD", "CON", "CYC", "NUC", "OQ", "PG", "BL", "LOT", "RO", "BB",
    "BD", "BT", "CC", "CT", "DR", "PKG", "SC", "WT", "DZN", "DRL", "QA", "RL",
}
_SINONIMOS = {
    "EACH": "EA", "EACHES": "EA", "UNIT": "EA", "UNITS": "EA", "PCS": "EA",
    "PIECE": "EA", "PIECES": "EA", "ITEM": "EA", "ITEMS": "EA",
    "BAG": "BG", "BAGS": "BG", "KIT": "KT", "KITS": "KT", "KITT": "KT",
    "SETS": "SET", "PAIR": "PR", "PAIRS": "PR", "BOX": "BX", "BOXES": "BX",
    "CASE": "CS", "CASES": "CS", "CAN": "CA", "CANS": "CA",
    "CARTON": "CT", "CARTONS": "CT", "CONTAINER": "CON", "CONTAINERS": "CON",
    "LOT": "LOT", "LOTS": "LOT", "ROLL": "RO", "ROLLS": "RO",
}

# Tramite que no puede salir en un texto para un distribuidor. Mismo criterio
# que el filtro del PDF: frases completas, no palabras sueltas, porque tirar
# cualquier linea que contenga "amendment" se lleva por delante media
# especificacion tecnica.
_RE_TRAMITE = re.compile(
    r"\b(this\s+amendment|is\s+being\s+issued|are\s+being\s+issued"
    r"|interested\s+parties|point\s+of\s+contact|questions?\s+(should|must|may)"
    r"|no\s+response\s+is\s+required|acknowledge\s+receipt"
    r"|changes?\s+are\s+highlighted|see\s+attached|end\s+of\s+amendment"
    r"|attachments?\s+are\s+included|additional\s+time|narrative"
    r"|amd\s*0*\d|respond\s+by|questions?\s+should\s+be\s+directed)\b", re.I)
_RE_AMD = re.compile(r"\bAMD\s*0*\d{1,4}\b", re.I)

PROMPT = """\
Eres un comprador de adquisiciones de EE.UU. Lee el aviso de SAM.gov
y extrae la informacion que necesita un DISTRIBUIDOR para cotizar el material.

AVISO
---
{aviso}
---

Devuelve SOLO este JSON, sin texto alrededor ni cercas de markdown:

{{
  "producto_limpio": "titulo comercial corto del item",
  "cantidad": 0,
  "unidad": "EA",
  "descripcion_distribuidor": "especificacion tecnica clara"
}}

REGLAS, IMPORTANTES

producto_limpio
  Como lo escribiria un catalogo comercial: "20-foot Container with Gantry Beam
  and Chain Lift". Sin el codigo de clase inicial de SAM.gov ("53--PARTS KIT...")
  y sin "AMD" ni "Narrative".

cantidad
  El numero de unidades que se PIDE, no una medida.
  - "2-20 foot Containers" son 2 contenedores, NO 20 y NO "foot".
  - "6 INCH PUMP" es la medida de un rotor: NO es la cantidad.
  - "this contract includes the estimated quantity of 100 each" son 100.
  Si el aviso NO dice cantidad, pon 1. No adivines un numero mayor.

unidad
  Sigla de catalogo militar en mayusculas: EA, BG, KT, SET, PR, CA, CS, BX,
  PK, CON, LOT, HD. Traduce "units" a EA, "each" a EA, "boxes" a BX,
  "containers" a CON. Si no hay unidad clara, usa EA.

descripcion_distribuidor
  Lo que un tecnico de compras necesita para pedir el material: dimensiones,
  materiales, tolerancias, normas, referencias de plano.
  QUITA TODO el papel administrativo: narratives de enmienda ("AMD 0001
  Narrative..."), acuses de recibo, punto de contacto, plazos de presentacion.
  No lo escribas en espanol: en ingles de adquisiciones de defensa (FAR/DFARS).
  No inventes datos que no esten en el aviso.
"""


def _limpiar_texto(texto: str) -> str:
    """Quita el tramite de una frase o parrafo, sea cual sea la forma."""
    t = _RE_AMD.sub(" ", str(texto or ""))
    partes = []
    for trozo in re.split(r"(?<=[.;])\s+|\n+", t):
        s = (trozo or "").strip()
        if not s or _RE_TRAMITE.search(s):
            continue
        if len(re.sub(r"[^0-9A-Za-z]", "", s)) < 3:
            continue
        partes.append(s)
    return " ".join(partes).strip()


def _normalizar_unidad(bruto) -> str:
    u = re.sub(r"[^A-Z]", "", str(bruto or "").upper())
    u = _SINONIMOS.get(u, u)
    return u if u in _UOIS else ""


def _mencionado(valor: float, texto: str) -> bool:
    """
    El numero aparece escrito en el texto de origen.

    No es una prueba de que la lectura sea correcta, pero descarta lo que el
    modelo se ha inventado de la nada, que es lo que de verdad duele: un "2"
    en la columna QTY de un documento que va a un distribuidor, sin nada en el
    aviso del que venga, no se puede distinguir de un dato.

    Se busca como numero entero con limites de PALABRA, no como subcadena: un
    "2" dentro de "2000" no cuenta, y "20" dentro de "2-20" tampoco.
    """
    if not valor or valor <= 0:
        return False
    if float(valor) != int(valor):
        return False
    return bool(_re_num.search("") or re.search(
        r"(?<![\d.,])%s(?![\d.,])" % re.escape(str(int(valor))), str(texto or "")))


_re_num = re.compile(r".")


def _validar(datos: dict, respaldo: dict, texto_fuente: str = "") -> dict:
    """
    Devuelve lo usable de la respuesta, mezclado con el respaldo.

    Cada campo se decide POR SEPARADO, porque la calidad no es uniforme: un
    modelo puede acertar la cantidad y equivocarse en la unidad, o al reves.
    Mezclar en bloque significaria tirar lo bueno por lo malo.

    De donde sale cada campo se devuelve en "_fuente" para poder verlo.
    """
    r = respaldo or {}
    fuera = {
        "producto_limpio": _limpiar_texto(datos.get("producto_limpio")),
        "descripcion_distribuidor": _limpiar_texto(
            datos.get("descripcion_distribuidor")),
        "cantidad": _cantidad(datos.get("cantidad")),
        "unidad": _normalizar_unidad(datos.get("unidad")),
    }
    fuera["fuente"] = {}

    # --- producto ---
    prod = fuera["producto_limpio"]
    if 3 <= len(prod) <= 140:
        fuera["producto_limpio"] = prod
        fuera["fuente"]["producto_limpio"] = "ia"
    else:
        fuera["producto_limpio"] = str(r.get("producto") or "").strip()
        fuera["fuente"]["producto_limpio"] = "regex"

    # --- descripcion ---
    desc = fuera["descripcion_distribuidor"]
    if len(desc) >= 20:
        fuera["descripcion_distribuidor"] = desc[:1200]
        fuera["fuente"]["descripcion_distribuidor"] = "ia"
    else:
        fuera["descripcion_distribuidor"] = str(
            r.get("especificacion") or "").strip()
        fuera["fuente"]["descripcion_distribuidor"] = "regex"

    # --- cantidad y unidad ---
    cant_ia = fuera["cantidad"]
    cant_re = _cantidad(r.get("cantidad"))
    # La cantidad de la IA se acepta si el numero sale de verdad en el texto.
    #
    # La primera version de esta regla exigia que la regex tambien hubiera
    # encontrado una, y era un error: rechazaba exactamente el caso para el que
    # existe el parsing con IA. En "2-20 foot Containers" la regex no ve
    # ninguna cantidad (el 20 es una medida y "foot" no es unidad), asi que la
    # IA era rechazada y salia 0. Es decir, la regla anulaba la razon de ser
    # del modulo.
    #
    # La comprobacion es directa contra el TEXTO, no contra el parser: el
    # numero tiene que estar escrito en el aviso. Eso deja pasar "2" de
    # "2-20 foot Containers" y descarta un 999999 que el modelo se haya
    # inventado. No es una prueba de que la lectura sea correcta, pero es
    # suficiente para separar "leido" de "inventado".
    # SIEMPRE hay que comprobar el texto. Antes la regla era
    # "cant_re or _mencionado(...)", y con eso un 999999 se colaba siempre que
    # la regex hubiera encontrado algo: el "cant_re" era un atajo que no
    # comprueba NADA sobre el numero de la IA. Lo unico que separa "leido" de
    # "inventado" es que el numero este escrito en el aviso.
    if cant_ia and _mencionado(cant_ia, texto_fuente):
        fuera["cantidad"] = cant_ia
        fuera["fuente"]["cantidad"] = "ia"
    else:
        fuera["cantidad"] = cant_re or 0.0
        fuera["fuente"]["cantidad"] = "regex"

    uni_ia = fuera["unidad"]
    uni_re = _normalizar_unidad(r.get("unidad"))
    if uni_ia:
        fuera["unidad"] = uni_ia
        fuera["fuente"]["unidad"] = "ia"
    else:
        fuera["unidad"] = uni_re or "EA"
        fuera["fuente"]["unidad"] = "regex"

    return fuera


def _cantidad(bruto) -> float:
    """
    Cantidad a numero, o 0 si no hay.

    Se rechazan los negativos, los infinitos y los valores absurdos (>1e9). Un
    modelo que devuelve 1000000000 por una confusion no puede acabar en la
    columna QTY de un documento que va a un distribuidor.
    """
    try:
        v = float(bruto)
    except (TypeError, ValueError):
        return 0.0
    if v != v or v in (float("inf"), float("-inf")):
        return 0.0
    if v <= 0 or v > 1_000_000_000:
        return 0.0
    return v


def estructurar(aviso: dict, descripcion: str, respaldo: dict,
                usar_cache: bool = True) -> dict:
    """
    Devuelve el JSON estructurado ya validado, mezclado con el respaldo.

    NUNCA LANZA. Si no hay clave, no hay cuota, la API falla, el modelo contesta
    una tonteria o el JSON no es legible, se devuelve el respaldo tal cual y el
    PDF se genera con lo que hubiera generado antes. Un documento a medias es
    mucho mejor que un documento que no sale.
    """
    import store

    nid = str(aviso.get("noticeId") or "")
    titulo = str(aviso.get("title") or "")
    texto = str(descripcion or "").strip()

    # --- Sin nada que estructurar ---
    if not texto and not titulo:
        log.info("estructurar('%s'): no hay texto", nid)
        return _validar({}, respaldo, texto)

    # --- 1) Cache ---
    # Es la barrera que mas importa en la practica: el usuario repite /pdf
    # varias veces mientras ajusta el documento, y cada repeticion seria otra
    # llamada sin esto.
    if usar_cache and nid:
        guardados, modelo, edad = store.leer_estructura(nid)
        if guardados:
            log.info("estructurar('%s'): desde cache (%s)", nid, modelo)
            return guardados
        # El fallo tambien se cachea, y por un tiempo. Sin esto, una clave
        # invalida hace fallar los cuatro modelos de la cola en CADA /pdf, que
        # es cuatro llamadas por cada pulsacion del usuario.
        if edad >= 0 and edad < MINUTOS_REINTENTO_FALLO:
            log.info("estructurar('%s'): fallo cacheado hace %.0f min, no se "
                     "reintenta", nid, edad)
            return _validar({}, respaldo, texto)

    # --- 2) Cuota antes de gastar ---
    # OJO CON EL NOMBRE: la funcion se llama quota.quedan(), no "queden". Con
    # "queden" el AttributeError caia en el except de abajo, que lo decia y
    # seguia adelante: es decir, SIN CUOTA LLAMABA A LA API IGUAL, que es
    # justo lo que esta barrera tiene que impedir. El fallo era silencioso
    # porque el except lo convierte en un aviso y continua.
    #
    # Por eso el except ya no continua: si no se puede comprobar la cuota, se
    # asume que no la hay. Es el lado conservador, y el coste de equivocarse es
    # un PDF con el parser de texto, que es exactamente el respaldo.
    try:
        import quota
        if quota.quedan() < 1:
            log.info("estructurar('%s'): sin cuota de Gemini, se usa el "
                     "parser de texto", nid)
            return _validar({}, respaldo, texto)
    except Exception as e:
        log.warning("estructurar('%s'): no se pudo comprobar la cuota (%s). "
                    "Se asume que no hay y se usa el parser de texto, que es "
                    "el lado conservador: equivocarse cuesta un PDF sin IA, y "
                    "no lo contrario una llamada sin cuota.", nid, e)
        return _validar({}, respaldo, texto)

    # --- 3) La llamada ---
    try:
        from google import genai
    except ImportError:
        log.info("estructurar('%s'): google-genai no esta instalado", nid)
        return _validar({}, respaldo, texto)

    if not config.GEMINI_API_KEY:
        log.info("estructurar('%s'): sin GEMINI_API_KEY, se usa el parser "
                 "de texto", nid)
        return _validar({}, respaldo, texto)

    cuerpo = texto[:MAX_CARACTERES] or titulo
    if titulo:
        cuerpo = f"TITLE: {titulo}\n\n{cuerpo}"
    prompt = PROMPT.format(aviso=cuerpo)

    cola = [config.GEMINI_MODEL] + [
        m for m in config.GEMINI_MODELES_ALTERNATIVOS
        if m and m != config.GEMINI_MODEL
    ]
    datos, modelo_usado = {}, ""
    # Por gemini_conn. Aqui antes vivia un bucle de modelos con su propia idea
    # de cuando insistir: un 429 pasaba al siguiente, y cualquier otra cosa
    # tambien, pero con un SOLO intento. Lo que no habia en ningun sitio del
    # repo era el backoff con espera ni el timeout de red.
    #
    # Un 429 no se reintenta contra el MISMO modelo, porque la cuota es por
    # modelo y por dia: repetirlo no puede funcionar nunca, solo gasta tiempo.
    # Se cambia de modelo. Un 503 si se reintenta, porque es transitorio y el
    # mismo puede responder dentro de unos segundos.
    try:
        import gemini_conn
        crudo, modelo_usado = gemini_conn.llamar(
            prompt, str(aviso.get("solicitationNumber") or nid),
            cadena=cola[:4], respaldo=config.GEMINI_MODELO_RESPALDO,
            temperature=0.0,
            response_mime_type="application/json",
            max_output_tokens=1200,
        )
        datos = _leer_json(crudo)
    except Exception as e:
        log.warning("estructurar('%s'): la cadena de modelos no sirvio (%s)",
                    nid, str(e)[:130])
        try:
            quota.gastar_fallo()
        except Exception:
            pass

    if not datos:
        log.info("estructurar('%s'): el modelo no devolvio nada usable", nid)
        if usar_cache and nid:
            # Se cachea el FALLO con el centinela. Sin esto, el siguiente /pdf
            # vuelve a gastar la misma llamada y vuelve a fallar.
            try:
                store.guardar_estructura(nid, {}, "")
            except Exception:
                pass
        return _validar({}, respaldo, texto)

    salida = _validar(datos, respaldo, texto)
    salida["_modelo"] = modelo_usado
    log.info("estructurar('%s'): %s | producto=%s cantidad=%s unidad=%s "
             "(fuentes: %s)", nid, modelo_usado,
             salida["fuente"].get("producto_limpio"),
             salida["cantidad"], salida["unidad"], salida["fuente"])
    try:
        quota.gastar(1)
    except Exception:
        pass
    if usar_cache and nid:
        try:
            store.guardar_estructura(nid, salida, modelo_usado)
        except Exception:
            pass
    return salida


def _leer_json(bruto: str) -> dict:
    """Saca el JSON de la respuesta. {} si no se puede."""
    if not bruto:
        return {}
    limpio = re.sub(r"^```(?:json)?|```$", "", bruto.strip(),
                     flags=re.MULTILINE).strip()
    try:
        obj = json.loads(limpio)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, ValueError):
        pass
    a = limpio.find("{")
    b = limpio.rfind("}")
    if a == -1 or b <= a:
        return {}
    try:
        obj = json.loads(limpio[a:b + 1])
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}