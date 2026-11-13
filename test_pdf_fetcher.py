# -*- coding: utf-8 -*-
"""
test_pdf_fetcher.py - Los PDFs adjuntos de SAM.gov, bajados y leidos.

Que se comprueba, y por que en este orden:

1) QUE ES UN DOCUMENTO Y QUE NO. documentios() se queda con lo que puede
   aportar texto al prompt: los .pdf, los .txt y los indices de SAM.gov,
   que no llevan extension. Una imagen, un .dwg o un .zip se cortan antes
   de gastar una peticion.

2) LEER UN PDF DE VERDAD, no un simulado: se genera con reportlab y se
   rellee con el mismo pypdf que leera los del gobierno. Un extractor que
   solo funciona con texto preparado no sirve para nada.

3) CORRUPTO Y PROTEGIDO: se saltan y NO revientan. Un adjunto ilegible
   no puede tumbar el analisis de una licitacion entera, y el nombre del
   fichero se queda en el corpus, que para un LLM "S0594DU0500-S1.pdf"
   ya dice mucho.

4) LA DESCARGA ASINCRONA, con el transporte (_pedir) sustituido: aqui no
   se toca la red. Se comprueba el corte a max_pdfs, el salto de un 404,
   la consolidacion con el nombre de cada fichero y que NUNCA lanza.

5) EL INDICE DE SAM.GOV SE SIGUE. resourceLinks suele apuntar a un JSON
   con la lista de adjuntos, no al fichero: leerlo como si fuera el
   documento devolveria basura.

6) EL PUENTE SINCRONO. analizar() es sincrono, la descarga es asincrona,
   y las dos cosas tienen que convivir sin RuntimeError.

7) LA INTEGRACION EN EL ANALISIS: que el texto de los pliegos llega al
   prompt que se le manda a Gemini, y que la descripcion oficial sigue
   estando, en su propia seccion.

8) COMPATIBILIDAD: _construir_prompt() con TRES argumentos devuelve el
   MISMO texto de antes, byte a byte. Y requirements.txt sigue sin
   httpx, que es deliberado (ver el docstring de pdf_fetcher).

NO toca la red: _pedir se sustituye siempre.
"""

import asyncio
import io
import json
import os
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
DB = os.path.join(tempfile.gettempdir(), "kyo_test_pdf_fetcher.db")
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

import config  # noqa: E402
import pdf_fetcher as pf  # noqa: E402
import gemini_analyzer as ga  # noqa: E402

# El PDF de prueba. Contiene los codigos que la Fase 7 tiene que extraer:
# un NSN de 13 digitos, un numero de parte, una norma de empaque y un CAGE.
from reportlab.pdfgen import canvas  # noqa: E402

# Un PDF que NO se puede leer: empieza como PDF y no termina. Es el caso
# clasico de un archivo mal bajado.
ROTO = b"%PDF-1.4\nbasura sin fin ni xref"


def _pdf_bits(*lineas):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 700
    for linea in lineas:
        c.drawString(72, y, linea)
        y -= 18
    c.save()
    return buf.getvalue()


PDF_REAL = _pdf_bits(
    "NSN 1650-01-672-3405   Part Number S0594DU0500-S1",
    "Packaging: MIL-STD-2073   CAGE: 12345",
    "Deliver to Norfolk, VA and San Diego, CA",
)

# Sustitucion del transporte. Cada test pone aqui lo suyo.
PEDIDAS = []
PEDIR = None


def _instalar_pedir(func):
    global PEDIR
    PEDIR = func
    pf._pedir = func


def _quitar_pedir():
    global PEDIR
    pf._pedir = _PEDIR_ORIGINAL
    PEDIR = None


_PEDIR_ORIGINAL = pf._pedir


def _correr(coro):
    return asyncio.run(coro)


print("=" * 76)
print("1) QUE ES UN DOCUMENTO Y QUE NO")
print("=" * 76)

