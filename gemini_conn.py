"""
gemini_conn.py - El unico sitio por el que se habla con Gemini.

POR QUE ESTE MODULO EXISTE

Habia cinco llamadas a generate_content repartidas por cinco ficheros, cada una
con su propia idea de reintentar: una hacia backoff 2/4/8 para SAM.gov (que no
es lo mismo), otra dormia 2**intento, otra no dormia nada. Y NINGUNA tenia un
timeout de red estricto, que es lo que en un contenedor de Render produce un
hilo colgado: la peticion espera, la espera no es de red, y el hilo se queda
tomado sin que nadie sepa por que.

Aqui hay una sola respuesta a las tres preguntas que se REPITEN en cada sitio:
cuanto se reintenta, con que espera, y a quien se cae cuando ya no hay mas
reintentos que probar.

REGLAS, Y POR QUE SON ESTAS Y NO OTRAS

503 Y 429 NO SON LO MISMO, Y CONFUNDIRLOS ES UN ERROR CARO.

    503 Service Unavailable -> Saturacion transitoria. El mismo modelo puede
        funcionar en 30 segundos. Aqui SI ayuda reintentar, y con espera
        creciente para no repetir la estampida que lo provoco.

    429 Resource Exhausted -> Cuota agotada PARA ESE MODELO Y ESE DIA. Reintentar
        el mismo modelo no puede funcionar nunca, solo gasta los segundos de
        otra persona y los tuyos. Asi que un 429 NO se reintenta: se cambia de
        modelo de inmediato.

Confundirlos produce el fallo clasico: tres reintentos contra un modelo sin
cuota, 2+4+8 = 14 segundos de espera, y luego el mismo 429. Catorce segundos por
aviso, en un barrido que va por la lista de Telegram.

LA ESPERA

2s, 4s, 8s. Es la escala del encargo y coincide con la que ya usa SAM.gov, que
esta medida. Ojo con el coste agregado: 14 segundos POR AVISO bloqueando el
hilo. En /pdf es aceptable (un usuario esperando), en el barrido se suma. Por
eso la espera es inyectable, para que un test no tarde 14 segundos reales.

EL MODELO DE RESPALDO

Es configurable con GEMINI_MODELO_RESPALDO, y el valor por defecto NO es
gemini-1.5-flash.

El encargo pedia 1.5-flash. Ese nombre no esta en la lista de modelos que este
repo ha medido que responden (config.GEMINI_MODELES_ALTERNATIVOS, con anotaciones
del 28 y 29-sep-2026), y la propia configuracion deja escrito que hay nombres de
modelo que NO EXISTEN para esta cuenta. Un respaldo que devuelve 404 es peor que
no tener ninguno, porque el dia que hace falta no esta, y ademas ocupa el sitio
del primer reintento que si habria funcionado.

Asi que el mecanismo es exactamente el pedido, y el modelo se decide por
configuracion. Si de verdad quieres 1.5-flash, se pone en la variable de
entorno y listo; pero conviene medirlo antes con sondear_modelos.py, que es lo
que hay que hacer con cualquier nombre de modelo nuevo.

FORMATO DE LOS LOGS

Se escribe la marca de tiempo DENTRO del mensaje, no se deja al handler de
logging. Asi el formato pedido se cumple este donde este el log, en vez de
depender de una configuracion que otro dia alguien cambia y nadie comprueba.
"""
import logging
import os
import time

import config

log = logging.getLogger("kyomoto.gemini_conn")

# El nombre del modelo de respaldo sale de config, que lo lee del entorno.
# Ver la cabecera del modulo antes de cambiarlo.
MODELO_RESPALDO = str(getattr(config, "GEMINI_MODELO_RESPALDO", "") or
                      "gemini-flash-latest")

MAX_INTENTOS = 3           # El encargo: tres.
ESPERA_BASE = 2.0          # El encargo: 2s -> 4s -> 8s.
TIMEOUT_MS = 15_000        # El encargo: 15 segundos por peticion.


def _marca() -> str:
    return time.strftime("[%Y-%m-%d %H:%M:%S]")


