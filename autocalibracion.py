# -*- coding: utf-8 -*-
"""
autocalibracion.py - Modulo de autocalibracion y feedback autonomo (Fase 4).

CIERRA EL CICLO. Hoy un barrido imprime titulos, codigos y descartes en el
log, y ahi se quedan. Este modulo los empaqueta, se los manda a un SEGUNDO
pipeline de IA con un prompt de meta-analisis tecnico, y guarda lo que la IA
aprendio en un JSON que el motor de heuristica lee en el proximo ciclo.

TRES PIEZAS Y DOS LECTURAS:

    empaquetar_barrido(resumen)  -> el paquete de aprendizaje (fase 1)
    meta_analizar(paquete)       -> la llamada a la IA + JSON estricto (fase 2)
    aplicar(datos)               -> validacion + persistencia    (fase 3)

    config.excepciones_dinamicas()  -> dynamic_whitelist_tokens
    config.peso("NOMBRE_PESO")      -> adjusted_complexity_weights

LA REGLA DE ORO: LA IA APRENDE, NO DECIDE.

Un modelo remoto no tiene por que ser razonable, y el dia que no lo sea no
va a tumbar un barrido ni a cambiar el comportamiento del motor por debajo
de lo que este codigo permite. Por eso, antes de tocar nada:

  * Las claves del JSON son DOS y solo dos. Lo demas se ignora.
  * Los pesos solo pueden nombrar constantes de la LISTA BLANCA
    (PESOS_PERMITIDOS). Una clave inventada no existe y se descarta.
  * Cada peso se recorta a su rango y se devuelve ENTERO, igual que la
    constante estatica que reemplaza: el tipo del puntaje no cambia.
  * Los tokens se filtran por cantidad, longitud y alfabeto.
  * Un VETO (-100: defensa severa, NAICS, fecha) no es alcanzable desde
    aqui. La IA solo relaja CASTIGOS, que es donde viven los falsos
    positivos, nunca las barreras duras.
  * Nada de esto lanza jamas: si la calibracion falla, el motor cae en los
    estaticos de config, que siguen intactos y son el comportamiento actual.
  * Ninguna clave, ningun token y ningun dato de este modulo viajan a
    ningun sitio: solo se persiste lo que la IA propuso, nunca secretos.

EL FICHERO

    config.AUTOCALIBRACION_FICHERO

por defecto sigue el camino de DB_PATH (licitaciones.autocalibracion.json),
de modo que una prueba que redirige la base de datos redirige la
calibracion tambien. Es un JSON de una sola linea de estado, no una base
de datos: no hay nada que migrar y nada que limpiar.

LEER EN CADA CICLO

    autocalibracion.refrescar()

se llama al inicio de cada barrido (scanner.escanear). Entre ciclos, la
lectura se cachea por mtime, asi que cientos de puntuaciones cuestan un
stat() cada una y no cientos de aperturas.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone

import config

log = logging.getLogger("autocalibracion")

# --- Contrato del JSON de la IA -------------------------------------------
#
# Exactamente estas dos claves, y ninguna mas. Es el contrato que el prompt
# exige y que validar() hace cumplir del lado de Python.
CLAVE_TOKENS = "dynamic_whitelist_tokens"
CLAVE_PESOS = "adjusted_complexity_weights"

# --- Filtros de los tokens de bypass ---------------------------------------
MAX_TOKENS = 25          # tope de tokens aprendidos por ciclo
MIN_TOKEN = 3            # "kit" ya es util; "de" no lo es
MAX_TOKEN = 40           # un token largo deja de ser un token
# Minusculas, digitos, espacio, punto y guion. Nada de acentos ni simbolos:
# el texto con el que se compara tambien se normaliza asi.
_RE_TOKEN = re.compile(r"^[a-z0-9][a-z0-9 .\-]*$")

# --- Lista blanca de pesos ajustables --------------------------------------
#
# nombre -> (minimo, maximo). Los rangos CONTIENEN el valor estatico actual,
# para que un "sin cambios" sea siempre aceptable.
#
# Estas seis son la matriz de scoring entera menos los UMBRALES: el puntaje
# minimo (PUNTAJE_MINIMO, PUNTAJE_MINIMO_DEGRADADO) no esta aqui a proposito.
# El umbral es politica -que de tan estrictos somos- y el peso es
# calibracion -cuanto pesa cada senal-. Que la IA no pueda relajar el umbral
# es la mitad de la razon de existir de esta lista.
PESOS_PERMITIDOS: dict[str, tuple[float, float]] = {
    "PENALIZACION_MANTENIMIENTO": (-30, 0),
    "PENALIZACION_ARMAMENTO": (-30, 0),
    "PENALIZACION_URGENCIA": (-20, 0),
    # PENALIZACION_OBRA_PUNTOS es una magnitud POSITIVA que se resta.
    "PENALIZACION_OBRA_PUNTOS": (0, 10),
    "COTS_PUNTOS": (0, 10),
    "SET_ASIDE_PYME_PUNTOS": (0, 10),
}

# --- Cache de lectura -------------------------------------------------------
_CACHE = {"fichero": None, "mtime": None, "datos": {}}


# ==========================================================================
#  1. LECTURA (lo que usa el motor)
# ==========================================================================
def _leer(ruta: str) -> dict:
    """
    Lee y valida el fichero. {} si no existe, no es JSON, es de otra
    version o no pasa el filtro de validar().

    Nunca lanza: el motor prefiere los estaticos a una excepcion.
    """
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            crudo = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        log.debug("autocalibracion: fichero ilegible, se ignoran")
        return {}
    if not isinstance(crudo, dict):
        return {}
    if crudo.get("version") != config.AUTOCALIBRACION_VERSION:
        log.debug("autocalibracion: version distinta, se ignoran")
        return {}
    return validar(crudo)


def cargar(force: bool = False) -> dict:
    """
    La calibracion validada, cacheada por mtime.

    `force=True` salta la cache (es refrescar()). Si el fichero no existe
    se cachea "no existe" tambien, para no reintentar el stat en cada
    puntuacion.
    """
    try:
        ruta = config.AUTOCALIBRACION_FICHERO
    except Exception:
        return {}
    if not ruta:
        return {}
    try:
        mtime = os.stat(ruta).st_mtime_ns
    except OSError:
        mtime = None
    if not force and _CACHE["fichero"] == ruta and _CACHE["mtime"] == mtime:
        return _CACHE["datos"]
    datos = _leer(ruta) if mtime is not None else {}
    _CACHE["fichero"] = ruta
    _CACHE["mtime"] = mtime
    _CACHE["datos"] = datos
    return datos


def refrescar() -> dict:
    """
    Fuerza la relectura. Se llama AL INICIO DE CADA CICLO de barrido.

    Ademas de recoger el fichero nuevo, fija una foto unica para todo el
    ciclo: la primera puntuacion y la ultima del mismo barrido ven los
    mismos pesos, aunque alguien escriba a mitad de camino.
    """
    return cargar(force=True)


def peso_aprendido(nombre: str):
    """El peso aprendido para `nombre`, o None si no esta calibrado."""
    datos = cargar()
    if not datos:
        return None
    valor = (datos.get(CLAVE_PESOS) or {}).get(nombre)
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return valor


def tokens_bypass() -> set:
    """Los tokens de bypass aprendidos, en minusculas. Vacio si no hay."""
    datos = cargar()
    if not datos:
        return set()
    return set(datos.get(CLAVE_TOKENS) or [])


# ==========================================================================
#  2. VALIDACION - el filtro que hace inocua cualquier respuesta
# ==========================================================================
def _validar_tokens(valor) -> list | None:
    """
    None si la CLAVE esta mal (no es lista). Una lista filtrada si lo esta.

    Un token individual malo se descarta y el resto sigue: el numero de
    tokens no es lo que rompe el motor, la calidad de cada uno. Se acepta
    una lista vacia, que significa "no hay nada que relajar".
    """
    if not isinstance(valor, (list, tuple)):
        return None
    salida: list[str] = []
    vistos: set[str] = set()
    for item in valor:
        if not isinstance(item, str):
            continue
        token = item.strip().lower()
        if not (MIN_TOKEN <= len(token) <= MAX_TOKEN):
            continue
        if not _RE_TOKEN.match(token):
            continue
        if token in vistos:
            continue
        vistos.add(token)
        salida.append(token)
        if len(salida) >= MAX_TOKENS:
            break
    return salida


def _validar_pesos(valor) -> dict | None:
    """
    None si la CLAVE esta mal (no es objeto). Un dict filtrado si lo esta.

    Tres filtros en cadena, y el orden importa:
      1. nombre fuera de la lista blanca -> no existe, se ignora;
      2. no numerico (o bool, que en Python es int) -> se ignora;
      3. fuera de rango -> se RECORTA al rango, no se descarta: si la IA
         pidio -120 para mantenimiento, quiere decir "mas duro", y -30 ya
         es el techo de lo que este motor admite.
    El resultado siempre es int, igual que la constante que sustituye.
    """
    if not isinstance(valor, dict):
        return None
    salida: dict[str, int] = {}
    for nombre, bruto in valor.items():
        if nombre not in PESOS_PERMITIDOS:
            continue
        if isinstance(bruto, bool) or not isinstance(bruto, (int, float)):
            continue
        v = float(bruto)
        if v != v or v in (float("inf"), float("-inf")):
            continue
        lo, hi = PESOS_PERMITIDOS[nombre]
        salida[nombre] = int(round(max(lo, min(hi, v))))
    return salida


def validar(datos) -> dict:
    """
    Pasa la respuesta de la IA por el filtro y devuelve un registro limpio
    (o {} si la respuesta no es usable).

    Las DOS claves son obligatorias: si falta una, no hay contrato que
    cumplir y se ignora todo. Las claves extra del JSON se ignoran sin
    castigo, porque el prompt puede acumular ruido y lo importante es lo
    que el motor va a leer.
    """
    if not isinstance(datos, dict):
        return {}
    tokens = _validar_tokens(datos.get(CLAVE_TOKENS))
    pesos = _validar_pesos(datos.get(CLAVE_PESOS))
    if tokens is None or pesos is None:
        return {}
    limpio = {
        "version": config.AUTOCALIBRACION_VERSION,
        CLAVE_TOKENS: tokens,
        CLAVE_PESOS: pesos,
    }
    # Metadata opcional: se conserva si es del tipo esperado y si no, no.
    if isinstance(datos.get("actualizado"), str):
        limpio["actualizado"] = datos["actualizado"][:40]
    ciclos = datos.get("ciclos")
    if isinstance(ciclos, int) and not isinstance(ciclos, bool) and 0 <= ciclos < 1000000:
        limpio["ciclos"] = ciclos
    return limpio


# ==========================================================================
#  3. PAQUETE - lo que se le manda al pipeline secundario
# ==========================================================================
def _entero(valor, defecto: int = 0) -> int:
    if isinstance(valor, bool):
        return defecto
    try:
        return int(valor)
    except (TypeError, ValueError):
        return defecto


def _texto(valor, tope: int) -> str:
    if not isinstance(valor, str):
        return ""
    return valor.strip()[:tope]


def empaquetar_barrido(resumen: dict) -> dict:
    """
    El paquete de aprendizaje de un barrido.

    Tres bloques, y cada uno responde a una pregunta distinta del prompt:

      avisos     - que puntuo el motor y POR QUE. Titulos, PSC, NAICS,
                   puntaje y motivos. Es la materia prima para distinguir
                   un veto justo de un castigo por palabra ambigua.
      descartes  - que se cayo. Con el mismo detalle mas el motivo, que es
                   donde estan los falsos positivos que hay que encontrar.
      cola       - que se quedo a medias. Los avisos puntuados que nunca
                   llegaron a analizarse (cuota, descripcion pendiente) y
                   los errores: el motor no puede aprender de lo que no
                   vio, y esto lo dice en voz alta.

    Todo viene del `resumen` que devuelve scanner.escanear: este modulo no
    lee el scanner ni la base de datos, solo el diccionario que le pasan.
    Asi el paquete se puede construir en una prueba con un resumen de
    mentira, sin SAM.gov ni Gemini.
    """
    resumen = resumen if isinstance(resumen, dict) else {}

    avisos = []
    for d in (resumen.get("detalle_puntajes") or []):
        if not isinstance(d, dict):
            continue
        avisos.append({
            "titulo": _texto(d.get("titulo"), 120),
            "psc": _texto(d.get("psc"), 10),
            "naics": _texto(d.get("naics"), 12),
            "puntaje": _entero(d.get("puntaje")),
            "motivos": [_texto(m, 90)
                        for m in (d.get("motivos") or [])[:4]
                        if isinstance(m, str)],
        })

    descartes = []
    for d in (resumen.get("descartes") or []):
        if not isinstance(d, dict):
            continue
        descartes.append({
            "titulo": _texto(d.get("titulo"), 120),
            "psc": _texto(d.get("psc"), 10),
            "naics": _texto(d.get("naics"), 12),
            "motivo": _texto(d.get("motivo"), 90),
        })

    puntuales = _entero(resumen.get("puntuales"))
    analizadas = _entero(resumen.get("analizadas"))
    errores = [_texto(e, 160)
               for e in (resumen.get("errores") or [])[:10]
               if isinstance(e, str)]

    conteo_descartes = {}
    for motivo, n in (resumen.get("descarte_motivo") or {}).items():
        if isinstance(motivo, str) and not isinstance(n, bool):
            try:
                conteo_descartes[motivo[:60]] = int(n)
            except (TypeError, ValueError):
                continue

    return {
        "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "conteo": {
            "traidas": _entero(resumen.get("traidas")),
            "candidatas": _entero(resumen.get("candidatas")),
            "puntuales": puntuales,
            "analizadas": analizadas,
            "viables": _entero(resumen.get("viables")),
        },
        "avisos": avisos,
        "descartes": descartes,
        "conteo_descartes": conteo_descartes,
        "cola": {
            # Puntuadas pero nunca analizadas: se quedaron en la cola.
            "sin_analizar": max(0, puntuales - analizadas),
            "analizadas": analizadas,
            "errores": errores,
        },
    }


def _util(paquete: dict) -> bool:
    """False si el paquete no tiene nada que analizar."""
    return bool(paquete.get("avisos") or paquete.get("descartes"))


# ==========================================================================
#  4. EL PIPELINE SECUNDARIO
# ==========================================================================
#
# Un cliente aparte del de gemini_analyzer a proposito: este es un canal
# secundario, y si el primario cambia de firma o de version este no se
# entera. Ademas el paquete no contiene ninguna clave ni ningun aviso
# confidencial, solo titulos, codigos y motivos publicos de SAM.gov.
_CLIENTE = None

PROMPT_META = """Eres un auditor tecnico de un motor de heuristica de compra federal (SAM.gov).
Esto es un META-ANALISIS de un BARRIDO COMPLETO, no de un aviso suelto.

