# -*- coding: utf-8 -*-
"""
test_pliego.py - El pliego largo se baja, se limpia y se cachea.

La Fase 9 existe porque el motor de decision solo veia 3.000 caracteres:
las clausulas que mandan en un pliego (SUBSAFE, Nivel I, radiografia,
los datos DI) estan casi siempre mas abajo, en el HTML descargado o en un
adjunto .docx. Esta suite comprueba las tres cosas que hacen que eso
deje de ser un problema:

  1. LIMPIEZA: el HTML y el DOCX se convierten en texto conservando la
     numeracion de clausulas, las viñetas y los saltos, y sin romper
     identificadores. `MIL-STD-2073` tiene que salir entero aunque el
     fuente lo parta en dos lineas.
  2. INGESTA: la descripcion larga se baja solo cuando hace falta, se
     guarda en SQLite por (noticeId, fecha de actualizacion), y un aviso
     enmendado se vuelve a pedir mientras que una misma version no.
  3. RED: ningun test toca la red del gobierno. La bajada se comprueba
     con la API de SAM.gov sustituida por un doble, que es la unica forma
     de probar la logica de cache sin depender de que un servicio externo
     conteste hoy.

Doble modo: se puede correr con pytest o con `python test_pliego.py`.
"""
import io
import os
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

# Hay que fijar el entorno ANTES de importar config: config lee el
# entorno una sola vez, en el import, y sin esto el test escribiria en
# la base del usuario.
_TMP = tempfile.mkdtemp(prefix="kyomoto_pliego_")
os.environ["DB_PATH"] = os.path.join(_TMP, "licitaciones.db")
os.environ.setdefault("GEMINI_API_KEY", "x")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "123456789")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

import adjuntos  # noqa: E402
import config  # noqa: E402
import pdf_fetcher  # noqa: E402
import pliego  # noqa: E402
import sam_api  # noqa: E402
import store  # noqa: E402


# --------------------------------------------------------------------------
# Material
# --------------------------------------------------------------------------
def _docx(parrafos, saltos=False) -> bytes:
    """Un .docx de verdad, montado con zipfile, para no depender de nada."""
    cuerpo = ""
    for p in parrafos:
        cuerpo += '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % escape(p)
    if saltos:
        cuerpo = ('<w:p><w:r><w:t>Linea uno</w:t></w:r>'
                  '<w:r><w:br/></w:r><w:r><w:t>Linea dos</w:t></w:r></w:p>')
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main"><w:body>%s</w:body></w:document>'
           % cuerpo)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


# Un pliego largo de verdad: varias secciones, CDRLs en lista, numeracion
# de clausulas, enmiendas y los identificadores que NO se pueden romper.
PLIEGO_LARGO = """<!DOCTYPE html>
<html><head><title>Notice</title>
<style type="text/css">body{font-family:Arial}</style>
<script>alert("no me leas")</script>
</head><body>
<h1>SECTION 1.0  SCOPE OF WORK</h1>
<p>The Contractor shall furnish <b>100 EA</b> of rotary shaft seals,
part number <b>N0010426QFF16</b>, under this contract.</p>
<p>Material shall conform to <b>ASTM-B505</b> and dimensional
tolerances per <b>DI-MISC-80678</b>.</p>
<!-- comentario interno del portal -->
<h2>SECTION 2.0  QUALITY REQUIREMENTS</h2>
<ul>
  <li>DI-MISC-81020 data item shall be submitted with each shipment.</li>
  <li>DI-QCIC-81110 shall be furnished prior to delivery.</li>
  <li>SUBSAFE material is required for all wetted parts.</li>
  <li>Level I inspection shall be performed at the plant.</li>
  <li>Repair welding is not Permitted.</li>
  <li>Radiography of all butt welds shall be performed per MIL-STD-2073.</li>
</ul>
<h2>SECTION 3.0  PACKAGING</h2>
<p>Packaging shall conform to MIL-STD-129 and military packaging
requirements.</p>
<p>The Contracting Officer will dissolve the small business set aside
if no qualified small business responds.</p>
<h2>SECTION 4.0  DELIVERY</h2>
<p>Delivery 45 days after receipt of order. FOB Destination,
inspection at destination.</p>
<h2>AMENDMENT 001</h2>
<p>The due date is extended to 20 December 2026.</p>
<h2>AMENDMENT 002</h2>
<p>The quantity is changed to 100 EA.</p>
</body></html>"""


