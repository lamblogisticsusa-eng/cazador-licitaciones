"""
test_pdf_flujo.py - El comando /pdf de punta a punta, con W9127N26QA145.

QUE PIDE LA TAREA

"Ejecuta las pruebas del bot con una licitacion valida (ej: W9127N26QA145) y
confirma que genera la respuesta y el archivo sin silencios."

ESTE ARCHIVO ES ESO: llama a cmd_pdf() de verdad, con un Update y un Context
falsos que registran todo lo que el handler intenta enviar a Telegram, y con la
API de SAM.gov sustituida por una que devuelve W9127N26QA145.

No prueba el codigo de dentro del handler, que es lo que hace test_pdf_rfq.py.
Prueba lo que el USUARIO VE: que le llega un mensaje antes de que termine nada,
que le llega el archivo, y que en ningun camino se queda sin contestacion.

Los caminos que se recorren:

  1. /pdf sin argumentos                 -> lista lo disponible
  2. /pdf W9127N26QA145                  -> respuesta inmediata + PDF
  3. /pdf w9127n26qa145 (minusculas)     -> responde igual
  4. /pdf "  W9127N26QA145  " (sucio)    -> responde igual
  5. /pdf W9127N26QA145 | Distribuidor   -> con proveedor puesto
  6. /pdf con numero que no existe       -> mensaje claro, NO silencio
  7. /pdf con la API caida               -> mensaje de error, NO silencio
  8. /pdf con la generacion rota         -> mensaje de error, NO silencio

En todos: NUNCA puede quedar sin responder.
"""
import asyncio
import io
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_pdf_flujo.db")
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


import main as m
import pdf_generator as pdfg
import sam_api
import store

store.init_db()

SOL = "W9127N26QA145"
AVISO = {
    "noticeId": "a1b2c3d4e5f6a7b8c9d0",
    "solicitationNumber": SOL,
    "title": "53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL",
    "fullParentPathName": "Department of the Navy",
    "typeOfSetAsideDescription": "Total Small Business Set-Aside",
    "naicsCode": "332999", "classificationCode": "5330",
    "baseType": "Solicitation",
    "postedDate": "2026-10-02 09:00:00",
    "responseDeadLine": "2026-11-14T17:00:00-04:00",
    "uiLink": "https://sam.gov/opp/a1b2c3d4e5f6a7b8c9d0/view",
    "placeOfPerformance": {"city": "Philadelphia", "state": "Pennsylvania"},
    "description": "https://api.sam.gov/prod/opportunities/v1/noticedesc"
                   "?noticeid=a1b2c3d4e5f6a7b8c9d0",
}
DESC = ("Line Item Number 0001: NSN 5330-01-586-4931, Quantity 50 EA. "
        "Unit of Issue EA. MECHANICAL SEAL KIT, EPDM 70 SHORE A PER ASTM D2000.\n"
        "CLIN 0002: NSN 5330-01-586-4932, Quantity 10 KT. Unit of Issue KT.\n")


# ------------------------------------------------------------------ dobles
class Estado:
    """Lo que el handler 'edita': el mensaje de estado."""

    def __init__(self, con, texto):
        self._con = con
        self._texto = texto

    async def edit_text(self, txt, parse_mode=None, **k):
        self._con.append(("estado_editado", txt))
        self._texto = txt


class Bot:
    def __init__(self, con):
        self._con = con

    async def send_chat_action(self, chat_id, action):
        self._con.append(("chat_action", action))


