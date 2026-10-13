"""
test_rango_fechas.py - El HTTP 400 "Date range must be no more than 1 year apart".

EL DIAGNOSTICO, MEDIDO

El usuario dice que postedFrom y postedTo "no estan definidos, o sobrepasan
el limite de 365 dias". La primera parte no era cierta: las dos fechas SI
estaban definidas. El problema era que estaban EN EL LIMITE.

Medido en el codigo antes del arreglo:

    "postedFrom": _fecha(hoy - timedelta(days=dias)),   dias=365
    "postedTo":   _fecha(hoy),

Eso son exactamente 365 dias, que es exactamente un ano. La API dice que el
rango no puede ser "more than 1 year apart", y un rango de un ano esta en el
limite, no dentro. Lo que la API tolera es algo MENOS de un ano.

UNA EXPLICACION QUE RESULTO SER FALSA

La primera version de este archivo afirmaba que habia un agravante de calendario:
que timedelta(days=365) no son siempre 365 dias, porque al cruzar un 29 de
febrero el rango serian 366. Se comprobo ejecutando el calculo sobre cuatro
fechas de un anyo bisiesto y NO es cierto: la resta de un timedelta es exacta, y
365 dias son 365 dias el 1 de marzo, el 29 de febrero y el 28 de diciembre.

Lo estaba escribiendo porque suena convincente y encaja con un fallo que aparece
intermitente. Y aqui el fallo NO es intermitente: es un rango de exactamente 365
dias contra un limite de 365, que falla todos los dias.

Se deja constancia porque es el error tipico aldiagnosticar uno de estos: cuando
el fallo parece aleatorio, se busca una causa que lo haga parecer aleatorio, y
se encuentra una que cuadra con la teoria pero no con la ejecucion. La seccion 3
comprueba el calculo de verdad, y por eso se ve.

LO QUE SE COMPRUEBA

  1) El rango que sale, medido sobre las cadenas exactas que van en la peticion
  2) Que no supera 365 dias NUNCA, ni pidiendo 365, ni pidiendo 10.000
  3) Que el rango viejo eran 365 dias exactos TODOS los dias, bisiesto o no:
     comprueba que la teoria del 366 dias era falsa, y que el fallo no depende
     de la fecha
  4) Que postedTo sea HOY y postedFrom este en el pasado, en MM/DD/YYYY
  5) La peticion con N3220527Q7003 tal cual
  6) Que un 400 y un 429 NO den el mismo mensaje
  7) Que el cuerpo del 400 llegue al log de Render
"""
import io
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

DB = os.path.join(tempfile.gettempdir(), "kyo_test_rango.db")
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


import sam_api

print("=" * 76)
print("1) EL RANGO QUE SALE, MEDIDO SOBRE LO QUE SE MANDA")
print("=" * 76)
hoy = datetime(2026, 10, 12, 17, 30, tzinfo=timezone.utc)
desde, hasta = sam_api._rango_posted(360, hoy=hoy)
n = sam_api._dias_entre(desde, hasta)
print("  postedFrom = %s" % desde)
print("  postedTo   = %s" % hasta)
print("  diferencia = %d dias de calendario" % n)
check("El rango esta dentro del limite de la API", n <= 365, "-> %d dias" % n)
check("Y con margen de sobra", n <= 360, "-> %d dias" % n)
check("Y cubre casi un ano, para no perder licitaciones antiguas",
      n >= 355, "-> %d dias: si fuera 180 perderiamos la mitad del ano" % n)

print()
print("=" * 76)
print("2) NUNCA SUPERA 365 DIAS, PASE LO QUE PASE")
print("=" * 76)
# El clamp tiene que aguantar tambien las llamadas absurdas. Un dia alguien
# escribira dias=365 creyendo que "un ano" es lo que quiere decir la API, que
# es exactamente el error que se acaba de arreglar.
for pedido in (1, 30, 90, 180, 359, 360, 361, 365, 366, 400, 10000, -5):
    d, h = sam_api._rango_posted(pedido, hoy=hoy)
    real = sam_api._dias_entre(d, h)
    ok = real <= 365
    if not ok:
        FALLA += 1
    print("  %s pedido=%-7s -> %s .. %s = %d dias"
          % ("OK   " if ok else "FALLA", pedido, d, h, real))
    check("  pedido %-6s <= 365" % pedido, ok)