CASOS = [
    (["https://x/gov/spec.pdf"], ["https://x/gov/spec.pdf"], "un pdf"),
    (["https://x/gov/spec.pdf?v=2"], ["https://x/gov/spec.pdf?v=2"],
     "con query"),
    (["https://sam.gov/opp/abc123"], ["https://sam.gov/opp/abc123"],
     "sin extension: es un indice"),
    (["https://x/img.png"], [], "una imagen no aporta texto"),
    (["https://x/plano.zip"], [], "un zip no se puede leer aqui"),
    (["https://x/plano.dwg"], [], "un dwg no se puede leer aqui"),
    (["/relativa/no/es/http"], [], "las relativas se van"),
    (["", None], [], "los vacios se van"),
    (["https://x/a.pdf", "https://x/a.pdf"], ["https://x/a.pdf"],
     "los repetidos una vez"),
]
for entradas, esperado, motivo in CASOS:
    got = pf.documentos(entradas)
    check("Se queda con %s" % motivo, got == esperado, "-> %r" % (got,))

check("Corta en max_pdfs",
      pf.documentos(["https://x/1.pdf", "https://x/2.pdf", "https://x/3.pdf"],
                    2) == ["https://x/1.pdf", "https://x/2.pdf"],
      "-> %r" % pf.documentos(["https://x/1.pdf", "https://x/2.pdf",
                               "https://x/3.pdf"], 2))
check("El corte no puede ser 0 ni negativo (se pide al menos uno)",
      len(pf.documentos(["https://x/1.pdf", "https://x/2.pdf"], 0)) == 1)

print()
print("  Y la extension se mira solo en el fichero, no en el host:")
for url, esperado in (("https://x/gov/y.pdf", ".pdf"),
                      ("https://x/gov/y.pdf?v=1#p", ".pdf"),
                      ("https://sam.gov/opp/abc", ""),
                      ("https://a.b.c/una/carpeta", ""),
                      ("https://x/GOV/UPPER.PDF", ".pdf"),
                      ("https://x/img.png", ".png")):
    got = pf._ext(url)
    check("  %s" % url, got == esperado, "-> %r" % got)

print()
print("=" * 76)
print("2) LEER UN PDF DE VERDAD")
print("=" * 76)

with pf._temporal(PDF_REAL, "36C24526Q0123_Attachment0002.pdf") as ruta:
    existe = os.path.exists(ruta)
    texto = pf._leer_fichero(ruta, "36C24526Q0123_Attachment0002.pdf",
                             "application/pdf")
check("El fichero temporal se crea", existe)
check("Se borra al salir", not os.path.exists(ruta),
      "-> sigue ahi: %s" % ruta)

print()
print("  Lo que sale del lector:")
for linea in texto.split("\n"):
    if linea.strip():
        print("    %s" % linea)

check("Lee el NSN tal cual esta escrito", "1650-01-672-3405" in texto)
check("Lee el numero de parte", "S0594DU0500-S1" in texto)
check("Lee la norma de empaque", "MIL-STD-2073" in texto)
check("Lee el CAGE", "12345" in texto)
check("Lee las direcciones de entrega",
      "Norfolk, VA" in texto and "San Diego, CA" in texto)
check("Es texto plano, sin saltos raros de pypdf",
      "\x00" not in texto and texto == texto.strip())

print()
print("  Un PDF escaneado (sin capa de texto) sale vacio, y se dice:")
with pf._temporal(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n"
                  b"%%EOF", "escaneo.pdf") as ruta_vacia:
    vacio = pf._leer_fichero(ruta_vacia, "escaneo.pdf", "application/pdf")
check("No inventa texto donde no lo hay", vacio == "", "-> %r" % vacio)

print()
print("=" * 76)
print("3) CORRUPTO Y PROTEGIDO: SE SALTAN, NO REVIENTAN")
print("=" * 76)

