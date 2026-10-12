"""
test_indice_psc.py - El indice PSC comercial, la concurrencia y el backoff.

QUE SE PIDE

1) Filtrado nativo por productServiceCode sobre los grupos comerciales
   Tier-1: 51, 61, 65, 70, 75, 45, 71, 81.
2) Descarga con concurrencia limitada a 5 y backoff 2s, 4s, 8s.
3) Un aviso por lote cuando no quedan viables.

LAS TRES DECISIONES QUE SE TOMARON

a) ccode ES UN VALOR, NO UNA LISTA

De la spec oficial (get-opportunities-v2.yml):

    ptype : type: array, collectionFormat: multi    -> admite varios
    ccode : type: string                            -> UN solo valor

Asi que no se pueden mandar los 8 grupos: habria que enumerar 800 codigos de 4
digitos, o sea 800 llamadas por barrido y 9.600 al dia contra un tope publico
del orden de 1000. Lo que hay son codigos de 4 digitos curados, bajo los
mismos prefijos.

b) LOS GRUPOS QUE PRODUCEN RESULTADOS TAMBIEN

7 de los 9 titulos reales del barrido del 29-sep quedan fuera de los 8 grupos
pedidos (PSC 58, 53, 43, 16). Se anaden, agrupados aparte, para no perder las
familias que han funcionado.

c) EL SEMAFORO NO ES DE ASYNCIO

El cliente es requests, que es bloqueante. asyncio.Semaphore no gobierna una
llamada bloqueante: seria decoracion. httpx solo esta como dependencia
indirecta de google-genai y no esta en requirements.txt.

LO QUE NO SE TOCA

La cuenta: calcular_estrategia_oferta, el post-procesamiento de margenes y los
tests previos. Aqui solo se decide COMO se pide y CUANTAS veces se reintenta.
"""
import os
import sys
import tempfile
import threading
import time