EL PROBLEMA QUE TIENES QUE RESOLVER
El motor puntua avisos por palabras clave, y cuando se equivoca casi siempre
es en la misma direccion: penaliza de mas. El caso tipico: "REMAN" y
"MODIFICATION" son palabras de CATALOGO -un alternador remanufacturado o un
kit de modificacion se compran y se venden en caja- y el mismo patron que
hunde un contrato de mantenimiento en sitio les resta 20 puntos. Igual con
"aircraft coatings", que es pintura que se vende por bote. Esas devoluciones,
las de articulos COTS legitimos cuya clasificacion (PSC) y su NAICS dicen que
son producto, son las que tienes que encontrar.

LOS DATOS DEL BARRIDO
%s

TU TAREA, EN ESTE ORDEN
1. LOCALIZA LOS FALSOS POSITIVOS: avisos descartados o mal puntuados que por
   su titulo, su PSC y su NAICS parezcan material COTS de catalogo y hayan
   caido por una palabra ambigua (reman, modification, overhaul, maintenance,
   refurbish, install, labor, service...). Cita el titulo que te llevo ahi.
   Y busca PATRONES REPETITIVOS: el mismo motivo cayendo una y otra vez con
   titulos parecidos es la senal mas fuerte de un ajuste. El campo
   "conteo_descartes" trae esas repeticiones ya contadas.
