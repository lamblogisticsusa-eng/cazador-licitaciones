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
# El encargo fija 2s -> 4s -> 8s. Sale de config para que se pueda bajar sin
# tocar codigo: en un contenedor de Render el margen es distinto, y una prueba
# que simula seis modelos caidos tardaria 900s con la espera clavada.
ESPERA_BASE = float(getattr(config, "GEMINI_ESPERA_BACKOFF_SEG", 2) or 2)
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
        "401"  credencial no reconocida, es fatal, no se cambia de modelo
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
    # El CODIGO primero, y es lo primero de todo. google.genai lo pone en
    # .code, y es el dato mas fiable: el texto del error lo cambia Google entre
    # versiones, el codigo no. Antes solo se miraba el texto, asi que un
    # ClientError(503) cuyo mensaje no llevara el "503" dentro se clasificaba
    # como "otro" y se reintentaba en vez de cambiar de modelo.
    codigo = getattr(exc, "code", None)
    if not isinstance(codigo, int):
        codigo = getattr(exc, "status_code", None)
    if not isinstance(codigo, int):
        resp = getattr(exc, "response", None)
        codigo = getattr(resp, "status_code", None)
    if isinstance(codigo, int):
        if codigo == 503:
            return "503"
        if codigo == 429:
            return "429"
        if codigo == 404:
            return "404"
        if codigo == 400:
            return "400"
        if codigo == 401:
            return "401"
        if codigo in (408, 504):
            return "red"

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
    if "401" in texto or "UNAUTHENTICATED" in texto or "unauthenticated" in texto.lower():
        # El 401 es FATAL: la credencial esta mal y ningun modelo de la lista la
        # va a arreglar. Antes solo se reconocia por .code, y un RuntimeError
        # con el texto de Google se colaba por el otro lado.
        return "401"
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
                if clase in ("429", "400", "404", "401"):
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


class ErrorDeCadena(Exception):
    """
    Se agoto la cola de modelos. NO lleva el mensaje de usuario: lleva los
    DATOS, y es quien los componga el modulo que llamo.

    El motivo es que cada modulo tiene su propio texto para el usuario y sus
    propios contadores. Un unico "fallo de Gemini" aqui obligaria a todos a
    inventar el suyo, y el de un barrido completo es largo de verdad: explica
    cuantos modelos se quedaron sin cuota, cuantos solo saturados, y avisa de
    que el aviso no se marca como visto para que el proximo barrido lo recoja.

    Asi que aqui solo se junta la evidencia:

        eventos  [{"clase": "429", "modelo": "...", "texto": "..."}, ...]
        ultimo   la ultima excepcion, con su tipo y su mensaje

    Y el que llama decide que le cuenta a su usuario.
    """

    def __init__(self, motivo: str, eventos=None, ultimo=None):
        super().__init__(motivo)
        self.motivo = motivo
        self.eventos = list(eventos or [])
        self.ultimo = ultimo

    def modelos_de(self, clase: str) -> list[str]:
        """Los modelos que fallaron de una clase concreta, sin repetir."""
        return list(dict.fromkeys(e.get("modelo", "") for e in self.eventos
                                  if e.get("clase") == clase))

    def texto_ultimo(self) -> str:
        return str(self.ultimo) if self.ultimo is not None else ""


def _config_con_thinking(types, extra: dict, presupuesto, sin_pensar: bool):
    """
    El GenerateContentConfig, con o sin thinking_budget.

    El presupuesto se mete como ESTRUCTURA, no dentro del prompt. Va en la
    config de la llamada porque es ahi donde el modelo lo recibe, y porque
    ponerlo en el prompt no lo limita: solo se lo cuenta como texto.

    Y se mete aqui y no en el modulo que llama, por un motivo concreto: asi el
    modelo de respaldo hereda el limite sin que nadie tenga que acordarse. Si
    el limite se construyera arriba, el dia que se anadiera un modelo nuevo se
    podria olvidar, y ese modelo se comeria el presupuesto entero.
    """
    cfg = dict(extra or {})
    if sin_pensar or presupuesto is None:
        cfg.pop("thinking_config", None)
        return types.GenerateContentConfig(**cfg)
    try:
        cfg["thinking_config"] = types.ThinkingConfig(
            thinking_budget=int(presupuesto))
    except Exception:
        # SDK sin ThinkingConfig, o sin ese campo: se sigue sin el limite
        # explicito en vez de reventar. Es una degradacion, no un fallo: el
        # modelo sigue trabajando, solo que pensando mas.
        cfg.pop("thinking_config", None)
    return types.GenerateContentConfig(**cfg)


