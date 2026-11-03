# -*- coding: utf-8 -*-
"""
test_fase2.py - Fase 2: direccion real en el Ship-To y etiqueta de envio.

QUE SE COMPRUEBA

1) _dato_plano() desenvuelve la forma de OBJETO con la que SAM.gov publica
   ciudad, estado y pais ({"name": "Norfolk"}, {"code": "IL", ...}), sin
   colar el repr de Python en el papel.
2) El Ship-To de la orden trae la direccion FISICA completa (instalacion,
   calle, ciudad, estado y ZIP) extraida de los metadatos del aviso, y nunca
   un destino generico ("USA") ni un dict.
3) lugar_entrega deja de colapsar a "UNITED STATES".
4) La etiqueta de envio (label.py) es un documento INDEPENDIENTE, en
   ingles, con Shipper of Record = L.A.M.B. Logistics LLC (con UEI y
   NCAGE), Consignee = la direccion exacta de la base/agencia, y
   referencias cruzadas: solicitud, CLIN y unidades.
5) Retrocompatibilidad: placeOfPerformance en forma de texto sigue saliendo
   identico, y la orden de compra sigue teniendo 2 paginas.

POR QUE EXISTE ESTE ARREGLO

La mitad de los avisos trae los campos de lugar como OBJETO y no como
texto. Con str(v) el Ship-To salia asi:

    {'name': 'Norfolk'}, {'code': 'VA', 'name': 'Virginia'} 61299

Eso no es una direccion: es el rastro de la maquina que la compuso. En un
documento de envio, un transportista no puede entregar a un dict, y el
lugar de entrega se quedaba en "UNITED STATES", que no es un destino sino
la ausencia de uno.
"""
import io
import os
import re
import sys
import tempfile
import types