# --------------------------------------------------------------------------
# 1) Limpieza del HTML
# --------------------------------------------------------------------------
def test_html_conserva_estructura_e_identificadores():
    t = pliego.de_html_a_texto(PLIEGO_LARGO)

    # La estructura: secciones, lista con viñetas y saltos de parrafo.
    assert "SECTION 1.0" in t and "SECTION 4.0" in t
    assert "\n- DI-MISC-81020" in t
    assert t.count("\n- ") == 6, t
    # Los identificadores, ENTEROS.
    for token in ("N0010426QFF16", "ASTM-B505", "DI-MISC-80678",
                  "MIL-STD-2073", "MIL-STD-129"):
        assert token in t, token
    # Ni el script ni los comentarios se cuelan al modelo.
    assert "alert" not in t and "font-family" not in t
    assert "comentario interno" not in t
    # Los `<p>` no se funden: los parrafos van en su propia linea.
    assert "contract.\n" in t
    # Los espacios repetidos de la fuente quedan normalizados.
    assert "  " not in t.split("\n- ")[0]


def test_html_sin_etiquetas_no_se_come_texto():
    # `temp < 40 y > 20` NO es una etiqueta: si de_html_a_texto tratara
    # cualquier `<...>` como tag, el modelo veria "temp 40 y 20".
    assert "temp < 40 y > 20" in pliego.de_html_a_texto(
        "<p>Keep at temp < 40 y > 20</p>")


def test_html_vacio_y_basura_no_lanzan():
    assert pliego.de_html_a_texto("") == ""
    assert pliego.de_html_a_texto(None) == ""
    assert pliego.de_html_a_texto("<html><body></body></html>") == ""


# --------------------------------------------------------------------------
# 2) Normalizacion e identificadores
# --------------------------------------------------------------------------
def test_normalizar_junta_espacios_sin_romper_identificadores():
    t = pliego.normalizar("N0010426QFF16   es    MIL-STD-2073")
    assert t == "N0010426QFF16 es MIL-STD-2073"


def test_normalizar_cose_el_identificador_partido_por_el_fuente():
    # El corte de linea de MITAD del token es lo unico que rompe un
    # identificador en un HTML. Juntar lineas a ciegas en cambio borraria
    # los parrafos, y eso si es destructivo.
    assert pliego.normalizar("MIL-STD-\n2073") == "MIL-STD-2073"
    assert pliego.normalizar("DI-MISC-\n 80678") == "DI-MISC-80678"
    assert pliego.normalizar("ASTM-\nB505") == "ASTM-B505"


def test_normalizar_conserva_los_saltos_y_recorta_los_vacios():
    t = pliego.normalizar("a\n\n\n\n\nb")
    assert t == "a\n\nb"


# --------------------------------------------------------------------------
# 3) DOCX con la stdlib
# --------------------------------------------------------------------------
def test_docx_extrae_el_texto():
    bits = _docx(["SECTION 1.0 SCOPE", "100 EA de sellos N0010426QFF16",
                  "Empaque MIL-STD-129"])
    t = pliego.de_docx_a_texto(bits)
    assert "SECTION 1.0 SCOPE" in t
    assert "N0010426QFF16" in t
    assert "MIL-STD-129" in t
    assert "\n" in t


def test_docx_con_saltos_de_linea_internos():
    assert pliego.de_docx_a_texto(_docx([], saltos=True)) == "Linea uno\nLinea dos"