class Mensaje:
    def __init__(self, con):
        self._con = con
        self.text = ""

    async def reply_text(self, txt, parse_mode=None, **k):
        self._con.append(("texto", txt))
        # OJO: hay que DEVOLVER el objeto de estado. En el handler real,
        # reply_text devuelve el Message que se puede editar despues, y el
        # codigo hace:
        #
        #     estado = await update.message.reply_text(...)
        #     await estado.edit_text(...)
        #
        # Si el doble devuelve None, revienta en la edicion. Y como la
        # edicion esta dentro del try que captura las excepciones, el fallo
        # se disfrazaba de error de generacion del PDF.
        return Estado(self._con, txt)

    async def reply_document(self, document=None, filename=None, caption=None,
                             parse_mode=None, **k):
        datos = document.getvalue() if hasattr(document, "getvalue") else b""
        self._con.append(("documento", (filename, len(datos), caption)))

    def get_bot(self):
        return Bot(self._con)


class Chat:
    id = 123456789


class Update:
    def __init__(self, texto, con):
        self.message = Mensaje(con)
        self.message.text = texto
        self.message.chat = Chat()
        self.effective_chat = Chat()


class Ctx:
    pass


class _Enum:
    HTML = "HTML"


class _PM:
    HTML = "HTML"


m.ParseMode = _PM


def ejecutar(texto, con):
    """Corre cmd_pdf y devuelve lo que se intento enviar."""
    asyncio.run(m.cmd_pdf(Update(texto, con), Ctx()))
    return con


def msgs(con):
    return [t for k, t in con if k in ("texto", "estado_editado")]


print("=" * 76)
print("SIMULACION DE SAM.GOV CON W9127N26QA145")
print("=" * 76)

_real_get = sam_api._get
_real_desc = sam_api.obtener_descripcion


def get_falso(params, reintentos=None):
    class R:
        status_code = 200

        def __init__(self, c):
            self._c = c

        def json(self):
            return self._c

    if params.get("solnum") == SOL:
        return R({"totalRecords": 1, "opportunitiesData": [AVISO]})
    if params.get("noticeid") == AVISO["noticeId"]:
        return R({"totalRecords": 1, "opportunitiesData": [AVISO]})
    return R({"totalRecords": 0, "opportunitiesData": []})


sam_api._get = get_falso
sam_api.obtener_descripcion = lambda nid, opp=None: DESC

print()
print("=" * 76)
print("1) /pdf W9127N26QA145")
print("=" * 76)
con = ejecutar("/pdf " + SOL, [])
for k, v in con:
    if k == "texto":
        print("  [texto]     %s" % v.replace("\n", " ")[:96])
    elif k == "estado_editado":
        print("  [estado]    %s" % v.replace("\n", " ")[:96])
    elif k == "chat_action":
        print("  [accion]    %s" % v)
    elif k == "documento":
        print("  [PDF]       %s (%d bytes)" % (v[0], v[1]))

estados = [t for k, t in con if k in ("texto", "estado_editado")]
docs = [v for k, v in con if k == "documento"]
check("Respondio ALGO", len(estados) >= 1)
check("El primer mensaje es el de estado, con el numero limpio",
      estados and SOL in estados[0] and "Consultando licitacion" in estados[0],
      "-> %r" % (estados[0][:70] if estados else None))
check("Mando el indicador upload_document",
      any(k == "chat_action" and v == "upload_document" for k, v in con))
check("Y el PDF llego", len(docs) == 1, "-> %d documentos" % len(docs))
if docs:
    check("Con nombre de fichero y tamano razonable",
          docs[0][1] > 50000, "-> %d bytes" % docs[0][1])
    check("Y el pie de foto lleva la solicitud", SOL in docs[0][2])
print()
print("  El mensaje final:")
print("    %s" % estados[-1].replace("\n", "\n    ")[:420])

print()
print("=" * 76)
print("2) EL MISMO NUMERO, ESCRITO DE OTRAS FORMAS")
print("=" * 76)
variantes = [
    ("/pdf w9127n26qa145", "minusculas"),
    ("/pdf  %s  " % SOL, "espacios alrededor"),
    ("/pdf W9127\u00a0N26QA145", "espacio duro invisible"),
    ("/pdf %s | Nombre del distribuidor" % SOL, "con proveedor"),
]
for cmd, etiqueta in variantes:
    con = ejecutar(cmd, [])
    e = [t for k, t in con if k in ("texto", "estado_editado")]
    d = [v for k, v in con if k == "documento"]
    check("  %-24s responde y manda PDF" % etiqueta,
          bool(e) and len(d) == 1,
          "-> %d mensajes, %d PDFs" % (len(e), len(d)))