2. DERIVA LOS TOKENS: de esos casos concretos saca TERMINOS cortos que, si
   aparecen en un titulo, marquen el aviso como articulo de catalogo y
   relajen la penalizacion por patron. No inventes terminos que no se
   deduzcan de los datos de arriba. Maximo 25. OJO: la penalizacion de
   mantenimiento ya se salta repair, maintenance, overhaul y refurbish por
   doble; lo que la hunde es reman, modification y armament/aeroespacial.
3. CALIBRA LOS PESOS: si la mayoria de los descartes viene de una sola causa,
   mueve SOLO ese peso. Si el reparto esta parecido o no hay senales,
   NO MOVAS NADA: el valor por defecto es mejor que un ajuste inventado.
   Ajusta como maximo tres pesos.

REGLAS DE HIERRO
- Las claves obligatorias son EXACTAMENTE DOS:
    dynamic_whitelist_tokens      (lista de strings)
    adjusted_complexity_weights   (objeto)
- Los UNICOS nombres de peso que existen son, con su rango admitido:
%s
  Un nombre fuera de esa lista no existe: se descarta. No inventes claves.
- Cada peso es un ENTERO dentro de su rango.
- NO puedes relajar un veto: los vetos son -100 y no estan en la lista.
  Tampoco puedes tocar el puntaje minimo ni nada que no aparezca arriba.