DB = os.path.join(tempfile.gettempdir(), "kyo_test_fase2.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "x"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"
os.environ["DRY_RUN"] = "1"
os.environ["ETIQUETAS_DIR"] = os.path.join(tempfile.gettempdir(),
                                           "etiquetas_fase2")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print("  OK    %s" % nombre)
    else:
        FALLA += 1
        print("  FALLA %s %s" % (nombre, extra))


# ---------------------------------------------------------------- Gemini
# Mismo stub que el resto de la suite: sin red, sin cuota, sin sorpresas.
_STUB = types.ModuleType("google.genai")


class _RespGemini:
    text = ""


class _Modelos:
    def generate_content(self, *a, **k):
        return _RespGemini()


class _ClienteGemini:
    models = _Modelos()


_STUB.Client = lambda api_key=None: _ClienteGemini()
_google = types.ModuleType("google")
_google.genai = _STUB
sys.modules["google"] = _google
sys.modules["google.genai"] = _STUB
_tipos = types.ModuleType("google.genai.types")
_tipos.GenerateContentConfig = lambda **k: k
_tipos.ThinkingConfig = lambda **k: k
sys.modules["google.genai.types"] = _tipos

import pdf_generator as pdfg
import label
import sam_api
import store

store.init_db()

_DESC = ("Line Item Number 0001: NSN 3110-01-586-4931, CUI 0001XK1234, "
         "Cage 12345. Part Number M7A1-100. Quantity: 100 EA. "
         "Unit of Issue EA. Description: BEARING, INSERT, S-1 SERIES, "
         "PER DRAWING 1110700278.")
_real_desc = sam_api.obtener_descripcion
sam_api.obtener_descripcion = lambda nid, opp=None: _DESC

# Un aviso con la forma REAL de SAM.gov: ciudad, estado y pais como objeto.
AVISO = {
    "noticeId": "fase2-1234567890",
    "solicitationNumber": "W912DY-26-R-0042",
    "title": "BEARING, INSERT, S-1 SERIES",
    "fullParentPathName": "DEPT OF DEFENSE.ARMY",
    "typeOfSetAsideDescription": "Total Small Business Set-Aside",
    "naicsCode": "332999",
    "classificationCode": "3110",
    "postedDate": "2026-10-20 09:00:00",
    "responseDeadLine": "2026-11-30T17:00:00-05:00",
    "placeOfPerformance": {
        "name": "Rock Island Arsenal",
        "streetAddress": "1500 E Rock Island Arsenal Rd",
        "city": {"name": "Rock Island"},
        "state": {"code": "IL", "name": "Illinois"},
        "zip": "61299",
        "country": {"code": "USA", "name": "UNITED STATES"},
    },
}

# La misma, solo con el pais: el caso en que NO hay direccion que pintar.
AVISO_SOLO_PAIS = dict(AVISO, placeOfPerformance={"country": {"code": "USA"}})

# Y la forma de TEXTO, que es la que traen la mayoria de los tests viejos.
AVISO_TEXTO = dict(AVISO, placeOfPerformance={
    "city": "Philadelphia", "state": "Pennsylvania"})


def _aviso_con_desc(aviso):
    try:
        return pdfg.desde_aviso(aviso)
    finally:
        pass


print("=" * 76)
print("1) _dato_plano: los objetos de SAM.gov, campo por campo")
print("=" * 76)
casos = [
    ("texto plano", "Groton", "Groton"),
    ("objeto con name", {"name": "Norfolk"}, "Norfolk"),
    ("objeto con code y name", {"code": "IL", "name": "Illinois"}, "Illinois"),
    ("objeto solo code", {"code": "VA"}, "VA"),
    ("zip numerico", 61299, "61299"),
    ("lista de lineas", [{"name": "A"}, {"name": "B"}], "A"),
    ("None", None, ""),
    ("objeto sin claves", {"foo": "bar"}, ""),
    ("cadena vacia", "   ", ""),
]
for etiqueta, entrada, esperado in casos:
    salida = pdfg._dato_plano(entrada)
    check("  %-24s -> %r" % (etiqueta, salida), salida == esperado,
          "-> esperado %r" % esperado)

print()
print("  Y nunca deja el repr de Python:")
for roto in ({"name": "Norfolk"}, {"code": "IL", "name": "Illinois"},
             {"code": "USA", "name": "UNITED STATES"}):
    check("  sin repr en %r" % roto, "'" not in pdfg._dato_plano(roto),
          "-> %r" % pdfg._dato_plano(roto))

print()
print("=" * 76)
print("2) LA DIRECCION FISICA DEL AVISO LLEGA ENTERA AL PIPELINE")
print("=" * 76)
dir_full = pdfg._direccion_completa(AVISO)
print("  direccion_entrega =", dir_full)
for campo, esperado in (("nombre", "Rock Island Arsenal"),
                        ("calle", "1500 E Rock Island Arsenal Rd"),
                        ("ciudad", "Rock Island"),
                        ("estado", "Illinois"),
                        ("zip", "61299")):
    check("  trae %s" % campo, dir_full.get(campo) == esperado,
          "-> %r" % dir_full.get(campo))
check("  el pais se normaliza a UNITED STATES",
      dir_full.get("pais") == "UNITED STATES", "-> %r" % dir_full.get("pais"))
check("  y sin un solo repr de dict",
      not any("'" in str(v) for v in dir_full.values()),
      "-> %r" % dir_full)

print()
print("  lugar_entrega, que alimenta las instrucciones:")
lugar = pdfg.lugar_de_entrega_aviso(AVISO)
print("   ", repr(lugar))
check("  es la ciudad y el estado reales, no el pais",
      lugar.startswith("Rock Island, Illinois"), "-> %r" % lugar)
check("  cierra en UNITED STATES", lugar.endswith("UNITED STATES"),
      "-> %r" % lugar)
check("  y no se queda en el generico",
      lugar.strip().upper() not in ("UNITED STATES", "USA"), "-> %r" % lugar)
check("  sin repr de dict", "'" not in lugar, "-> %r" % lugar)

print()
print("=" * 76)
print("3) LA REGLA DURA SIGUE EN PIE: SOLO EL PAIS NO ES DIRECCION")
print("=" * 76)
check("  _direccion_completa con solo el pais devuelve {}",
      pdfg._direccion_completa(AVISO_SOLO_PAIS) == {},
      "-> %r" % pdfg._direccion_completa(AVISO_SOLO_PAIS))
check("  y lugar_de_entrega_aviso, tambien",
      pdfg.lugar_de_entrega_aviso(AVISO_SOLO_PAIS) == "",
      "-> %r" % pdfg.lugar_de_entrega_aviso(AVISO_SOLO_PAIS))

print()
print("=" * 76)
print("4) RETROCOMPATIBILIDAD: LA FORMA DE TEXTO SALE IDENTICA")
print("=" * 76)
dir_txt = pdfg._direccion_completa(AVISO_TEXTO)
check("  ciudad y estado de texto",
      dir_txt.get("ciudad") == "Philadelphia"
      and dir_txt.get("estado") == "Pennsylvania", "-> %r" % dir_txt)
lugar_txt = pdfg.lugar_de_entrega_aviso(AVISO_TEXTO)
check("  lugar con el formato de siempre",
      lugar_txt == "Philadelphia, Pennsylvania, UNITED STATES",
      "-> %r" % lugar_txt)
check("  lineas_direccion sigue componiendo la postal",
      pdfg.lineas_direccion(dir_txt)[-1]
      == "Philadelphia, Pennsylvania, UNITED STATES",
      "-> %r" % pdfg.lineas_direccion(dir_txt))

print()
print("=" * 76)
print("5) EL SHIP-TO DEL PDF: DIRECCION REAL, NUNCA 'USA'")
print("=" * 76)
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

an = _aviso_con_desc(AVISO)
datos = pdfg.desde_analisis(an, {"nombre": "Radwell International"})
pdf = pdfg.generar_po(datos)
print("  PDF: %d bytes" % len(pdf))
check("  se genera", len(pdf) > 5000, "-> %d bytes" % len(pdf))

txt = ""
if PdfReader:
    paginas = PdfReader(io.BytesIO(pdf)).pages
    txt = "\n".join(p.extract_text() or "" for p in paginas)
    check("  la orden sigue teniendo 2 paginas", len(paginas) == 2,
          "-> %d" % len(paginas))
    check("  trae la instalacion destino",
          "Rock Island Arsenal" in txt)
    check("  trae la calle", "1500 E Rock Island Arsenal Rd" in txt)
    check("  trae ciudad, estado y ZIP",
          "Rock Island, Illinois 61299" in txt,
          "-> falta 'Rock Island, Illinois 61299'")
    check("  con UNITED STATES detras", "UNITED STATES" in txt)
    check("  y SIN el repr de Python en todo el documento",
          "{'" not in txt and "'name'" not in txt)
    # Un "USA" SUELTO en una linea no es una direccion, es el generico.
    lineas_usa = [l.strip() for l in txt.splitlines()
                  if l.strip().upper() in ("USA", "UNITED STATES")]
    check("  ninguna linea del documento es solo el pais",
          not lineas_usa, "-> %r" % lineas_usa)

print()
print("=" * 76)
print("6) LA ETIQUETA DE ENVIO: DOCUMENTO INDEPENDIENTE")
print("=" * 76)
ruta = label.generar(an["notice_id"], an)
print("  ruta:", ruta)
check("  devuelve una ruta", bool(ruta))
check("  el fichero existe", bool(ruta) and os.path.exists(ruta))
tam = os.path.getsize(ruta) if ruta and os.path.exists(ruta) else 0
check("  y pesa mas de 2 KB", tam > 2048, "-> %d bytes" % tam)
if ruta:
    with open(ruta, "rb") as f:
        check("  es un PDF valido", f.read(5) == b"%PDF-")
    check("  el nombre lleva la solicitud",
          "W912DY-26-R-0042" in os.path.basename(ruta),
          "-> %s" % os.path.basename(ruta))

txt_e = ""
if ruta and PdfReader:
    pag_e = PdfReader(io.BytesIO(open(ruta, "rb").read())).pages
    txt_e = "\n".join(p.extract_text() or "" for p in pag_e)
    check("  es UNA hoja aparte, no una pagina mas de la orden",
          len(pag_e) == 1, "-> %d" % len(pag_e))

    print()
    print("  Shipper of Record:")
    for lo in ("SHIPPER OF RECORD", "L.A.M.B. Logistics LLC",
               "1209 Mountain Rd. PL. NE", "Albuquerque, NM 87110",
               "+1 (702) 516-6086", "lamblogisticsusa@gmail.com",
               "UEI NEXGNXGF5E38", "NCAGE STYG2"):
        check("    trae %s" % lo, lo in txt_e, "-> falta %r" % lo)
    check("    y NO el remitente viejo",
          "Santiago" not in txt_e and "Kyomoto" not in txt_e,
          "-> sale el remitente antiguo")

    print()
    print("  Consignee:")
    check("    trae la seccion", "CONSIGNEE (SHIP TO)" in txt_e)
    check("    la instalacion exacta", "Rock Island Arsenal" in txt_e)
    check("    la calle exacta", "1500 E Rock Island Arsenal Rd" in txt_e)
    check("    ciudad, estado y ZIP",
          "Rock Island, Illinois 61299" in txt_e,
          "-> falta 'Rock Island, Illinois 61299'")

    print()
    print("  Referencias cruzadas:")
    check("    trae la solicitud", "W912DY-26-R-0042" in txt_e)
    check("    trae el CLIN", re.search(r"\bCLIN\b", txt_e) is not None)
    check("    con su numero", "0001" in txt_e)
    check("    trae las unidades", "100 EA" in txt_e)
    check("    hueco para los bultos, que el aviso no trae",
          "Packages" in txt_e)
    check("    y la fecha de entrega",
          "November 30, 2026" in txt_e)

    print()
    print("  Zero-Spanish: la etiqueta va a un transportista de EE.UU.")
    for mal in ("ETIQUETA DE ENVIO", "ENTREGAR A", "DESDE (ORIGEN)",
                "DATOS DEL CONTRATO", "CONTENIDO", "DECLARACION DE VALOR"):
        check("    no sale %r" % mal, mal not in txt_e,
              "-> esta en la etiqueta")
    check("    secciones en ingles",
          "CONTENTS" in txt_e and "REFERENCES" in txt_e)

    print()
    print("  Sin datos inventados:")
    correos = set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", txt_e))
    check("    un solo correo, el de la casa",
          correos <= {pdfg.EMAIL}, "-> %r" % correos)
    telefonos = set(re.findall(r"\+1 \(\d{3}\) \d{3}-\d{4}", txt_e))
    check("    un solo telefono, el de la casa",
          telefonos <= {pdfg.TELEFONO}, "-> %r" % telefonos)

print()
print("=" * 76)
print("7) SIN DIRECCION: HUECO HONESTO, NUNCA 'USA' COMO DESTINO")
print("=" * 76)
an_pais = _aviso_con_desc(AVISO_SOLO_PAIS)
ruta_p = label.generar(an_pais["notice_id"], an_pais)
check("  se genera igualmente", bool(ruta_p) and os.path.exists(ruta_p))
if ruta_p and PdfReader:
    txt_p = "\n".join(p.extract_text() or ""
                      for p in PdfReader(io.BytesIO(
                          open(ruta_p, "rb").read())).pages)
    i = txt_p.find("CONSIGNEE")
    bloque = txt_p[i:i + 200] if i >= 0 else ""
    print("   ", " / ".join(bloque.splitlines()[:3]))
    check("  avisa de que falta la direccion",
          "to be confirmed" in bloque, "-> %r" % bloque)
    check("  y no pinta el pais como destino",
          not any(l.strip().upper() in ("USA", "UNITED STATES")
                  for l in bloque.splitlines()),
          "-> %r" % bloque)

print()
print("=" * 76)
print("8) LA ETIQUETA TOLERA UN ANALISIS VIEJO (SIN direccion_entrega)")
print("=" * 76)
# Los analisis que guarda el barrido vienen de gemini_analyzer y traen
# lugar_entrega con "/" y producto en castellano, pero NO direccion_entrega.
viejo = {
    "notice_id": "viejo-999",
    "solicitation": "W9127N26QA145",
    "producto": "Sellos mecanicos para bomba",
    "lugar_entrega": "Norfolk / VA / USA",
    "limite": "2026-11-30T17:00:00-05:00",
    "unidad_medida": "EA",
    "cantidad_total": 500,
}
ruta_v = label.generar(viejo["notice_id"], viejo)
check("  se genera sin direccion_entrega",
      bool(ruta_v) and os.path.exists(ruta_v))
if ruta_v and PdfReader:
    txt_v = "\n".join(p.extract_text() or ""
                      for p in PdfReader(io.BytesIO(
                          open(ruta_v, "rb").read())).pages)
    check("  usa el lugar grosero, que si es real",
          "Norfolk" in txt_v, "-> no sale Norfolk")
    check("  con las unidades del analisis", "500 EA" in txt_v)
    check("  y el hueco de los bultos", "Packages" in txt_v)

print()
print("=" * 76)
print("9) LA FIRMA PUBLICA NO SE HA MOVIDO")
print("=" * 76)
import inspect
check("  label.generar(notice_id, analisis) sigue igual",
      list(inspect.signature(label.generar).parameters)
      == ["notice_id", "analisis"])
check("  label.nombre_archivo sigue con su formato",
      label.nombre_archivo("123456-2026", "W9127N26QA145")
      == "etiqueta_W9127N26QA145_1234562026.pdf",
      "-> %r" % label.nombre_archivo("123456-2026", "W9127N26QA145"))
check("  generar_po sigue con su firma",
      list(inspect.signature(pdfg.generar_po).parameters) == ["datos",
                                                             "db_path"])
check("  desde_aviso sigue con su firma",
      list(inspect.signature(pdfg.desde_aviso).parameters) == ["aviso",
                                                               "db_path"])
check("  desde_analisis sigue con su firma",
      list(inspect.signature(pdfg.desde_analisis).parameters)
      == ["analisis", "proveedor", "db_path"])

sam_api.obtener_descripcion = _real_desc
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
    except OSError:
        pass

print()
print("=" * 76)
if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: el destino sale real y la etiqueta esta lista para el envio")