def test_docx_invalido_devuelve_vacio_y_no_lanza():
    assert pliego.de_docx_a_texto(b"") == ""
    assert pliego.de_docx_a_texto(b"esto no es un zip") == ""
    # Un .doc heredado no tiene word/document.xml.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("otro.xml", "<x/>")
    assert pliego.de_docx_a_texto(buf.getvalue()) == ""


# --------------------------------------------------------------------------
# 4) Despacho por fichero y texto pegado
# --------------------------------------------------------------------------
def test_de_archivo_despacha_por_contenido():
    assert "N0010426QFF16" in pliego.de_archivo(
        _docx(["N0010426QFF16"]), "pliego.docx")
    assert "parrafo" in pliego.de_archivo(
        b"<p>un parrafo</p>", "index.htm")
    assert "plano" in pliego.de_archivo(b"texto plano", "notas.txt")
    assert pliego.de_archivo(b"", "x.docx") == ""


def test_texto_pegado_distingue_html_de_texto_plano():
    assert pliego.texto_pegado("<p>QTY &lt; 100 EA</p>") == "QTY < 100 EA"
    assert pliego.texto_pegado("QTY < 100 EA") == "QTY < 100 EA"
    assert pliego.texto_pegado("") == ""


# --------------------------------------------------------------------------
# 5) El delegado en sam_api
# --------------------------------------------------------------------------
def test_limpiar_html_delega_y_conserva_los_parrafos():
    t = sam_api.limpiar_html("<p>uno</p><p>dos</p>")
    # Cada parrafo en su propia linea, con su hueco: fundirlos en un
    # parrafo largo seria perder la estructura del pliego.
    assert t == "uno\n\ndos", repr(t)
    # El markdown de ** de noticedesc sigue quitandose.
    assert sam_api.limpiar_html("**negrita**") == "negrita"
    # Texto plano sin etiquetas: no se come nada.
    assert sam_api.limpiar_html("temp < 40 y > 20") == "temp < 40 y > 20"


# --------------------------------------------------------------------------
# 6) Los adjuntos: HTML y DOCX entran en el corpus
# --------------------------------------------------------------------------
def test_adjuntos_lee_html_y_docx():
    html = adjuntos._texto_de_recurso(
        b"<p>100 EA de sellos N0010426QFF16</p>", "spec.html")
    assert "N0010426QFF16" in html and "<p>" not in html

    docx = adjuntos._texto_de_recurso(
        _docx(["DI-MISC-80678 obligatorio"]), "pliego.docx")
    assert "DI-MISC-80678" in docx

    # Los que antes se descartaban siguen descartandose.
    assert adjuntos._texto_de_recurso(b"x", "D.dwg") == ""
    assert adjuntos._texto_de_recurso(b"hola", "notas.txt") == "hola"
    # ...y el .docx dejo de ser uno de ellos.
    assert ".docx" not in adjuntos._EXT_PEOR
    assert ".docx" in adjuntos._EXT_RICO


def test_pdf_fetcher_acepta_el_docx():
    assert ".docx" in pdf_fetcher._DOC
    ruta = os.path.join(_TMP, "spec.docx")
    with open(ruta, "wb") as f:
        f.write(_docx(["NSN 6140-01-234-5678"]))
    assert "6140-01-234-5678" in pdf_fetcher._leer_fichero(
        ruta, "spec.docx", "application/octet-stream")
    # Un .dwg sigue sin aportar nada.
    assert ".dwg" not in pdf_fetcher._DOC
    assert pdf_fetcher.documentos(["https://x/spec.docx"], 5) == [
        "https://x/spec.docx"]


# --------------------------------------------------------------------------
# 7) La bajada: cache por noticeId + fecha de actualizacion
# --------------------------------------------------------------------------
def test_pliego_largo_apagado_no_toca_la_red(monkeypatch):
    monkeypatch.setattr(config, "PLIEGO_LARGO_EN_ANALISIS", False)
    monkeypatch.setattr(sam_api, "obtener_descripcion_larga",
                        lambda *a, **k: pytest.fail("no deberia llamar a SAM"))
    assert pliego.texto_largo("N-1", {"updateDate": "2026-11-01"}) == ""