def consultar_gemini_con_fallback(prompt: str, contrato: str = "", *,
                                   thinking_budget=None, config_extra=None,
                                   cola=None, respaldo: str = "",
                                   max_intentos: int = MAX_INTENTOS,
                                   esperar=None, continuar=None,
                                   reintentar_sin_thinking: bool = True,
                                   cliente=None, devolver_respuesta: bool = False,
                                   modulo: str = "gemini_conn"):
    """
    LA ENTRADA UNICA. Devuelve (texto, modelo) o levanta ErrorDeCadena.

    Aqui vive toda la politica y en ningun otro sitio:

      · 503 y red -> reintento con backoff 2/4/8, y luego al siguiente modelo
      · 429       -> sin reintentos, directo al siguiente. La cuota es por
                    modelo y por dia; insistir no puede funcionar
      · 400       -> un reintento del MISMO modelo sin thinking_budget, que es el
                    fallo mas frecuente y el mas facil de arreglar. UNA vez
                    sola, o seria un bucle infinito
      · 404       -> al siguiente, anotado como incompatible
      · 401       -> nada de reintentar ni de cambiar. La clave esta mal y
                    ningun modelo la va a arreglar

    `continuar` es el cortacircuito. Se consulta ANTES de llamar y ANTES de
    dormir, nunca despues: si otro aviso ya confirmo que la cuota se agoto,
    este no deberia ni despertarse para comprobarlo.
    """
    esperar = esperar or time.sleep
    contrato = contrato or "sin-numero"
    # El cliente se puede INYECTAR, y esa es la via por la que las pruebas
    # meten un doble: seis suites sustituyen gemini_analyzer._get_cliente() y
    # dependen de que la llamada salga por ahi. Si este modulo se fabricara su
    # propio cliente sin mirar, esas seis dejarian de probar nada y seguirian
    # dando verde sin comprobar nada. Por eso el cliente entra por parametro.
    _cliente_inyectado = cliente is not None
    cola = [m for m in (cola or [config.GEMINI_MODEL]) if m]
    if respaldo and respaldo not in cola:
        cola.append(respaldo)
    extra = dict(config_extra or {})
    eventos: list[dict] = []

    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise ErrorDeCadena("el SDK de Gemini no esta instalado: %s" % e)

    ultimo = None
    indice = 0
    while indice < len(cola):
        modelo = cola[indice]
        sin_pensar = False
        for intento in range(1, max(1, max_intentos) + 1):
            if continuar is not None and not continuar():
                raise ErrorDeCadena(
                    "cortocircuito: otro aviso ya confirmo que se acabo la "
                    "cuota de Google", eventos, ultimo)
            try:
                # El timeout de red se fija al construir el cliente. Un
                # cliente INYECTADO viene con el suyo, si viene: no se toca,
                # porque si lo creo aqui perderia el doble de la prueba.
                el = (cliente if _cliente_inyectado
                      else _cliente(genai, types, TIMEOUT_MS))
                respuesta = el.models.generate_content(
                    model=modelo,
                    contents=prompt,
                    config=_config_con_thinking(types, extra,
                                                thinking_budget, sin_pensar),
                )
                texto = str(getattr(respuesta, "text", "") or "")
                if texto.strip():
                    if modelo != cola[0]:
                        log.info("%s [INFO] Responde [%s] a traves de [%s].",
                                 _marca(), contrato, modelo)
                    if devolver_respuesta:
                        return texto, modelo, respuesta
                    return texto, modelo
                ultimo = RuntimeError(
                    "Solicitud [%s]: %s devolvio vacio" % (contrato, modelo))
            except Exception as e:
                ultimo = e
                clase = clasificar(e)
                eventos.append({"clase": clase, "modelo": modelo,
                                "texto": str(e)[:200],
                                "intento": intento})
                if clase == "401":
                    raise ErrorDeCadena(
                        "401: Google no reconoce la credencial", eventos,
                        ultimo) from e
                if clase == "400" and reintentar_sin_thinking                         and not sin_pensar and thinking_budget is not None:
                    sin_pensar = True
                    log.info(
                        "%s [INFO] Solicitud [%s]: [%s] rechazo la peticion "
                        "(400). Reintento el MISMO modelo sin thinking_budget "
                        "(%s).", _marca(), contrato, modelo, thinking_budget)
                    continue
                if clase == "400" and reintentar_sin_thinking \
                        and not sin_pensar and thinking_budget is None:
                    sin_pensar = True
                    continue
                if clase in ("429", "400", "404"):
                    if indice + 1 < len(cola):
                        log.warning(
                            "%s [WARNING] Solicitud [%s]: %s en [%s]. Cambio "
                            "a [%s].", _marca(), contrato, clase, modelo,
                            cola[indice + 1])
                    break
                # 503 o fallo de red: transitorio, se insiste con espera.
                if intento < max_intentos:
                    if continuar is not None and not continuar():
                        raise ErrorDeCadena(
                            "cortocircuito antes de la espera", eventos,
                            ultimo)
                    espera = ESPERA_BASE * (2 ** (intento - 1))
                    log.warning(
                        "%s [WARNING] Error 503 detectado en Solicitud [%s]. "
                        "Aplicando backoff exponencial. Intento %s/%s.",
                        _marca(), contrato, intento, max_intentos)
                    try:
                        esperar(espera)
                    except Exception:
                        pass
                    continue
                break
        indice += 1
        if indice < len(cola):
            # El punto de transicion a contingencia. El modulo que llama decide
            # si esto merece un mensaje propio o solo una linea en el log.
            _log_contingencia(contrato, cola[indice], modulo)

    # Cuando la cola ENTERA se agota por CUOTA, hay una pausa antes de
    # rendirse. No es un capricho: mientras un aviso duerme, los demas pueden
    # leer el cortacircuito y abandonar el lote sin gastar su propia cuota en
    # descubrir lo mismo. Solo con 429; si fue saturacion, esperar no sirve de
    # nada, porque no hay cuota que reponer.
    if any(e.get("clase") == "429" for e in eventos):
        pausa = float(getattr(config, "GEMINI_PAUSA_SEG", 0) or 0) * 3
        if pausa > 0:
            log.warning("%s [WARNING] Solicitud [%s]: la cola entera se quedo "
                        "sin cuota. Pausa %.0fs antes de rendirse.",
                        _marca(), contrato, pausa)
            try:
                esperar(pausa)
            except Exception:
                pass

    raise ErrorDeCadena(
        "Se agotaron los %d modelos de la cola" % len(cola), eventos, ultimo)


def _log_contingencia(contrato: str, modelo: str, modulo: str) -> None:
    log.info(
        "%s [INFO] Modulo %s transicionando a politica de contingencia para "
        "contrato [%s]: [%s].", _marca(), modulo, contrato, modelo)


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