print()
print("=" * 76)
print("3) UNA SOLICITUD QUE NO EXISTE")
print("=" * 76)
con = ejecutar("/pdf W0000X00XX999", [])
e = [t for k, t in con if k in ("texto", "estado_editado")]
print("  %s" % (e[-1].replace("\n", " ")[:150] if e else "SIN RESPUESTA"))
check("NO se queda callado", bool(e))
check("Y explica por que puede fallar",
      any("No encuentro" in x for x in e)
      and any("archiv" in x.lower() or "cuota" in x.lower() for x in e))
check("Y no manda ningun PDF", not any(k == "documento" for k, v in con))

print()
print("=" * 76)
print("4) LA API DE SAM.GOV ESTA CAIDA")
print("=" * 76)


def get_caido(params, reintentos=None):
    raise sam_api.SamError("HTTP 429: cuota agotada")


sam_api._get = get_caido
con = ejecutar("/pdf " + SOL, [])
sam_api._get = get_falso
e = [t for k, t in con if k in ("texto", "estado_editado")]
print("  %s" % (e[-1].replace("\n", " ")[:150] if e else "SIN RESPUESTA"))
check("NO se queda callado con la API caida", bool(e))
check("Y el mensaje de estado salio igualmente",
      any("Consultando licitacion" in x for x in e))
check("No manda PDF falso", not any(k == "documento" for k, v in con))

print()
print("=" * 76)
print("5) LA GENERACION DEL PDF FALLA")
print("=" * 76)
_real_po = pdfg.generar_po


def po_roto(datos, db_path=None):
    raise RuntimeError("reportlab: no disk space")


pdfg.generar_po = po_roto
con = ejecutar("/pdf " + SOL, [])
pdfg.generar_po = _real_po
e = [t for k, t in con if k in ("texto", "estado_editado")]
print("  %s" % (e[-1].replace("\n", " ")[:150] if e else "SIN RESPUESTA"))
check("NO se queda callado si el PDF falla", bool(e))
check("Y el error menciona los logs de Render",
      any("logs de Render" in x for x in e))

print()
print("=" * 76)
print("6) /pdf SIN ARGUMENTO")
print("=" * 76)
con = ejecutar("/pdf", [])
e = [t for k, t in con if k in ("texto", "estado_editado")]
check("Responde con la lista o con la explicacion", bool(e))
print("  %s" % (e[-1].replace("\n", " ")[:150] if e else "SIN RESPUESTA"))
if not store.analisis_recientes(limite=5):
    check("Y explica que puede usar CUALQUIER numero de SAM.gov",
          any("W9127N26QA145" in x for x in e))

print()
print("=" * 76)
print("7) NINGUN CAMINO SE QUEDA SIN RESPONDER")
print("=" * 76)
for cmd in ("/pdf", "/pdf " + SOL, "/pdf w9127n26qa145",
            "/pdf W0000X00XX999", "/pdf   ", "/pdf | solo proveedor",
            "/pdf !!!", "/pdf " + SOL + " | Acme"):
    con = ejecutar(cmd, [])
    e = [t for k, t in con if k in ("texto", "estado_editado")]
    ok = bool(e)
    print("  %-34s %s (%d mensajes)"
          % (repr(cmd)[:34], "responde" if ok else "SILENCIO", len(e)))
    check("  %-32s responde" % repr(cmd)[:32], ok)

sam_api._get = _real_get
sam_api.obtener_descripcion = _real_desc

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
print("RESULTADO: /pdf responde siempre y entrega el documento")
print("=" * 76)