# ------------------------------------------------------------------ CLASIFICAR
def clasificar(exc: BaseException) -> str:
    """
    Que clase de fallo es. Devuelve una de:

        "503"  saturacion transitoria, se reintenta
        "429"  cuota agotada, NO se reintenta, se cambia de modelo
        "404"  el modelo no existe para esta cuenta, se salta
        "400"  peticion invalida, reintentar es perder el tiempo
        "red"  timeout o conexion, se reintenta
        "otro" cualquier otra cosa

    Se mira primero el TIPO de la excepcion y despues el texto, porque Google
    cambia los mensajes entre versiones y los tipos se mantienen. Un
    ServiceUnavailable dice 503 en el nombre y en el texto; un
    RESOURCE_EXHAUSTED dice 429 en el texto y a veces no en el tipo.
    """
    nombre = type(exc).__name__.lower()
    texto = str(exc)

    # El 503 primero: ServiceUnavailable y Unavailable son el mismo fallo, y
    # "unavailable" aparece tambien dentro de otros nombres.
    if "serviceunavailable" in nombre or "unavailable" in nombre:
        return "503"
    if "resourceexhausted" in nombre:
        return "429"
    if "notfound" in nombre or "404" in texto:
        return "404"
    if "deadline" in nombre or "timeout" in nombre or "timeout" in texto.lower():
        return "red"
    if "429" in texto or "RESOURCE_EXHAUSTED" in texto or "quota" in texto.lower():
        return "429"
    if "503" in texto or "Service Unavailable" in texto:
        return "503"
    if "400" in texto or "INVALID_ARGUMENT" in texto:
        return "400"
    if ("connection" in nombre or "connection" in texto.lower()
            or "temporarily" in texto.lower()):
        return "red"
    return "otro"


# ------------------------------------------------------------------ LOGS
def _log_reintento(contrato: str, intento: int, espera: float) -> None:
    log.warning(
        "%s [WARNING] Error 503 detectado en Solicitud [%s]. Aplicando "
        "backoff exponencial. Intento %s/%s.", _marca(), contrato, intento,
        MAX_INTENTOS)


def _log_respaldo(contrato: str, modelo: str) -> None:
    log.info(
        "%s [INFO] Reintentos agotados para serie 3.x en Licitacion [%s]. "
        "Transicionando a modelo de respaldo estable (%s) para asegurar "
        "continuidad del barrido.", _marca(), contrato, modelo)


def _log_fin(contrato: str, modelo: str, clase: str) -> None:
    log.info("%s [INFO] Solicitud [%s] respondida por [%s] via %s.",
             _marca(), contrato, modelo, clase)


# ------------------------------------------------------------------ CLIENTE
def _cliente(genai, types, timeout_ms: int):
    """
    El cliente, con timeout de red ESTRICTO.

    Va en HttpOptions del cliente y no en el GenerateContentConfig de la
    llamada, porque es una opcion de transporte y no del modelo. Sin esto, una
    peticion puede quedarse esperando en un servidor de Render sin que nada la
    corte, y el hilo queda tomado.
    """
    try:
        http = types.HttpOptions(timeout=int(timeout_ms))
        return genai.Client(api_key=config.GEMINI_API_KEY, http_options=http)
    except Exception:
        # Si esta version del SDK no acepta http_options, se sigue sin el. Un
        # cliente sin timeout es peor que nada, pero romper la llamada entera
        # seria peor todavia.
        return genai.Client(api_key=config.GEMINI_API_KEY)


def _llamar_una_vez(genai, types, modelo: str, prompt: str,
                    timeout_ms: int, **cfg):
    """Una peticion. No reintenta, no captura:propaga el fallo."""
    cliente = _cliente(genai, types, timeout_ms)
    respuesta = cliente.models.generate_content(
        model=modelo,
        contents=prompt,
        config=types.GenerateContentConfig(**cfg),
    )
    return str(getattr(respuesta, "text", "") or "")