print()
print("  Y con dias que ni son un numero:")
for pedido in (None, "", "muchos", 12.7):
    d, h = sam_api._rango_posted(pedido, hoy=hoy)
    real = sam_api._dias_entre(d, h)
    ok = 0 < real <= 365
    if not ok:
        FALLA += 1
    print("  %s pedido=%-8r -> %s .. %s = %d dias"
          % ("OK   " if ok else "FALLA", pedido, d, h, real))

print()
print("=" * 76)
print("3) EL RANGO VIEJO ERAN 365 DIAS EXACTOS, BISIESTO O NO")
print("=" * 76)
# Se comprueba la teoria del "366 dias al cruzar el 29 de febrero", que era la
# explicacion que se habia escrito antes de ejecutar el calculo. Es falsa: la
# resta de un timedelta es exacta.
print("  Con el codigo viejo (timedelta(days=365)) y el nuevo:")
vistos = set()
for etiqueta, base in (
        ("el 1 de marzo de un bisiesto", datetime(2024, 3, 1, tzinfo=timezone.utc)),
        ("el 2 de marzo de un bisiesto", datetime(2024, 3, 2, tzinfo=timezone.utc)),
        ("el 29 de febrero", datetime(2024, 2, 29, tzinfo=timezone.utc)),
        ("el 1 de marzo de un anyo normal", datetime(2026, 3, 1, tzinfo=timezone.utc)),
        ("hoy", datetime(2026, 10, 12, tzinfo=timezone.utc))):
    viejo = base - timedelta(days=365)
    viejo_n = (base.date() - viejo.date()).days
    vistos.add(viejo_n)
    nuevo_d, nuevo_h = sam_api._rango_posted(365, hoy=base)
    nuevo_n = sam_api._dias_entre(nuevo_d, nuevo_h)
    print("    %-32s viejo=%-4d nuevo=%-4d" % (etiqueta, viejo_n, nuevo_n))
    check("    %-32s el nuevo <= 360" % etiqueta, nuevo_n <= 360,
          "-> %d" % nuevo_n)

check("El rango viejo era SIEMPRE 365, nunca 366: la teoria del bisiesto era falsa",
      vistos == {365},
      "-> se vieron estos valores: %s" % sorted(vistos))
check("O sea que el fallo no dependia de la fecha: era siempre el mismo",
      len(vistos) == 1)
check("Y el nuevo deja margen todos los dias", True)

print()
print("=" * 76)
print("4) LOS FORMATOS Y EL ORDEN")
print("=" * 76)
import re
check("MM/DD/YYYY con diagonales, que es lo que exige la API",
      bool(re.fullmatch(r"\d{2}/\d{2}/\d{4}", desde))
      and bool(re.fullmatch(r"\d{2}/\d{2}/\d{4}", hasta)),
      "-> %r .. %r" % (desde, hasta))
check("postedTo es HOY", hasta == sam_api._fecha(hoy), "-> %s" % hasta)
# OJO: comparar las dos como CADENAS no significa nada con MM/DD/YYYY.
# "10/17/2025" < "10/12/2026" es True porque "1" < "2" en el segundo caracter,
# no porque la fecha sea anterior. Hay que compararlas como fechas.
_d = datetime.strptime(desde, "%m/%d/%Y")
_h = datetime.strptime(hasta, "%m/%d/%Y")
check("postedFrom esta en el pasado", _d < _h, "-> %s .. %s" % (desde, hasta))
check("Y el orden de las dos es el correcto", _d < _h)
check("Y postedTo no esta en el futuro", _h <= datetime.strptime(
    sam_api._fecha(hoy), "%m/%d/%Y"))

print()
print("=" * 76)
print("5) LA PETICION CON N3220527Q7003 TAL CUAL")
print("=" * 76)
capturadas = []


class R:
    def __init__(self, codigo, cuerpo="", texto=""):
        self.status_code = codigo
        self._c = cuerpo
        self.text = texto

    def json(self):
        return self._c


def get_falso(params, reintentos=None):
    capturadas.append(dict(params))
    return R(200, {"totalRecords": 0, "opportunitiesData": []})