- Tokens: minusculas, de 3 a 40 caracteres, solo letras, digitos, espacio,
  punto y guion.

Devuelve UN SOLO objeto JSON, sin texto antes ni despues, sin fences de
markdown y sin comentarios. Forma exacta:
{"dynamic_whitelist_tokens": ["parts kit"], "adjusted_complexity_weights": {"PENALIZACION_MANTENIMIENTO": -15}}
"""


def _prompt(paquete: dict) -> str:
    """
    Arma el prompt. Se concatenan los %s a mano y no con format(): el JSON
    de ejemplo lleva llaves, y con format() habria que escaparlas todas y
    la minima errata deja el prompt ilegible para el modelo.
    """
    pesos = "\n".join(
        "    %s entre %d y %d" % (n, lo, hi)
        for n, (lo, hi) in sorted(PESOS_PERMITIDOS.items())
    )
    return PROMPT_META % (json.dumps(paquete, ensure_ascii=False, indent=2),
                          pesos)


def _extraer_json(texto: str) -> dict:
    """El objeto JSON de la respuesta, con o sin fence alrededor."""
    t = (texto or "").strip()
    if not t:
        return {}
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    ini, fin = t.find("{"), t.rfind("}")
    if ini < 0 or fin <= ini:
        return {}
    try:
        dato = json.loads(t[ini:fin + 1])
    except Exception:
        return {}
    return dato if isinstance(dato, dict) else {}


def _get_cliente():
    """Cliente propio, cacheado. Devuelve None si no se puede crear."""
    global _CLIENTE
    if _CLIENTE is not None:
        return _CLIENTE
    if not config.GEMINI_API_KEY:
        return None
    try:
        from google import genai
        _CLIENTE = genai.Client(api_key=config.GEMINI_API_KEY)
    except Exception as e:
        log.debug("autocalibracion: sin cliente de Gemini: %s", e)
        return None
    return _CLIENTE


def meta_analizar(paquete: dict) -> dict:
    """
    Fase 2: manda el paquete al pipeline secundario y devuelve el objeto
    JSON crudo de la IA (sin validar, eso es de aplicar()).

    Devuelve {} si no hay clave, no hay cliente, ningun modelo responde o
    la respuesta no es JSON. Nunca lanza: la falla de este canal no puede
    costarle un barrido al usuario.
    """
    cliente = _get_cliente()
    if cliente is None:
        log.debug("autocalibracion: sin cliente, se salta el meta-analisis")
        return {}
    try:
        from google.genai import types
    except Exception as e:
        log.debug("autocalibracion: google.genai sin tipos: %s", e)
        return {}

    prompt = _prompt(paquete)
    # Cadena de modelos igual que la del analizador principal: si el
    # primario esta caido o sin cuota, el meta-analisis no es lo que
    # tiene que frenar el ciclo.
    cola = [config.GEMINI_MODEL] + [
        m for m in config.GEMINI_MODELES_ALTERNATIVOS
        if m and m != config.GEMINI_MODEL
    ]
    for modelo in cola:
        try:
            respuesta = cliente.models.generate_content(
                model=modelo,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    # Clave del contrato: la respuesta sale como JSON puro,
                    # sin prosa que tengamos que recortar a mano.
                    response_mime_type="application/json",
                ),
            )
            crudo = getattr(respuesta, "text", None) or ""
            datos = _extraer_json(crudo)
            if datos:
                return datos
            log.debug("autocalibracion: %s devolvio vacio", modelo)
        except Exception as e:
            log.debug("autocalibracion: fallo en %s: %s", modelo, e)
    return {}


# ==========================================================================
#  5. PERSISTENCIA
# ==========================================================================
def aplicar(datos: dict) -> dict:
    """
    Fase 3: valida, decide si hay cambio y persiste. Nunca lanza.

    La respuesta de la IA es AUTORITATIVA para las dos claves: si devuelve
    pesos vacios, es la IA diciendo "no ajustes nada", y el estado anterior
    se limpia en vez de quedarse congelado. Se reemplaza, no se acumula,
    para que el estado no crezca ni se quede pegado a un ajuste que ya no
    se sostiene.

    Escritura atomica (temporal + os.replace): un corte a mitad de escritura
    deja el fichero anterior, nunca uno a medias.
    """
    if not config.AUTOCALIBRACION_HABILITADA:
        return {"ok": False, "motivo": "autocalibracion deshabilitada"}
    limpio = validar(datos)
    if not limpio:
        return {"ok": False, "motivo": "payload rechazado por validacion"}

    previo = cargar()
    if (previo.get(CLAVE_TOKENS) == limpio[CLAVE_TOKENS]
            and previo.get(CLAVE_PESOS) == limpio[CLAVE_PESOS]):
        return {"ok": True, "sin_cambios": True,
                "tokens": len(limpio[CLAVE_TOKENS]),
                "pesos": len(limpio[CLAVE_PESOS])}

    registro = {
        "version": config.AUTOCALIBRACION_VERSION,
        "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ciclos": _entero(previo.get("ciclos")) + 1,
        CLAVE_TOKENS: limpio[CLAVE_TOKENS],
        CLAVE_PESOS: limpio[CLAVE_PESOS],
    }
    ruta = config.AUTOCALIBRACION_FICHERO
    carpeta = os.path.dirname(ruta)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    temporal = ruta + ".tmp"
    with open(temporal, "w", encoding="utf-8") as f:
        json.dump(registro, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(temporal, ruta)
    cargar(force=True)   # deja la cache al dia con lo que acaba de salir
    log.info("autocalibracion: %d token(s) y %d peso(s) persistidos",
             len(registro[CLAVE_TOKENS]), len(registro[CLAVE_PESOS]))
    return {"ok": True, "sin_cambios": False,
            "tokens": len(registro[CLAVE_TOKENS]),
            "pesos": len(registro[CLAVE_PESOS])}


# ==========================================================================
#  6. LA TAREA ASINCRONA POST-BARRIDO
# ==========================================================================
async def ciclo_post_barrido(resumen: dict) -> dict:
    """
    La tarea que corre DESPUES de cada barrido, sin bloquearlo.

    Se lanza desde main.py con asyncio.create_task y hace las tres fases en
    hilos (asyncio.to_thread), porque tanto la llamada a la IA como el
    fichero son bloqueantes y este codigo vive en el event loop de
    Telegram: si se hiciera en linea, el usuario esperaria el meta-analisis
    para ver su escaneo.

    Devuelve siempre un dict con "ok" y jamas lanza: una excepcion aqui se
    convierte en un resultado, no en una tarea muerta que Python se traga
    en silencio.
    """
    try:
        paquete = empaquetar_barrido(resumen)
        if not _util(paquete):
            return {"ok": False, "motivo": "paquete sin datos suficientes"}
        if not config.GEMINI_API_KEY:
            return {"ok": False, "motivo": "sin GEMINI_API_KEY"}
        if not config.AUTOCALIBRACION_HABILITADA:
            return {"ok": False, "motivo": "autocalibracion deshabilitada"}
        datos = await asyncio.to_thread(meta_analizar, paquete)
        if not datos:
            return {"ok": False, "motivo": "la IA no devolvio JSON valido"}
        return await asyncio.to_thread(aplicar, datos)
    except Exception as e:
        # Ultima muralla: lo importante es que el barrido no se entere.
        log.warning("autocalibracion: ciclo interrumpido: %s", e)
        return {"ok": False, "motivo": str(e)[:200]}