# ------------------------------------------------------------------ LLAMADA
def llamar(prompt: str, contrato: str = "", *,
           modelo: str = "", respaldo: str = "", cadena=None,
           max_intentos: int = MAX_INTENTOS,
           espera_base: float = ESPERA_BASE,
           timeout_ms: int = TIMEOUT_MS,
           dormir=None, **cfg) -> tuple[str, str]:
    """
    Llama a un modelo con reintentos, y cae al de respaldo si hace falta.

    Devuelve (texto, modelo_que_respondio). Lanza la ultima excepcion si no hay
    NI el modelo principal NI el de respaldo.

    503 y "red" se reintentan con 2/4/8, porque son transitorios y el mismo
    modelo puede responder un momento despues.

    429, 400 y 404 NO se reintentan. Un 429 es cuota de ese modelo para ese dia:
    repetirlo no puede funcionar. Un 404 es que el modelo no existe. Un 400 es
    que la peticion esta mal y da igual cuantas veces se mande.

    En los tres casos se va al de respaldo de inmediato, que es lo que evita
    perder catorce segundos por aviso.
    """
    dormir = dormir or time.sleep
    principal = modelo or config.GEMINI_MODEL
    suplente = respaldo or MODELO_RESPALDO
    contrato = contrato or "sin-numero"

    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        log.warning("%s [WARNING] Solicitud [%s]: el SDK de Gemini no esta "
                    "instalado (%s).", _marca(), contrato, e)
        raise

    # Si el modelo de respaldo coincide con el principal, no hay cadena: se
    # reintentaria el mismo modelo dos veces y se Sumarian las esperas.
    if suplente == principal:
        suplente = ""

    # La cola. "cadena" es la lista completa de modelos conocidos que se prueban
    # en orden; si no se pasa, son dos: el principal y el de respaldo. La cadena
    # se mantiene porque ya estaba, y porque un 429 es cuota POR MODELO: si el
    # principal se ha quedado sin, hay mas donde mirar antes de rendirse.
    #
    # El de respaldo va SIEMPRE el ultimo, y es el unico al que se le llama
    # "respaldo" en el log, para que el mensaje del encargo signifique algo.
    cola = [m for m in (cadena or [principal]) if m]
    if suplente and suplente not in cola:
        cola.append(suplente)
    if not suplente and len(cola) == 1 and cola[0] != principal:
        cola.insert(0, principal)

    ultima = None
    for indice, modelo_prueba in enumerate(cola):
        etapa = "principal" if modelo_prueba == principal else "cadena"
        if etapa == "cadena" and modelo_prueba == suplente:
            etapa = "respaldo"
            _log_respaldo(contrato, modelo_prueba)
        elif etapa == "cadena":
            log.info("%s [INFO] Solicitud [%s]: [%s] no sirve, se prueba [%s].",
                     _marca(), contrato, cola[indice - 1], modelo_prueba)
        reintentar = True
        for intento in range(1, max_intentos + 1):
            try:
                texto = _llamar_una_vez(genai, types, modelo_prueba, prompt,
                                        timeout_ms, **cfg)
                if texto.strip():
                    _log_fin(contrato, modelo_prueba, etapa)
                    return texto, modelo_prueba
                ultima = RuntimeError(
                    "Solicitud [%s]: %s devolvio una respuesta vacia"
                    % (contrato, modelo_prueba))
            except Exception as e:
                ultima = e
                clase = clasificar(e)
                # Cuota, peticion invalida o modelo inexistente: insistir es
                # perder el tiempo. Se registra y se pasa al respaldo.
                if clase in ("429", "400", "404"):
                    log.warning(
                        "%s [WARNING] Solicitud [%s]: %s en [%s] (intento "
                        "%s/%s). No se reintenta un %s.", _marca(), contrato,
                        clase, modelo_prueba, intento, max_intentos, clase)
                    break
                if not reintentar:
                    break
                if intento >= max_intentos:
                    log.warning(
                        "%s [WARNING] Solicitud [%s]: %s persiste en [%s] tras "
                        "%s intentos.", _marca(), contrato, clase,
                        modelo_prueba, max_intentos)
                    break
                espera = espera_base * (2 ** (intento - 1))
                _log_reintento(contrato, intento, espera)
                try:
                    dormir(espera)
                except Exception:
                    pass

    if ultima is not None:
        raise ultima
    raise RuntimeError(
        "Solicitud [%s]: no hay modelo disponible. Se probaron: %s"
        % (contrato, ", ".join(cola) or "(ninguno)"))


def limpiar_cache(clave: str) -> None:
    """
    Quita el centinela de fallo de la cache para esta clave.

    Se llama cuando la llamadaha tenido exito. Sin esto, un fallo anterior
    quedaarchivado y el siguiente /pdf lee "fallo cacheado" y se rinde sin
    volver a preguntar, aunque la API este bien.
    """
    if not clave:
        return
    try:
        import store
        store.guardar_estructura(clave, {}, "")
        log.info("%s [INFO] Cache de fallo limpiada para [%s].",
                 _marca(), clave[:40])
    except Exception as e:
        log.warning("%s [WARNING] No se pudo limpiar la cache de [%s]: %s",
                    _marca(), clave[:40], e)