def test_pliego_largo_sin_notice_id_no_toca_la_red(monkeypatch):
    monkeypatch.setattr(config, "PLIEGO_LARGO_EN_ANALISIS", True)
    monkeypatch.setattr(sam_api, "obtener_descripcion_larga",
                        lambda *a, **k: pytest.fail("no deberia llamar a SAM"))
    assert pliego.texto_largo("", {"updateDate": "2026-11-01"}) == ""
    assert pliego.texto_largo(None) == ""


def test_pliego_largo_se_guarda_por_version(monkeypatch):
    """Se paga UNA vez por version; una misma version no se vuelve a pedir."""
    llamadas = []

    def doble(nid, opp=None):
        llamadas.append((nid, pliego.fecha_actualizacion(opp)))
        return "TEXTO LARGO DE SAM.GOV"

    monkeypatch.setattr(config, "PLIEGO_LARGO_EN_ANALISIS", True)
    monkeypatch.setattr(sam_api, "obtener_descripcion_larga", doble)

    nid = "PLIEGO-%d" % id(doble)
    v1 = {"updateDate": "2026-11-01T00:00:00"}

    # La primera vez se baja y se guarda.
    assert pliego.texto_largo(nid, v1) == "TEXTO LARGO DE SAM.GOV"
    assert len(llamadas) == 1

    # La misma version NO se vuelve a pedir: sale del cache.
    assert pliego.texto_largo(nid, v1) == "TEXTO LARGO DE SAM.GOV"
    assert len(llamadas) == 1, "una misma version no debe costar dos peticiones"

    # El aviso se enmendo: cambia la fecha, cambia el texto, se vuelve a
    # pedir. Un cache que no se invalidara describiria un pliego que el
    # comprador ya retoco.
    v2 = {"updateDate": "2026-11-09T00:00:00"}
    assert pliego.texto_largo(nid, v2) == "TEXTO LARGO DE SAM.GOV"
    assert len(llamadas) == 2 and llamadas[1][1] == "2026-11-09T00:00:00"


def test_pliego_largo_falla_sin_crear_ruido(monkeypatch):
    """La API caida devuelve "" y no lanza: el escaneo no se frena."""
    def roto(nid, opp=None):
        raise RuntimeError("404")

    monkeypatch.setattr(config, "PLIEGO_LARGO_EN_ANALISIS", True)
    monkeypatch.setattr(sam_api, "obtener_descripcion_larga", roto)
    assert pliego.texto_largo("PLIEGO-ROTO", {"updateDate": "2026-11-01"}) == ""


def test_cache_pliego_distingue_lo_vacio_de_lo_no_preguntado():
    nid = "CACHE-VACIA-%d" % os.getpid()
    assert store.leer_pliego(nid, "2026-11-01") is None      # no se ha mirado
    store.cache_pliego(nid, "2026-11-01", "")
    assert store.leer_pliego(nid, "2026-11-01") == ""        # mirado y vacio
    assert store.pliego_consultado(nid, "2026-11-01") is True
    assert store.leer_pliego(nid, "2026-11-02") is None      # otra version


def test_fecha_actualizacion_toma_el_update_date():
    assert pliego.fecha_actualizacion(
        {"updateDate": "2026-11-09", "postedDate": "2026-11-01"}) == "2026-11-09"
    assert pliego.fecha_actualizacion({"postedDate": "2026-11-01"}) == "2026-11-01"
    assert pliego.fecha_actualizacion({}) == ""
    assert pliego.fecha_actualizacion(None) == ""


if __name__ == "__main__":
    codigo = pytest.main(["-q", "--no-header", __file__])
    print()
    print("RESULTADO: " + ("el pliego largo se baja, se limpia y se cachea, "
                          "en verde"
                          if codigo == 0 else "FALLA - ver arriba"))
    sys.exit(int(codigo))