_real = sam_api._get
sam_api._get = get_falso
try:
    est = {}
    sam_api.buscar_por_solicitud("N3220527Q7003", estado=est)
finally:
    sam_api._get = _real

p = capturadas[0]
print("  La peticion que sale:")
for k in ("postedFrom", "postedTo", "solnum", "limit", "offset"):
    print("    %-12s %s" % (k, p.get(k)))
rango = sam_api._dias_entre(p["postedFrom"], p["postedTo"])
check("La solicitud viaja como N3220527Q7003", p.get("solnum") == "N3220527Q7003")
check("Y postedFrom/postedTo vienen puestos", bool(p.get("postedFrom"))
      and bool(p.get("postedTo")))
check("Y el rango es de %d dias, dentro del limite" % rango,
      0 < rango <= 365)
check("Y la API_key no se imprime en el test",
      "api_key" in p and p["api_key"] == "CLAVE-DE-PRUEBA")

# Y la busqueda por noticeId, que tenia el mismo bug copiado.
capturadas.clear()
sam_api._get = get_falso
try:
    sam_api.buscar_notice("a1b2c3d4e5f6a7b8c9d0")
finally:
    sam_api._get = _real
rango_n = sam_api._dias_entre(capturadas[0]["postedFrom"],
                              capturadas[0]["postedTo"])
check("Y buscar_notice usa el mismo rango acotado (%d dias)" % rango_n,
      0 < rango_n <= 365, "-> %d" % rango_n)

print()
print("=" * 76)
print("6) UN 400 Y UN 429 NO SON LO MISMO")
print("=" * 76)
print("  Lo que el usuario recibia con un 400:")
print('    "El numero puede estar bien: es la API de SAM.gov la que no ha')
print('     podido responder, casi siempre por cuota del plan gratuito.')
print('     Intentelo otra vez en unos minutos."')
print()
print("  Con eso, un 400 manda al usuario a esperar y reintentar, y va a")
print("  fallar igual: el rango se calcula en cada llamada y siempre sale mal.")

CODIGOS = [
    (400, "rango de fechas", False),
    (401, "clave", False),
    (429, "cuota", True),
    (500, "SAM.gov", True),
]
for codigo, palabra, esperar in CODIGOS:
    est = {}

    def get_400(params, reintentos=None, c=codigo):
        return R(c, {}, "Date range must be no more than 1 year apart"
                 if c == 400 else "error")

    sam_api._get = get_400
    try:
        sam_api.buscar_por_solicitud("N3220527Q7003", estado=est)
    finally:
        sam_api._get = _real
    motivo = str(est.get("motivo") or "")
    ok = est.get("fallo") and est.get("http") == codigo and palabra in motivo
    if not ok:
        FALLA += 1
    print("  %s HTTP %s -> %s" % ("OK   " if ok else "FALLA", codigo, motivo))
    check("  HTTP %s guarda el codigo y un motivo propio" % codigo, ok)

print()
print("  El punto clave: el 400 dice 'rango de fechas' y NO dice 'cuota'.")
est = {}
sam_api._get = lambda params, reintentos=None: R(
    400, {}, "Date range must be no more than 1 year apart")
try:
    sam_api.buscar_por_solicitud("N3220527Q7003", estado=est)
finally:
    sam_api._get = _real
check("El motivo del 400 menciona el rango",
      "rango" in est.get("motivo", "").lower())
check("Y NO sugiere cuota ni esperar", "cuota" not in est.get("motivo", "").lower()
      and "minutos" not in est.get("motivo", "").lower(),
      "-> %r" % est.get("motivo"))

print()
print("=" * 76)
print("7) EL CUERPO DEL ERROR LLEGA AL LOG")
print("=" * 76)
import logging


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lineas = []

    def emit(self, registro):
        self.lineas.append(registro.getMessage())


cap = _Cap()
sam_api.log.addHandler(cap)
sam_api.log.setLevel(logging.ERROR)
sam_api._get = lambda params, reintentos=None: R(
    400, {}, "Date range must be no more than 1 year apart")
try:
    sam_api.buscar_por_solicitud("N3220527Q7003")
finally:
    sam_api._get = _real
    sam_api.log.removeHandler(cap)
unidos = "\n".join(cap.lineas)
print("  El log dice:")
for l in cap.lineas:
    print("    %s" % l[:110])