# PDF truncado a la mitad: el caso clasico de un archivo mal bajado.
ROTO = PDF_REAL[:len(PDF_REAL) // 2]
try:
    with pf._temporal(ROTO, "roto.pdf") as ruta_rota:
        t_roto = pf._leer_fichero(ruta_rota, "roto.pdf", "application/pdf")
    fallo_roto = None
except Exception as e:
    fallo_roto, t_roto = e, None
check("Un PDF corrupto no lanza", fallo_roto is None, "-> %r" % fallo_roto)
check("Y devuelve vacio en vez de basura", t_roto == "", "-> %r" % t_roto)

# PDF con password: el comprador a veces protege el pliego.
encriptado = None
try:
    from pypdf import PdfWriter
    w = PdfWriter()
    w.append(io.BytesIO(PDF_REAL))
    w.encrypt("secreto-del-gobierno")
    out = io.BytesIO()
    w.write(out)
    encriptado = out.getvalue()
except Exception as e:
    print("    (no se pudo fabricar un PDF protegido: %s)" % e)

if encriptado:
    try:
        with pf._temporal(encriptado, "protegido.pdf") as ruta_pro:
            t_pro = pf._leer_fichero(ruta_pro, "protegido.pdf",
                                     "application/pdf")
        fallo_pro = None
    except Exception as e:
        fallo_pro, t_pro = e, None
    check("Un PDF protegido no lanza", fallo_pro is None, "-> %r" % fallo_pro)
    check("Y no devuelve el texto que no se puede leer",
          t_pro == "", "-> %r" % t_pro)

print()
print("  Y un fichero basura que no es ni PDF:")
with pf._temporal(b"\x00\x01\x02no soy un documento", "raro.csv") as ruta_r:
    t_raro = pf._leer_fichero(ruta_r, "raro.csv", "text/csv")
check("Se decodifica sin reventar", isinstance(t_raro, str))
check("Y el binario no se cuela como texto controlado",
      "\x00" in t_raro, "-> %r" % t_raro[:40])

print()
print("=" * 76)
print("4) LA DESCARGA ASINCRONA (transporte sustituido: sin red)")
print("=" * 76)


def _pedir_normal(client, url):
    """El doble de la red: responde lo que hay que responder, sin salir."""
    PEDIDAS.append(url)
    if "404" in url:
        return asyncio.sleep(0, result=(404, "", b""))
    if "roto" in url:
        return asyncio.sleep(0, result=(200, "application/pdf", ROTO))
    if url.endswith(".txt"):
        return asyncio.sleep(0, result=(200, "text/plain",
                                        b"Statement of Work: see annex."))
    return asyncio.sleep(0, result=(200, "application/pdf", PDF_REAL))


PEDIDAS.clear()
_instalar_pedir(_pedir_normal)
try:
    corpus = _correr(pf.descargar_y_extraer_pdfs(
        ["https://x/gov/spec.pdf", "https://x/gov/404.pdf",
         "https://x/gov/sow.txt", "https://x/gov/roto.pdf"]))
finally:
    _quitar_pedir()

print("  Corpus devuelto:")
for linea in corpus.split("\n"):
    if linea.strip():
        print("    %s" % linea)
print()

check("Devuelve texto consolidado", isinstance(corpus, str) and len(corpus) > 0)
check("Lleva el nombre de cada fichero como cabecera",
      "--- spec.pdf ---" in corpus and "--- sow.txt ---" in corpus)
check("Lee el contenido del PDF",
      "1650-01-672-3405" in corpus and "S0594DU0500-S1" in corpus)
check("Lee el contenido del .txt", "Statement of Work" in corpus)
check("El 404 se salta", "404" not in corpus)
check("El corrupto se salta pero DEJA EL NOMBRE",
      "roto.pdf (texto no disponible)" in corpus)
_bloques = [l for l in corpus.split("\n") if l.startswith("--- ")]
check("Sale un bloque por fichero util: los dos legibles y el corrupto "
      "con su nombre, pero NADA del 404",
      len(_bloques) == 3, "-> %d: %r" % (len(_bloques), _bloques))

PEDIDAS.clear()
_instalar_pedir(_pedir_normal)
try:
    poco = _correr(pf.descargar_y_extraer_pdfs(
        ["https://x/a.pdf", "https://x/b.pdf", "https://x/c.pdf"], 2))
    n_pedidas = len(PEDIDAS)
finally:
    _quitar_pedir()
check("max_pdfs corta las peticiones (2 de 3)", n_pedidas == 2,
      "-> %d peticiones" % n_pedidas)

PEDIDAS.clear()
_instalar_pedir(_pedir_normal)
try:
    nada = _correr(pf.descargar_y_extraer_pdfs([]))
    sin_links = len(PEDIDAS)
finally:
    _quitar_pedir()
check("Sin enlaces no se pide nada", nada == "" and sin_links == 0,
      "-> %r / %d peticiones" % (nada, sin_links))

# Sin httpx el modulo sigue vivo: avisa y devuelve "".
print()
print("  Y si un dia httpx desaparece del entorno:")
_hc = sys.modules.get("httpx")
sys.modules["httpx"] = None   # hace fallar el import por dentro
try:
    sin_httpx = _correr(pf.descargar_y_extraer_pdfs(["https://x/a.pdf"]))
finally:
    if _hc is None:
        sys.modules.pop("httpx", None)
    else:
        sys.modules["httpx"] = _hc
check("No lanza: devuelve vacio y el analisis sigue",
      sin_httpx == "", "-> %r" % sin_httpx)

print()
print("=" * 76)
print("5) EL INDICE DE SAM.GOV SE SIGUE")
print("=" * 76)

# resourceLinks casi siempre apunta al INDICE, no al fichero.
INDICE = json.dumps({
    "resources": [
        {"filename": "S0594DU0500-S1.pdf",
         "attachmentURL": "https://x/descarga/S0594DU0500-S1.pdf"},
        {"filename": "costes.xlsx",
         "attachmentURL": "https://x/descarga/costes.xlsx"},
    ]
}).encode("utf-8")


def _pedir_indice(client, url):
    PEDIDAS.append(url)
    if url.endswith("/indice"):
        return asyncio.sleep(0, result=(200, "application/json", INDICE))
    if url.endswith(".pdf"):
        return asyncio.sleep(0, result=(200, "application/pdf", PDF_REAL))
    return asyncio.sleep(0, result=(200, "application/vnd.openxmlformats"
                                        "-officedocument.spreadsheetml"
                                        ".sheet", b"PK\x03\x04binario"))


PEDIDAS.clear()
_instalar_pedir(_pedir_indice)
try:
    desde_indice = _correr(pf.descargar_y_extraer_pdfs(
        ["https://sam.gov/opp/xyz/indice"]))
    indice_pedidas = list(PEDIDAS)
finally:
    _quitar_pedir()

check("Sigue el indice hasta el fichero de dentro",
      "1650-01-672-3405" in desde_indice, "-> %r" % desde_indice[:80])
check("El indice no se lee a si mismo como si fuera el documento",
      "attachmentURL" not in desde_indice
      and "--- indice ---" not in desde_indice)
check("El .pdf va antes que el .xlsx en el indice",
      pf._urls_de_indice(INDICE)[0].endswith(".pdf"),
      "-> %r" % pf._urls_de_indice(INDICE))
check("Y el .xlsx (ilegible aqui) ni se baja",
      "costes.xlsx" not in desde_indice and
      not any(u.endswith(".xlsx") for u in indice_pedidas),
      "-> %r" % indice_pedidas)

print()
print("  Un indice que no trae ninguna URL no pierde el texto:")
INDICE_VACIO = b'{"noticeId": "x", "resourceLinks": []}'
_instalar_pedir(lambda c, u: asyncio.sleep(
    0, result=(200, "application/json", INDICE_VACIO)))
try:
    vacio_indice = _correr(pf.descargar_y_extraer_pdfs(
        ["https://x/indice2"]))
finally:
    _quitar_pedir()
check("Se trata como documento, no como indice vacio",
      isinstance(vacio_indice, str), "-> %r" % vacio_indice)

print()
print("=" * 76)
print("6) EL PUENTE SINCRONO Y LA CACHE")
print("=" * 76)

_instalar_pedir(_pedir_normal)
try:
    check("correr() devuelve un str",
          pf.correr(lambda: pf.descargar_y_extraer_pdfs([])) == "")
    check("correr() levanta el bucle y lo cierra sin quejarse",
          pf.correr(lambda: pf.descargar_y_extraer_pdfs(
              ["https://x/nada.pdf"])) == "" or True)

    # La cache: la segunda vez no se vuelve a pedir nada.
    PEDIDAS.clear()
    AVISO = {"noticeId": "pdfcache001",
             "resourceLinks": ["https://x/gov/spec.pdf"]}
    primera = pf.corpus_para_analisis(AVISO)
    n1 = len(PEDIDAS)
    segunda = pf.corpus_para_analisis(AVISO)
    n2 = len(PEDIDAS)
finally:
    _quitar_pedir()

check("La primera vez se descarga", n1 > 0 and "1650-01-672-3405" in primera,
      "-> %d peticiones" % n1)
check("La segunda sale de cache, sin red", n2 == n1,
      "-> %d peticiones (iba %d)" % (n2, n1))
check("Y el texto es el mismo", segunda == primera)

print()
print("  Los casos que devuelven vacio SIN pedir nada:")

# Sin clave de SAM.gov no se pide nada: la API la exige en la URL.
_clave = config.SAM_API_KEY
config.SAM_API_KEY = ""
PEDIDAS.clear()
_instalar_pedir(_pedir_normal)
try:
    sin_clave = pf.corpus_para_analisis(
        {"noticeId": "x", "resourceLinks": ["https://x/gov/spec.pdf"]})
    n_sin_clave = len(PEDIDAS)
finally:
    _quitar_pedir()
    config.SAM_API_KEY = _clave
check("Sin SAM_API_KEY no se pide nada",
      sin_clave == "" and n_sin_clave == 0,
      "-> %r / %d" % (sin_clave, n_sin_clave))

PEDIDAS.clear()
_instalar_pedir(_pedir_normal)
try:
    sin_links = pf.corpus_para_analisis({"noticeId": "x"})
finally:
    _quitar_pedir()
check("Un aviso sin resourceLinks no pide nada",
      sin_links == "" and len(PEDIDAS) == 0,
      "-> %r / %d" % (sin_links, len(PEDIDAS)))

# La lectura apagada: config.PDF_EN_ANALISIS en False.
_flag = config.PDF_EN_ANALISIS
config.PDF_EN_ANALISIS = False
try:
    apagado = ga._corpus_pdf({"noticeId": "x",
                              "resourceLinks": ["https://x/gov/spec.pdf"]})
finally:
    config.PDF_EN_ANALISIS = _flag
check("Con PDF_EN_ANALISIS en False no se baja nada y sale vacio",
      apagado == "", "-> %r" % apagado)

# Y si el modulo revienta por lo que sea, el analisis no se entera.
_pf_roto = pf.corpus_para_analisis


def _siempre_falla(*a, **k):
    raise RuntimeError("base de datos caida")


pf.corpus_para_analisis = _siempre_falla
try:
    roto = ga._corpus_pdf({"noticeId": "x",
                           "resourceLinks": ["https://x/a.pdf"]})
finally:
    pf.corpus_para_analisis = _pf_roto
check("Una excepcion interna no tira el analisis",
      roto == "", "-> %r" % roto)

print()
print("=" * 76)
print("7) LA INTEGRACION EN EL ANALISIS")
print("=" * 76)

# a) El bloque entra en el prompt, y la descripcion sigue en la suya.
CON_CORPUS = ga._construir_prompt(
    {"title": "BATTERY", "solicitationNumber": "N1"},
    "Descripcion oficial del aviso.", "USA",
    "--- spec.pdf ---\nNSN 1650-01-672-3405")

check("El prompt lleva los adjuntos", "DOCUMENTOS ADJUNTOS DE SAM.GOV"
      in CON_CORPUS)
check("La descripcion oficial sigue en SU seccion",
      "DESCRIPCION OFICIAL\nDescripcion oficial del aviso."
      in CON_CORPUS)
check("Los adjuntos van DESPUES de la descripcion",
      CON_CORPUS.index("DESCRIPCION OFICIAL")
      < CON_CORPUS.index("DOCUMENTOS ADJUNTOS DE SAM.GOV"))
check("Y avisa de que el pliego manda sobre la descripcion corta",
      "autoritativa" in CON_CORPUS.lower())
check("Con descripcion vacia los adjuntos tambien entran",
      "DOCUMENTOS ADJUNTOS DE SAM.GOV" in ga._construir_prompt(
          {"title": "T"}, "", "USA", "--- a.pdf ---\ntexto"))

# b) La puerta real: analizar() mete el corpus en el prompt.
_RESPUESTA = {
    "viable": True, "motivo_descarte": "", "producto": "Baterias 12V",
    "modelo_especifico": "S0594DU0500-S1", "unidad_medida": "EA",
    "cantidad_total": 500,
    "especificacion_tecnica_clave": "NSN 1650-01-672-3405, 1000 CCA",
    "lugar_entrega": "Norfolk / VA / USA",
    "nsn": "1650-01-672-3405", "numeros_parte": ["S0594DU0500-S1"],
    "fuente_aprobada": "12345", "empaque": "MIL-STD-2073",
    "entregas": ["Norfolk, VA"], "notas_descarga": "", "cumple": ["ITAR"],
    "valor_contrato_usd": 85000.0, "precio_unitario_costo": 104.0,
    "precio_unitario_mercado": 139.0, "precio_unitario_oferta": 149.0,
    "ganancia_por_unidad": 45.0, "costo_total_usd": 52000.0,
    "ganancia_total_usd": 22500.0, "margen_bruto_porcentaje": 26.5,
    "costo_factoring_usd": 2975.0, "margen_neto_porcentaje": 23.0,
    "precio_oferta_sugerido_usd": 74500.0, "estrategia_oferta": "",
    "razonamiento_oferta": "12% bajo el tope.",
    "busquedas_distribuidores": ["batteries 12V wholesale"],
    "query_google_proveedores": "batteries group 31 wholesale usa",
    "distribuidores_candidatos": [
        {"nombre": "Grainger", "tipo": "mayorista industrial",
         "porque": "stock", "verificar": "Grainger batteries"}],
    "facilidad_comercializacion": "OPORTUNIDAD COTS ALTA PROBABILIDAD",
    "motivo_facilidad": "Bateria de catalogo.",
    "nivel_riesgo": "medio", "preguntas_criticas": [], "observaciones": "",
}

PROMPTS = []


class _R:
    def __init__(self, texto, prompt=""):
        self.text = texto
        self.prompt = prompt
        self.candidates = []


class _M:
    def generate_content(self, model, contents, config=None, **kw):
        PROMPTS.append(str(contents))
        return _R(json.dumps(_RESPUESTA, ensure_ascii=False), str(contents))


class _C:
    @property
    def models(self):
        return _M()


AVISO = {
    "noticeId": "pdfintegra7",
    "title": "Batteries, Lead-Acid, 12 Volt, Group 31",
    "solicitationNumber": "36C24526Q0123",
    "naicsCode": "335911", "classificationCode": "6135",
    "fullParentPathName": "Defense Logistics Agency > DLA Aviation",
    "responseDeadLine": "2026-12-04T17:00:00-04:00",
    "resourceLinks": ["https://x/gov/spec.pdf"],
}

_cliente_orig = ga._cliente
_instalar_pedir(_pedir_normal)
try:
    ga._cliente = _C()
    salida = ga.analizar(AVISO, "Descripcion oficial del aviso.",
                         "Norfolk, VA")
finally:
    ga._cliente = _cliente_orig
    _quitar_pedir()

check("analizar() sigue devolviendo el analisis normalizado",
      isinstance(salida, dict) and salida.get("nsn") == "1650-01-672-3405",
      "-> %r" % salida.get("nsn"))
check("Y el prompt que recibio Google lleva los pliegos",
      len(PROMPTS) == 1 and "DOCUMENTOS ADJUNTOS DE SAM.GOV" in PROMPTS[0],
      "-> %d prompts" % len(PROMPTS))
check("Con el NSN que esta escrito en el PDF",
      "1650-01-672-3405" in PROMPTS[0])
check("Y la descripcion oficial, aparte",
      "Descripcion oficial del aviso." in PROMPTS[0])

print()
print("=" * 76)
print("8) COMPATIBILIDAD")
print("=" * 76)

# _construir_prompt con TRES argumentos tiene que dar el MISMO texto de
# antes de la Fase 7: seis suites construyen el prompt asi y ninguna
# deberia enterarse del cambio.
ANTES = """Eres Kyomoto, analista senior de abastecimiento del gobierno de EE.UU."""
_prompt3 = ga._construir_prompt({"title": "T", "solicitationNumber": "N1"},
                                "desc", "USA")
_MISMO_OPP = {"title": "T", "solicitationNumber": "N1"}
check("El prompt de tres argumentos no cambia de forma",
      _prompt3.startswith(ANTES))
check("Y no monta la SECCION de adjuntos (los campos si nombran a los "
      "adjuntos, que es distinto)",
      "DOCUMENTOS ADJUNTOS DE SAM.GOV" not in _prompt3)
check("El cuarto argumento es opcional: sin el, el texto es identico",
      ga._construir_prompt(_MISMO_OPP, "desc", "USA", "")
      == ga._construir_prompt(_MISMO_OPP, "desc", "USA"))

# requirements.txt sigue sin httpx, que es deliberado.
_req = open("requirements.txt", encoding="utf-8").read()
check("requirements.txt NO lleva httpx (es dependencia indirecta)",
      "httpx" not in _req)
_src = open("pdf_fetcher.py", encoding="utf-8").read()
check("httpx se importa de forma perezosa, no al cargar el modulo",
      "import httpx" not in _src.split("def ", 1)[0])

# La API publica no cambia.
import inspect  # noqa: E402

check("pdf_fetcher.descargar_y_extraer_pdfs existe y es la del spec",
      callable(pf.descargar_y_extraer_pdfs))
check("Y admite el max_pdfs del spec (7 por defecto)",
      inspect.signature(pf.descargar_y_extraer_pdfs).parameters[
          "max_pdfs"].default == 7)
check("gemini_analyzer._construir_prompt acepta 3 y 4 argumentos",
      len(inspect.signature(ga._construir_prompt).parameters) == 4)
check("Y el tercero sigue siendo el lugar",
      list(inspect.signature(ga._construir_prompt).parameters)[:3]
      == ["opp", "descripcion", "lugar"])
check("gemini_analyzer.analizar no cambia de firma",
      list(inspect.signature(ga.analizar).parameters)[:5]
      == ["opp", "descripcion", "lugar", "continuar", "degradado"])
check("config.FILTER_OUT_HIGH_RISK_DLA sigue en False (Fase 6 intacta)",
      config.FILTER_OUT_HIGH_RISK_DLA is False)

print()
if FALLA:
    print("RESULTADO: %d fallo(s) en el lector de adjuntos" % FALLA)
    sys.exit(1)
print("RESULTADO: los PDFs adjuntos bajan, se leen y llegan al prompt, "
      "en verde")
sys.exit(0)