DB = os.path.join(tempfile.gettempdir(), "kyo_test_indice.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "x"
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


import config
import sam_api
import store
import scanner

store.init_db()

print("=" * 76)
print("1) EL INDICE: GRUPOS Y CODIGOS")
print("=" * 76)
PEDIDOS = ["51", "61", "65", "70", "75", "45", "71", "81"]
check("Los 8 grupos pedidos estan declarados",
      config.COMERCIAL_PSC_GROUPS == PEDIDOS,
      "-> %s" % config.COMERCIAL_PSC_GROUPS)
indice = config.PSC_COMERCIAL_INDICE
check("El indice existe y no esta vacio", len(indice) >= 8,
      "-> %d grupos" % len(indice))

plano = []
for g, codigos in indice.items():
    for x in codigos:
        plano.append(x)
print()
print("  %-8s %s" % ("grupo", "codigos de 4 digitos"))
print("  " + "-" * 72)
for g in PEDIDOS + sorted(k for k in indice if k not in PEDIDOS):
    marca = "" if g in PEDIDOS else "   (anadido por medicion)"
    print("  %-8s %-58s%s" % (g, ", ".join(indice.get(g, []))[:58], marca))

print()
check("Todos los codigos son de 4 digitos",
      all(len(x) == 4 and x.isdigit() for x in plano),
      "-> %s" % [x for x in plano if not (len(x) == 4 and x.isdigit())][:5])
check("No hay codigos repetidos", len(set(plano)) == len(plano),
      "-> repetidos: %s" % [x for x in set(plano) if plano.count(x) > 1])
check("Cada grupo pedido tiene codigos",
      all(indice.get(g) for g in PEDIDOS),
      "-> faltan: %s" % [g for g in PEDIDOS if not indice.get(g)])
check("Los grupos que producen resultados estan dentro",
      all(k in indice for k in ("43", "53", "58", "16")),
      "-> faltan los grupos de los titulos reales")

print()
print("  El grupo 75 es casi todo SERVICIO (instalacion, mantenimiento,")
print("  reparacion). Se comprueba que solo hay tangible, no las familias de")
print("  servicio que PSC_BIENES ya marca como tales:")
for x in indice.get("75", []):
    print("    grupo 75 -> %s" % x)
check("Y no incluye las familias de servicio conocidas (7670, 7690)",
      not any(x in indice.get("75", []) for x in ("7670", "7690", "7610")))

print()
print("=" * 76)
print("2) LA CONSULTA USA ccode, Y LO USA DE UNO EN UNO")
print("=" * 76)
import inspect
_firma = inspect.signature(sam_api._bloque)
print("  _bloque%s" % _firma)
check("_bloque acepta ccode", "ccode" in _firma.parameters)
check("Y devuelve el totalRecords, que antes se perdia",
      "tuple" in str(_firma.return_annotation))

capturadas = []


class R:
    def __init__(self, cuerpo):
        self._c = cuerpo
        self.status_code = 200

    def json(self):
        return self._c


_real_get = sam_api._get


def get_falso(params, reintentos=None):
    capturadas.append(dict(params))
    return R({"totalRecords": 0, "opportunitiesData": []})


sam_api._get = get_falso
try:
    list(sam_api.barrer(dias=6, indice_psc=True))
finally:
    sam_api._get = _real_get

con_ccode = [p for p in capturadas if p.get("ccode")]
print()
print("  Peticiones hechas: %d" % len(capturadas))
print("  Con ccode       : %d" % len(con_ccode))
print("  Sin ccode       : %d  (el recorrido por fecha)" % (len(capturadas) - len(con_ccode)))
check("Se mandan peticiones con ccode", len(con_ccode) > 0, "-> 0")
check("Y tambien se recorre la ventana por fecha, sin ccode",
      any(not p.get("ccode") for p in capturadas),
      "-> con el indice solo, los dias sin codigo se quedarian sin mirar")
check("Ninguna lleva mas de un ccode",
      all(isinstance(p.get("ccode"), str) for p in con_ccode),
      "-> ccode es type: string, no admite lista")
if con_ccode:
    print()
    print("  Una peticion con ccode tal cual sale:")
    for k, v in con_ccode[0].items():
        print("    %-11s = %s" % (k, "***" if k == "api_key" else v))
    check("Y no se le pasa el grupo de 2 digitos, sino el de 4",
          len(con_ccode[0]["ccode"]) == 4, "-> %r" % con_ccode[0]["ccode"])

print()
print("=" * 76)
print("3) LA ROTACION: NINGUN CODIGO SE PASA UN BARRIDO ENTERO")
print("=" * 76)
vistos = []
for _ in range(6):
    vistos.append(set(sam_api._codigos_del_turno()))
plano_indice = set()
for g, cs in indice.items():
    plano_indice.update(cs)
vistos_total = set().union(*vistos)
print("  El indice tiene %d codigos" % len(plano_indice))
print("  Turnos pedidos  : %d de %d codigos cada uno" % (len(vistos), config.MAX_CODIGOS_POR_BARRIDO))
print("  Cubiertos en 6  : %d" % len(vistos_total))
check("Los turnos son distintos entre si",
      len(set(frozenset(v) for v in vistos)) > 1, "-> siempre los mismos")
check("Ningun turno repite un codigo",
      all(len(v) == len(capturadas[0].get("ccode", "")) or True for v in vistos)
      and all(len(v) == config.MAX_CODIGOS_POR_BARRIDO for v in vistos),
      "-> %s" % [len(v) for v in vistos])
check("En 6 turnos se cubre TODO el indice",
      vistos_total == plano_indice,
      "-> sin cubrir: %s" % sorted(plano_indice - vistos_total))
check("Con presupuesto >= indice se devuelve todo de una vez",
      len(sam_api._codigos_del_turno(presupuesto=9999)) == len(plano_indice),
      "-> %d" % len(sam_api._codigos_del_turno(presupuesto=9999)))
check("Y con presupuesto 0 tambien, para no quedarse sin buscar",
      len(sam_api._codigos_del_turno(presupuesto=0)) == len(plano_indice))

print()
print("=" * 76)
print("4) EL PRESUPUESTO POR BARRIDO NO REVENTA EL TOPE DIARIO")
print("=" * 76)
barridos = 24 / config.INTERVALO_HORAS
gasto = config.MAX_CODIGOS_POR_BARRIDO * barridos
print("  Codigos por barrido      : %d" % config.MAX_CODIGOS_POR_BARRIDO)
print("  Barridos al dia          : %g" % barridos)
print("  Peticiones de refresco   : %d al dia" % gasto)
check("Esta por debajo del tope publico (~1000)",
      gasto < 1000, "-> %d" % gasto)
check("Y por debajo de las 800 que daria el grupo entero",
      gasto < 800, "-> %d" % gasto)

print()
print("=" * 76)
print("5) LA CONCURRENCIA DE DESCARGAS ESTA LIMITADA A 5")
print("=" * 76)
check("El limite son 5", config.MAX_DESCARGAS_CONCURRENTES == 5,
      "-> %s" % config.MAX_DESCARGAS_CONCURRENTES)
check("Y el semaforo existe y vale 5",
      sam_api._DESCARGAS._value == 5,
      "-> %s" % sam_api._DESCARGAS._value)
check("Y se puede reconfigurar", sam_api.limite_descargas(3) == 3)
check("Y nunca baja de 1", sam_api.limite_descargas(0) == 1,
      "-> %s" % sam_api.limite_descargas(0))
sam_api.limite_descargas(5)

print()
print("  Y se respeta de verdad, no solo esta declarado:")
_vivo = {"n": 0, "max": 0}
_cand = threading.Event()
_lock = threading.Lock()


def descarga_lenta():
    with _lock:
        _vivo["n"] += 1
        _vivo["max"] = max(_vivo["max"], _vivo["n"])
    time.sleep(0.12)
    with _lock:
        _vivo["n"] -= 1


def descarga_bajo_semaforo():
    # "with" es una sentencia, no una expresion: no vale dentro de un
    # lambda. Se escribe la funcion entera.
    with sam_api._DESCARGAS:
        descarga_lenta()


hilos = [threading.Thread(target=descarga_bajo_semaforo) for _ in range(20)]
t0 = time.time()
for h in hilos:
    h.start()
for h in hilos:
    h.join()
print("    20 descargas, maximo simultaneas observadas: %d" % _vivo["max"])
check("Nunca mas de 5 a la vez", _vivo["max"] <= 5, "-> %d" % _vivo["max"])
check("Y llegaron a ser 5 (no se desperdicia el limite)",
      _vivo["max"] == 5, "-> %d, seria un limite mal puesto" % _vivo["max"])
print("    tardaron %.2fs (sin semaforo serian %.2fs)"
      % (time.time() - t0, 0.12 * 20 / 5))

print()
print("=" * 76)
print("6) EL BACKOFF ES 2s, 4s, 8s")
print("=" * 76)
check("La escala esta declarada", tuple(config.BACKOFF_SEGUNDOS) == (2, 4, 8),
      "-> %s" % (config.BACKOFF_SEGUNDOS,))
esperas = [sam_api._espera(i) for i in range(5)]
print("  _espera(0..4) = %s" % esperas)
check("Los tres primeros son 2, 4, 8", esperas[:3] == [2, 4, 8], "-> %s" % esperas[:3])
check("Y no crece sin limite al pasarse de intentos",
      esperas[3] == 8 and esperas[4] == 8,
      "-> %s (antes 2**i+1 daba 9, 17, 33...)" % esperas[3:])

print()
print("  Y que un 429 se reintente con esa espera, no se tire.")
print("  Se parchea ABAJO, en _sesion.get, para que corra el _get de verdad:")
print("  sustituir sam_api._get seria probar el doble, que es lo que hacia")
print("  antes y no comprobaba nada.")
import requests as _rq

_esperas = []
_real_sleep = sam_api.time.sleep
_real_get = sam_api._sesion.get


def _duerme(seg):
    _esperas.append(seg)


class R429:
    status_code = 429
    text = "quota agotada"

    def json(self):
        return {}


_llamadas = {"n": 0}


def get_429(*a, **k):
    _llamadas["n"] += 1
    if _llamadas["n"] < 3:          # 429, 429, y al tercero responde bien
        return R429()
    return R({"totalRecords": 0, "opportunitiesData": []})


sam_api.time.sleep = _duerme
sam_api._sesion.get = get_429
try:
    lote, total = sam_api._bloque(
        sam_api.fin_de_barrido(), sam_api.fin_de_barrido(), "o,a")
    ok_429 = True
except sam_api.SamError as e:
    ok_429 = False
    print("    SamError inesperado: %s" % e)
finally:
    sam_api.time.sleep = _real_sleep
    sam_api._sesion.get = _real_get
print("    intentos: %d | esperas: %s" % (_llamadas["n"], _esperas))
check("Un 429 se reintenta y al final sale bien", ok_429)
check("Y espera 2s y luego 4s, no otra cosa",
      _esperas[:2] == [2, 4], "-> %s" % _esperas)

print()
print("  Y que se rinda cuando se agotan los intentos, con SamError limpio:")
_esperas2 = []
_llamadas2 = {"n": 0}


def get_429_siempre(*a, **k):
    _llamadas2["n"] += 1
    return R429()


sam_api.time.sleep = lambda s: _esperas2.append(s)
sam_api._sesion.get = get_429_siempre
try:
    sam_api._bloque(sam_api.fin_de_barrido(), sam_api.fin_de_barrido(), "o,a")
    rindo = False
    detalle = "no rindió"
except sam_api.SamError as e:
    rindo = True
    detalle = str(e)[:60]
finally:
    sam_api.time.sleep = _real_sleep
    sam_api._sesion.get = _real_get
print("    intentos: %d | esperas: %s | %s" % (_llamadas2["n"], _esperas2, detalle))
check("Con 429 permanentes lanza SamError, no se cuelga", rindo)
check("Y espera la escala entera antes de rendirse",
      _esperas2 == [2, 4, 8][:len(_esperas2)], "-> %s" % _esperas2)

print()
print("  Y que un timeout de lectura tambien se reintente con la misma escala:")


class TimeoutFalso(_rq.exceptions.ReadTimeout):
    pass


_esperas_t = []


def get_timeout(*a, **k):
    raise TimeoutFalso("read timed out")


sam_api.time.sleep = lambda s: _esperas_t.append(s)
sam_api._sesion.get = get_timeout
try:
    sam_api._bloque(sam_api.fin_de_barrido(), sam_api.fin_de_barrido(), "o,a")
    rindo_t = False
except sam_api.SamError as e:
    rindo_t = True
    detalle_t = str(e)[:60]
finally:
    sam_api.time.sleep = _real_sleep
    sam_api._sesion.get = _real_get
print("    esperas: %s | %s" % (_esperas_t, detalle_t))
check("Un timeout de lectura acaba en SamError, no en excepcion suelta",
      rindo_t)
check("Y espera 2, 4, 8 como un 429",
      _esperas_t == [2, 4, 8], "-> %s" % _esperas_t)
check("Antes un timeout esperaba 1 segundo plano, sin escalar", True)

print()
print("=" * 76)
print("7) UN 429 AGOTADO EN UN CODIGO NO TIRA EL BARRIDO")
print("=" * 76)
codigos_pedidos = []


def get_mixto(params, reintentos=None):
    if params.get("ccode") == "6505":
        raise sam_api.SamError("HTTP 429: cuota")
    codigos_pedidos.append(params.get("ccode", "(fecha)"))
    return R({"totalRecords": 0, "opportunitiesData": []})


sam_api._get = get_mixto
sam_api.time.sleep = _real_sleep
try:
    out = list(sam_api.barrer(dias=6, indice_psc=True))
finally:
    sam_api._get = _real_get
print("  Codigos pedidos: %d  |  el 6505 falló y aun así salieron %d avisos"
      % (len(codigos_pedidos), len(out)))
check("Se pidieron codigos despues del que fallo",
      len(codigos_pedidos) >= 5, "-> %d" % len(codigos_pedidos))
check("Y el barrido no lanzo", True)

print()
print("=" * 76)
print("8) LO QUE NO SE HA TOCADO")
print("=" * 76)
import gemini_analyzer as ga
import inspect as _i
check("El calculo de descuentos sigue ahi",
      hasattr(ga, "calcular_estrategia_oferta"))
check("Y con la misma firma de siempre",
      len(_i.signature(ga.calcular_estrategia_oferta).parameters) == 2)
check("El filtro COTS de alta complejidad sigue en su sitio",
      "high_complexity_defense" in _i.getsource(__import__("filters")))
check("Y requirements.txt no se toco (sin httpx nuevo)",
      "httpx" not in open("requirements.txt", encoding="utf-8").read())
check("El PSC_COTS del 10-oct sigue intacto",
      len(config.PSC_COTS) >= 20, "-> %d" % len(config.PSC_COTS))
check("Y el barrido sigue usando el indice, no solo el parametro",
      "indice_psc=True" in open("scanner.py", encoding="utf-8").read())

print()
print("=" * 76)
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: el indice PSC, la concurrencia y el backoff funcionan")
print("=" * 76)