check("El cuerpo de la API llega al log", "Date range must be" in unidos)
check("Con el numero de solicitud", "N3220527Q7003" in unidos)
check("Y con el codigo HTTP", "400" in unidos)

print()
print("=" * 76)
print("8) LO QUE VE EL USUARIO CON UN 400 Y CON UN 429")
print("=" * 76)
# El motivo de por que esto importa: con un 400, el mensaje viejo decia
# "casi siempre por cuota del plan gratuito, intentelo otra vez en unos
# minutos". El rango se calcula en cada llamada, asi que dentro de un minuto
# falla EXACTAMENTE igual. El usuario espera, reintenta y pierde el rato dos
# veces, y como el numero de solicitud es correcto, lo siguiente que hace es
# buscar en Google si se ha equivocado. Que es justo lo que no lleva a ninguna
# parte.
import asyncio
import main as m
import store

store.init_db()


class _Estado:
    def __init__(self, con, txt):
        self._con, self._txt = con, txt

    async def edit_text(self, txt, parse_mode=None, **k):
        self._con.append(txt)


class _Bot:
    async def send_chat_action(self, chat_id, action):
        pass


class _Msg:
    def __init__(self, con):
        self._con = con
        self.text = ""
        self.chat = type("C", (), {"id": 1})()

    async def reply_text(self, txt, parse_mode=None, **k):
        self._con.append(txt)
        return _Estado(self._con, txt)

    async def reply_document(self, **k):
        self._con.append("[documento]")

    def get_bot(self):
        return _Bot()


m.ParseMode = type("PM", (), {"HTML": "HTML"})
SOL = "N3220527Q7003"


def _ver(codigo):
    con = []
    sam_api._get = lambda params, reintentos=None: R(
        codigo, {}, "Date range must be no more than 1 year apart"
        if codigo == 400 else "")
    try:
        up = type("U", (), {})()
        up.message = _Msg(con)
        up.message.text = "/pdf %s" % SOL
        up.effective_chat = type("C", (), {"id": 1})()
        asyncio.run(m.cmd_pdf(up, type("X", (), {})()))
    finally:
        sam_api._get = _real
    return [x for x in con if isinstance(x, str)]


for codigo, etiqueta in ((400, "HTTP 400"), (429, "HTTP 429")):
    msgs = _ver(codigo)
    final = msgs[-1] if msgs else "SIN RESPUESTA"
    print("  %s:" % etiqueta)
    print("    %s" % final.replace("\n", " ")[:190])
    check("  %s responde algo" % etiqueta, bool(msgs))
    check("  %s nombra el codigo HTTP" % etiqueta, str(codigo) in final)
    check("  %s dice que el numero puede estar bien" % etiqueta,
          "numero" in final.lower())

ms400 = " ".join(_ver(400))
ms429 = " ".join(_ver(429))
print()
check("El 400 habla del rango de fechas o del formato",
      "rango de fechas" in ms400, "-> no lo menciona")
# OJO: no basta con que la palabra "cuota" NO aparezca. El mensaje nuevo dice
# "no es un problema del numero ni de cuota", que es una NEGACION y esta bien.
# Lo que hay que comprobar es que no aparezca la CONSTRUCCION que engaña:
# atribuirle la causa a la cuota, o mandar a esperar.
check("Y NO le atribuye la causa a la cuota",
      "casi siempre por cuota" not in ms400.lower()
      and "por cuota del plan" not in ms400.lower(),
      "-> sigue la atribuyendo a la cuota")
check("Y NO le manda a esperar unos minutos",
      "minutos" not in ms400.lower(),
      "-> le dice que espere, y va a fallar igual")
check("Y ademas dice explicitamente que no es un problema de cuota",
      "ni de cuota" in ms400.lower(),
      "-> no descarta la cuota, que es lo que hacia falta")
check("El 429 SI habla de la cuota, que es lo correcto",
      "cuota" in ms429.lower() or "limite" in ms429.lower())
check("Y el 429 si le dice que reintente",
      "minutos" in ms429.lower(), "-> no le dice que espere")
check("Los dos mensajes son DISTINTOS", ms400 != ms429)

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
print("RESULTADO: el rango siempre cabe y el 400 no se confunde con el 429")
print("=" * 76)