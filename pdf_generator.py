"""
pdf_generator.py - Purchase Order / Vendor Invoice Request en PDF.

QUE ES ESTE DOCUMENTO

Una orden de compra que emite L.A.M.B. Logistics LLC a un distribuidor. Es
el documento que se le manda al proveedor para pedirle la mercancia que
despues se revende al gobierno de EE.UU.

Lo emite la empresa, lo firma su CEO, y va con los datos de L.A.M.B. No
pretende ser ningun documento oficial: no lleva sello, ni emblema, ni membrete
gubernamental, ni numeracion de agencia. El numero de PO lo genera L.A.M.B.
con su propio prefijo (LAMB-). Es una orden de compra privada, que es
exactamente lo que necesita una empresa que revende material para poder
comprarselo a su distribuidor.

REQUISITOS QUE PIDIO EL USUARIO

  Encabezado superior derecha : logo pequeno de la empresa
  Encabezado superior izquierda: L.A.M.B. Logistics LLC
                                1209 Mountain Rd. PL. NE, Ste. H
                                Albuquerque, NM 87110
  Cuerpo                      : numero de PO, referencia SAM.gov, tabla con
                                descripcion tecnica, cantidades,
                                especificaciones e instrucciones de despacho
  Pie de pagina                : Bastian Cordero, CEO

SOBRE EL LOGO

El logo se busca en assets/logo_lamb.png, que es configurable con la variable
LOGO_PATH. Si el fichero no esta, se dibuja un marcador de posicion con
reportlab, para que el PDF nunca falle por eso: es preferible un PDF con un
marca de agua a un PDF que no se genera.

EL NUMERO DE LO GENERA desde_analisis(), no generar_po()

    Asi el documento y el nombre del fichero salen del MISMO numero. Si se
    generara dentro de generar_po(), el fichero se llamaria siempre
    "PO.pdf" y no habria forma de distinguir dos ordenes.

COMO GENERA EL NUMERO DE PO

    LAMB-YYYYMMDD-####

El correlativo se guarda en la base de datos, no en memoria, porque en Render
el proceso se reinicia y si el contador viviera en una variable volveria a 1
con cada deploy, y dos purchase orders con el mismo numero en el mismo
distribuidor es un problema de verdad al conciliar.
"""
from __future__ import annotations

import io
import logging
import re
import os
import sqlite3
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)

import config

# --- Datos de la empresa -------------------------------------------------
log = logging.getLogger("kyomoto.pdf")

EMPRESA = "L.A.M.B. Logistics LLC"
DIRECCION = ["1209 Mountain Rd. PL. NE, Ste. H", "Albuquerque, NM 87110"]
FIRMANTE = "Bastian Cordero"
CARGO_FIRMANTE = "CEO"
EMAIL = "lamblogisticsusa@gmail.com"
TELEFONO = "+1 (702) 516-6086"

# Identificadores oficiales ante el gobierno de EE.UU.
#
# UEI (Unique Entity Identifier) y NCAGE (Commercial and Government Entity code).
# En un Small Business Set-Aside el UEI no es decorativo: es con el que se
# comprueba que la empresa existe en SAM.gov y que su NAICS encaja con el del
# contrato. Sin el, el documento no identifica a nadie.
UEI = "NEXGNXGF5E38"
NCAGE = "STYG2"

# Colores de la marca. El azul del logo es #1B3B6F y el gris #5B6770.
AZUL = colors.HexColor("#1B3B6F")
GRIS = colors.HexColor("#5B6770")
GRIS_CLARO = colors.HexColor("#E8EBEF")
GRIS_LINEA = colors.HexColor("#C9CFD6")

LOGO_BUSCADOS = [
    os.environ.get("LOGO_PATH", ""),
    os.path.join(os.path.dirname(__file__), "assets", "logo_lamb.png"),
    os.path.join(os.path.dirname(__file__), "assets", "logo.png"),
]

RUTINO_ETIQUETAS = {
    "fontName": "Helvetica",
    "fontSize": 8.5,
    "leading": 11,
    "textColor": colors.black,
}


# ======================================================================
#  Numero de PO
# ======================================================================
def siguiente_numero_po(db_path: str | None = None) -> str:
    """
    Correlativo persistente: LAMB-AAAAMMDD-NNNN.

    Va en SQLite y no en memoria porque en Render el proceso se reinicia con
    cada deploy. Con un contador en memoria volveria a 0001, y dos purchase
    orders con el mismo numero para el mismo distribuidor no se pueden
    conciliar.
    """
    ruta = db_path or config.DB_PATH
    hoy = datetime.now(timezone.utc).strftime("%Y%m%d")
    conn = sqlite3.connect(ruta)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS contador_po ("
            "  fecha TEXT PRIMARY KEY,"
            "  ultimo INTEGER NOT NULL DEFAULT 0)"
        )
        fila = conn.execute(
            "SELECT ultimo FROM contador_po WHERE fecha = ?", (hoy,)
        ).fetchone()
        n = (fila[0] if fila else 0) + 1
        conn.execute(
            "INSERT OR REPLACE INTO contador_po (fecha, ultimo) VALUES (?,?)",
            (hoy, n),
        )
        conn.commit()
    finally:
        conn.close()
    return f"LAMB-{hoy}-{n:04d}"


# ======================================================================
#  Logo
# ======================================================================
def _ruta_logo() -> str:
    for ruta in LOGO_BUSCADOS:
        if ruta and os.path.isfile(ruta):
            return ruta
    return ""


# El hueco que se le reserva al logo. Lo USA la cabecera para no pasar por
# debajo: medido, la cabecera llegaba a 6,30 pulgadas y el logo arrancaba en
# 5,90, y se solapaban 1,15.
_ANCHO_LOGO_RESERVADO = 2.00 * inch


def _encabezado_logo(canvas, doc, ancho: float) -> None:
    """
    Logo arriba a la derecha.

    Si el fichero no existe se dibuja un marcador con las iniciales, para
    que el PDF se genere igual. Un PDF que sale con el logo placeholder es
    utilizable; un PDF que no se genera por falta de una imagen, no.
    """
    ruta = _ruta_logo()
    alto = 0.72 * inch
    ancho_max = 1.85 * inch
    x = ancho - config.PDF_MARGEN_DERECHO - ancho_max
    y = doc.height - config.PDF_MARGEN_SUPERIOR - alto + 6

    if ruta:
        try:
            from reportlab.lib.utils import ImageReader

            img = ImageReader(ruta)
            iw, ih = img.getSize()
            # Se escala en un solo eje para no deformar el logo, que tiene
            # su propia proporcion.
            if iw >= ih:
                w = min(ancho_max, alto * iw / ih)
                h = w * ih / iw
            else:
                h = alto
                w = h * iw / ih
            canvas.drawImage(img, x + (ancho_max - w) / 2, y, w, h,
                             preserveAspectRatio=True, anchor="n")
            return
        except Exception:
            # Un logo corrupto no puede tumbar la orden de compra.
            pass

    # Marcador de posicion.
    canvas.saveState()
    canvas.setStrokeColor(GRIS_LINEA)
    canvas.setLineWidth(0.8)
    canvas.setDash(2, 2)
    canvas.rect(x, y, ancho_max, alto)
    canvas.setDash()
    canvas.setFillColor(AZUL)
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawCentredString(x + ancho_max / 2, y + alto / 2 + 2, "L.A.M.B.")
    canvas.setFillColor(GRIS)
    canvas.setFont("Helvetica", 6.5)
    canvas.drawCentredString(x + ancho_max / 2, y + alto / 2 - 12, "LOGO")
    canvas.restoreState()


# ======================================================================
#  Estilos
# ======================================================================
def _int_unidades(texto) -> int:
    """Un entero desde "24 bearings" o "100 each". 0 si no hay uno claro."""
    try:
        import lote_consolidado as _lc_u
        return _lc_u._unidades_numericas(texto)
    except Exception:
        return 0


def _estilos() -> dict:
    e = {}
    e["empresa"] = ParagraphStyle("empresa", fontName="Helvetica-Bold",
                                  fontSize=15, leading=18, textColor=AZUL)
    e["dir"] = ParagraphStyle("dir", fontName="Helvetica", fontSize=9,
                              leading=12.5, textColor=GRIS)
    e["titulo"] = ParagraphStyle("titulo", fontName="Helvetica-Bold",
                                 fontSize=15, leading=19, textColor=AZUL,
                                 alignment=TA_CENTER, spaceAfter=2)
    e["subtitulo"] = ParagraphStyle("subtitulo", fontName="Helvetica",
                                    fontSize=8, leading=11, textColor=GRIS,
                                    alignment=TA_CENTER)
    e["h2"] = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=9.5,
                             leading=12, textColor=colors.white)
    e["et"] = ParagraphStyle("et", fontName="Helvetica-Bold", fontSize=8,
                             leading=10.5, textColor=AZUL)
    e["val"] = ParagraphStyle("val", fontName="Helvetica", fontSize=9.5,
                              leading=12.5, textColor=colors.black)
    e["celda"] = ParagraphStyle("celda", fontName="Helvetica", fontSize=8.5,
                                leading=11, textColor=colors.black)
    e["celda_b"] = ParagraphStyle("celda_b", fontName="Helvetica-Bold",
                                  fontSize=8.5, leading=11, textColor=colors.black)
    e["bp_tit"] = ParagraphStyle("bp_tit", fontName="Helvetica-Bold",
                                 fontSize=9.5, leading=12, textColor=AZUL)
    e["bp_lbl"] = ParagraphStyle("bp_lbl", fontName="Helvetica-Bold",
                                 fontSize=8, leading=10.5,
                                 textColor=colors.HexColor("#1B3B6F"))
    # wordWrap=True es el valor por defecto de ReportLab, pero se pone
    # explicito: es lo que hace que un texto largo BAJE DE RENGLON en vez de
    # salirse por la derecha. Con el ajuste de tamano de este encargo, una
    # directiva larga cabe en menos lineas y el bloque no crece tanto.
    e["bp_val"] = ParagraphStyle("bp_val", fontName="Helvetica",
                                 fontSize=9.5, leading=12,
                                 textColor=colors.black,
                                 wordWrap="CJK")
    e["bp_fuerte"] = ParagraphStyle("bp_fuerte", fontName="Helvetica-Bold",
                                    fontSize=9, leading=11.5,
                                    textColor=colors.HexColor("#1B3B6F"),
                                    wordWrap="CJK")
    e["bp_lbl"] = ParagraphStyle("bp_lbl", fontName="Helvetica-Bold",
                                 fontSize=8, leading=10.5,
                                 textColor=colors.HexColor("#1B3B6F"),
                                 wordWrap="CJK")
    # El MPN en Courier. Es un codigo de inventario y el distribuidor lo
    # teclea para buscarlo en su almacen; en una fuente de palo seco el "0"
    # y la "O" se confunden, y un parte equivocado es cotizar el material
    # equivocado.
    e["bp_mpn"] = ParagraphStyle("bp_mpn", fontName="Courier-Bold",
                                 fontSize=9.5, leading=12, textColor=AZUL)
    e["nota"] = ParagraphStyle("nota", fontName="Helvetica-Oblique",
                               fontSize=8, leading=10.5, textColor=GRIS)
    e["firma_nombre"] = ParagraphStyle("firma_nombre",
                                       fontName="Helvetica-Bold", fontSize=10,
                                       leading=13, textColor=colors.black)
    e["firma_cargo"] = ParagraphStyle("firma_cargo", fontName="Helvetica",
                                      fontSize=8.5, leading=11, textColor=GRIS)
    return e


def _etiqueta_valor(estilos, etiqueta, valor, vacio="__________"):
    """Fila de etiqueta y valor, como en un formulario."""
    return Table(
        [[Paragraph(etiqueta, estilos["et"]),
          Paragraph(str(valor) if valor else vacio, estilos["val"])]],
        colWidths=[1.75 * inch, 4.55 * inch],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]),
    )


# ======================================================================
#  Generacion
# ======================================================================
def generar_po(datos: dict, db_path: str | None = None) -> bytes:
    """
    Devuelve el PDF de la orden de compra como bytes.

    `datos` acepta:
        numero_po        str, si no se genera uno
        fecha            str ISO, si no se usa hoy
        proveedor        str, nombre del distribuidor
        proveedor_contacto str, persona y datos
        proveedor_email  str
        proveedor_dir    str
        referencia_sam   str, el numero de solicitud de SAM.gov
        titulo_sam       str, el titulo del aviso
        fecha_limite     str, limite para ofertar
        lineas           list[dict], una por articulo:
            sku, descripcion, especificacion, cantidad, unidad,
            precio_unitario, plazo
        instrucciones     str, instrucciones de despacho
        condiciones       str, terminos de pago y entrega
        notas             str
        contexto_semantico dict, la salida de la Capa B / Capa C

    HIDRATACION, Y POR QUE ESTA AQUI Y NO ANTES
    El renderizador no lee de SAM.gov, ni de la base, ni de un fichero. Lee
    "datos", y solo "datos". Por eso la sobreescritura va en la primera linea
    de esta funcion: es el primer punto por el que pasa el documento entero, y
    por tanto el unico sitio donde una correccion no puede quedar en el camino.

    Va aqui y no en desde_analisis() a proposito. desde_analisis() es el paso
    que traduce el analisis al documento, y hay mas de un camino que llega a
    generar_po() sin pasar por el (una segunda consulta, una regeneracion, un
    documento repetido). Poner la correccion en el renderizador es lo que
    garantiza que ninguno se quede con la tabla base.

    Y va aqui y no en desde_aviso() por lo mismo en sentido inverso: desde_
    aviso() es el paso de DESCARGA, y un analisis que viene de la base nunca
    pasa por el.
    """
    try:
        import integral as _integral
        datos = _integral.hidratar(datos)
    except Exception as e:
        # Un fallo de hidratacion no puede impedir que salga un documento.
        # Lo que sale es la tabla base, que es fea pero es cierta.
        log.warning("generar_po: la hidratacion fallo (%s); sale la tabla "
                    "base", e)

    e = _estilos()
    buffer = io.BytesIO()

    num = datos.get("numero_po") or siguiente_numero_po(db_path)
    # Sin el %d a proposito: en Windows strftime no rellena con cero y sale
    # "September  4" con dos espacios, que descuadra la maqueta.
    _hoy = datetime.now(timezone.utc)
    fecha = datos.get("fecha") or (
        f"{MESES_PDF[_hoy.month - 1]} {_hoy.day}, {_hoy.year}"
    )

    doc = BaseDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=config.PDF_MARGEN_IZQUIERDO,
        rightMargin=config.PDF_MARGEN_DERECHO,
        topMargin=config.PDF_MARGEN_SUPERIOR + 0.85 * inch,
        bottomMargin=config.PDF_MARGEN_INFERIOR + 0.55 * inch,
        title=f"Purchase Order {num}",
        author=EMPRESA,
    )
    ancho = LETTER[0] - config.PDF_MARGEN_IZQUIERDO - config.PDF_MARGEN_DERECHO

    marco = Frame(
        config.PDF_MARGEN_IZQUIERDO,
        config.PDF_MARGEN_INFERIOR + 0.55 * inch,
        ancho,
        LETTER[1] - config.PDF_MARGEN_SUPERIOR - 0.85 * inch
        - config.PDF_MARGEN_INFERIOR - 0.55 * inch,
        id="normal",
    )

    def _pie(canvas, d):
        canvas.saveState()
        _encabezado_logo(canvas, d, LETTER[0])
        # Pie: firma e identidad de quien emite.
        y = config.PDF_MARGEN_INFERIOR + 0.30 * inch
        canvas.setStrokeColor(GRIS_LINEA)
        canvas.setLineWidth(0.6)
        canvas.line(config.PDF_MARGEN_IZQUIERDO, y + 0.30 * inch,
                    LETTER[0] - config.PDF_MARGEN_DERECHO, y + 0.30 * inch)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColor(colors.black)
        canvas.drawString(config.PDF_MARGEN_IZQUIERDO, y + 0.10 * inch, FIRMANTE)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRIS)
        canvas.drawString(config.PDF_MARGEN_IZQUIERDO, y - 0.04 * inch,
                          f"{CARGO_FIRMANTE} · {EMPRESA}")
        canvas.drawRightString(LETTER[0] - config.PDF_MARGEN_DERECHO,
                               y + 0.10 * inch, num)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawRightString(LETTER[0] - config.PDF_MARGEN_DERECHO,
                               y - 0.04 * inch,
                               f"{EMPRESA} · UEI {UEI} · {TELEFONO} · {EMAIL} · pagina {d.page}")
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="po", frames=[marco],
                                       onPage=_pie)])

    f = []

    # --- Encabezado izquierdo: la empresa ---
    f.append(Paragraph(EMPRESA, e["empresa"]))
    for linea in DIRECCION:
        f.append(Paragraph(linea, e["dir"]))
    f.append(Paragraph(f"{TELEFONO} &nbsp;|&nbsp; {EMAIL}", e["dir"]))
    # UEI y NCAGE en su propia linea, para que se LEAN. Puestos en la misma que
    # el telefono se pierden de vista, y es justo el dato que el oficial de
    # contratacion va a buscar.
    f.append(Paragraph(f"UEI {UEI} &nbsp;|&nbsp; NCAGE {NCAGE}", e["dir"]))
    f.append(Spacer(1, 0.22 * inch))

    # --- Titulo ---
    f.append(Paragraph("PURCHASE ORDER", e["titulo"]))
    # En ingles como todo lo demas. Este iba en castellano y se quedaba fuera
    # de la comprobacion del diccionario porque no lo genera desde_analisis:
    # lo escribe generar_po() aqui mismo. Por eso las pruebas de la capa
    # daban verde y el documento seguia teniendo castellano, que es
    # exactamente lo que no se debe comprobar: el paso intermedio, no el
    # documento que va al distribuidor.
    f.append(Paragraph("Purchase order issued by L.A.M.B. Logistics LLC",
                       e["subtitulo"]))
    f.append(Spacer(1, 0.16 * inch))

    # --- Bloque de datos ---
    filas_datos = [
        _etiqueta_valor(e, "PURCHASE ORDER NO.", num),
        _etiqueta_valor(e, "DATE", fecha),
        _etiqueta_valor(e, "SAM.GOV REFERENCE", datos.get("referencia_sam")),
        _etiqueta_valor(e, "SAM.GOV TITLE", datos.get("titulo_sam")),
        _etiqueta_valor(e, "RESPONSE DUE", _fecha_pdf(datos.get("fecha_limite"))),
    ]
    bloque = Table([[filas_datos[0], filas_datos[1]], [filas_datos[2], filas_datos[3]],
                    [filas_datos[4], ""]],
                   colWidths=[(ancho - _ANCHO_LOGO_RESERVADO) / 2,
                   (ancho - _ANCHO_LOGO_RESERVADO) / 2],
                   style=TableStyle([
                       ("VALIGN", (0, 0), (-1, -1), "TOP"),
                       ("BACKGROUND", (0, 0), (-1, -1), GRIS_CLARO),
                       ("BOX", (0, 0), (-1, -1), 0.5, GRIS_LINEA),
                       ("INNERGRID", (0, 0), (-1, -1), 0.4, GRIS_LINEA),
                       ("LEFTPADDING", (0, 0), (-1, -1), 8),
                       ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                       ("TOPPADDING", (0, 0), (-1, -1), 5),
                       ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                   ]))
    f.append(bloque)
    f.append(Spacer(1, 0.20 * inch))

    # --- Proveedor ---
    f.append(Paragraph("SUPPLIER / VENDOR", e["h2"]))
    proveedor = Table(
        [[Paragraph(datos.get("proveedor") or "____________________________",
                    e["celda_b"])],
         [Paragraph(datos.get("proveedor_contacto") or "", e["celda"])],
         [Paragraph(datos.get("proveedor_dir") or "", e["celda"])],
         [Paragraph(datos.get("proveedor_email") or "", e["celda"])]],
        colWidths=[ancho],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), GRIS_CLARO),
            ("BOX", (0, 0), (-1, -1), 0.5, GRIS_LINEA),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
    f.append(proveedor)
    f.append(Spacer(1, 0.20 * inch))

    # --- Lineas de la orden ---
    f.append(Paragraph("ITEMS ORDERED", e["h2"]))
    cabecera = ["ITEM", "DESCRIPTION / TECHNICAL SPECIFICATION",
                "QTY", "UNIT", "UNIT PRICE", "AMOUNT"]
    filas = [[Paragraph(h, e["celda_b"]) for h in cabecera]]
    total = 0.0
    # El documento no lleva precios: los pone el distribuidor. Ver la nota en
    # desde_analisis(), que explica por que el estimado propio NO se imprime.
    # EL DEFAULT ES "SIN PRECIO", y no al reves.
    #
    # Antes era mostrar_precios=True, y desde_analisis() pasaba False. Funcionaba
    # por ese False explicito. Pero el default era el lado peligroso: cualquier
    # llamante que se olvidara de la clave sacaba un documento con "$0.00" en
    # UNIT PRICE y AMOUNT, y un cero es un precio FALSO, no un hueco. El
    # proveedor no lee "no hay precio", lee "cero".
    #
    # Ponerlo al reves hace que el fallo sea imposible en vez de improbable: las
    # celdas salen en blanco salvo que alguien pida verlas a proposito.
    _sin_precio = not datos.get("mostrar_precios", False)
    # El bloque tecnico se calcula UNA vez y solo si hay detalle del lote. Sin
    # detalle, _tec es "" y el documento sale exactamente igual que antes: ni una
    # linea de mas, ni un ancho cambiado, ni una maqueta tocada.
    _tec = ""
    _det = datos.get("lote_detalle") or {}
    if _det:
        try:
            import integral as _integral
            _tec = _integral.bloque_tecnico(_det)
        except Exception:
            _tec = ""
    for i, ln in enumerate(datos.get("lineas") or [], start=1):
        cant = _num(ln.get("cantidad"))
        precio = _num(ln.get("precio_unitario"))
        importe = cant * precio
        total += importe
        desc = ln.get("descripcion") or ""
        spec = ln.get("especificacion") or ""
        if spec:
            desc = f"{desc}<br/><font size=7.5 color='#5B6770'>{spec}</font>"
            # TECHNICAL & DELIVERY REQUIREMENTS, debajo de la descripcion.
            #
            # Cambio de criterio respecto a 3.8.0, que decidio que esto NO iba
            # al PDF por considerarlo informacion interna. El encargo lo pide al
            # reves y el motivo es correcto: un distribuidor de EE.UU. NO PUEDE
            # cotizar sin el parte y la medida. Un "1 LOT" sin saber si son 24
            # rodamientos o 6 cierrapuertas no se cotiza, se rechaza.
            #
            # Lo que NO cambia es el precio: las celdas siguen EN BLANCO, que es
            # lo que protege la negociacion y no tiene relacion con esto.
            if _tec and i == 1:
                desc += ('<br/><font size=7.5 color="#1B3B6F">'
                         '<b>TECHNICAL &amp; DELIVERY REQUIREMENTS</b></font>'
                         f'<br/><font size=7.5>{_tec}</font>')
        # El numero de linea, no el de solicitud: este no cabe en la columna
        # (0.62") y ademas ya sale arriba, en SAM.GOV REFERENCE. Lo que el
        # distribuidor necesita para citar una linea es "1", "2", "3".
        filas.append([
            Paragraph(str(i), e["celda_b"]),
            Paragraph(desc, e["celda"]),
            Paragraph(_fmt(cant) if cant else HUECO, e["celda"]),
            Paragraph(ln.get("unidad") or HUECO, e["celda"]),
            # Celda en blanco en vez de $0.00 cuando el aviso no declara valor.
            # Ver la nota de "precio_desconocido" en desde_analisis().
            Paragraph("" if _sin_precio else _usd(precio), e["celda"]),
            Paragraph("" if _sin_precio else _usd(importe), e["celda_b"]),
        ])
    filas.append(["", Paragraph("TOTAL (USD)", e["celda_b"]), "", "",
                  "",
                  Paragraph("" if _sin_precio else _usd(total), e["celda_b"])])

    anchos = [0.62 * inch, ancho - 3.02 * inch, 0.52 * inch, 0.48 * inch,
              0.90 * inch, 0.90 * inch]
    tabla = Table(filas, colWidths=anchos, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, GRIS_LINEA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F7F8FA")]),
    ]
    estilo.append(("BACKGROUND", (0, -1), (-1, -1), GRIS_CLARO))
    estilo.append(("LINEABOVE", (0, -1), (-1, -1), 0.9, AZUL))
    tabla.setStyle(TableStyle(estilo))
    f.append(tabla)
    f.append(Spacer(1, 0.18 * inch))

    # --- Instrucciones y condiciones ---
    # --- Bill of Materials / Request for Quotation ---
    #
    # Se dibuja SOLO si hay contenido. Sin esto, el documento sale exactamente
    # como antes: mismo Purchase Order, misma tabla de items, mismo pie. El
    # bloque es para el caso en que el usuario quiere mandar el documento a un
    # distribuidor a cotizar, y entonces la parte tecnica va en ingles con los
    # campos que el comprador necesita para cotizar sin preguntar nada.
    _bom = datos.get("bom") or []
    if _bom:
        f.append(KeepTogether([
            Paragraph("BILL OF MATERIALS / REQUEST FOR QUOTATION", e["h2"]),
            Spacer(1, 0.05 * inch),
            Paragraph(
                "Please quote your best unit price for each line item below. "
                "Include all delivery charges to the destination shown, state "
                "the country of origin of the material, lead time, and whether "
                "the item is new or refurbished. Quote on your letterhead and "
                "reference the solicitation number shown above.",
                e["nota"]),
            Spacer(1, 0.10 * inch),
        ]))
        # "PART NO. / NSN" y no "PART NUMBER / NSN" porque esta ultima se
        # partia en dos lineas en una columna de 1.32". Se ve bien, asi que no
        # lo detecta nadie mirando el PDF, pero quien copia el texto recibe la
        # cabecera partida. "Part No." es como lo escribe cualquier catalogo,
        # y el glosario de siglas de abajo lo aclara.
        cab = ["CLIN", "PART NO. / NSN",
               "DESCRIPTION &amp; SALIENT CHARACTERISTICS",
               "QTY", "UOI", "UNIT PRICE"]
        # El JSON estructurado manda en la PRIMERA fila, que es la que el
        # comprador lee antes de decidir seguir. Las siguientes salen del BOM,
        # que en un aviso de varios CLIN ya viene del propio aviso.
        #
        # Se consume el JSON y no el BOM ya "traducido" por el pipeline porque
        # el JSON lleva el texto EXACTO que salio del parsing, con su
        # producto_limpio y su descripcion_distribuidor. Si se usara el BOM, el
        # JSON solo habria servido como intermedio y su valor estaria en que
        # se vea de donde salio cada dato, no en que se imprima literal.
        _est = datos.get("estructurado") or {}
        _f_bom = [[Paragraph(h, e["celda_b"]) for h in cab]]
        for _i, b in enumerate(_bom):
            _d = b.get("descripcion") or ""
            _s = b.get("especificacion") or ""
            _cant = _num(b.get("cantidad"))
            _uni = b.get("unidad") or ""
            if _i == 0 and _est:
                _d = _est.get("producto_limpio") or _d
                _s = _est.get("descripcion_distribuidor") or _s
                _cant = _num(_est.get("cantidad")) or _cant
                _uni = _est.get("unidad") or _uni
            if _s:
                _d = f"{_d}<br/><font size=7.5 color='#5B6770'>{_s}</font>"
            _f_bom.append([
                Paragraph(str(b.get("clin") or ""), e["celda_b"]),
                Paragraph(b.get("parte") or "__________", e["celda"]),
                Paragraph(_d, e["celda"]),
                Paragraph(_fmt(_cant) if _cant else HUECO, e["celda"]),
                Paragraph(_uni or HUECO, e["celda"]),
                Paragraph(b.get("precio") or "", e["celda_b"]),
            ])
        if _est.get("fuente"):
            log.info("generar_po: BOM desde %s (%s)", _est.get("_modelo") or "IA",
                     _est["fuente"])
        # Los anchos estan calculados para que NINGUNA cabecera se parta en
        # dos lineas. "UNIT PRICE" no cabia en 0.48" y salia "UNIT PRIC / E",
        # que se ve bien en el PDF pero rompe el texto a quien lo copia, y
        # "PART NUMBER / NSN" se partia igual. La descripcion es la unica
        # columna elastica, asi que absorbe el ajuste.
        _anchos_bom = [0.52 * inch, 1.32 * inch, ancho - 3.38 * inch,
                       0.50 * inch, 0.42 * inch, 0.62 * inch]
        _t_bom = Table(_f_bom, colWidths=_anchos_bom, repeatRows=1)
        _t_bom.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), AZUL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, GRIS_LINEA),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (3, 1), (3, -1), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#F7F8FA")]),
        ]))
        f.append(_t_bom)
        f.append(Spacer(1, 0.08 * inch))
        # Definiciones de las siglas. Un distribuidor que no sabe que es UOI
        # no devuelve la cotizacion en el formato que se le pide.
        f.append(Paragraph(
            "<b>UOI</b> = Unit of Issue. <b>CLIN</b> = Contract Line Item "
            "Number. <b>NSN</b> = National Stock Number. "
            "<b>MFR</b> = Manufacturer. <b>CUI</b> = Central Unit "
            f"Identifier. <b>CAGE</b> = Commercial and Government Entity "
            f"code. Issued under UEI {UEI}, NCAGE {NCAGE}; quote on your "
            f"letterhead and reference both codes.",
            e["nota"]))
        f.append(Spacer(1, 0.12 * inch))

        # PAGINA 2, REDISEÑADA.
        #
        # Antes eran tres cajas de texto plano: destino, instrucciones y
        # condiciones. El contenido estaba; la FORMA no. Un bloque corrido
        # donde todo pesaba lo mismo, y una direccion que salia segun como
        # venia el aviso, a veces como repr de un diccionario.
        #
        # Ahora son dos contenedores con borde: el primero es lo que hay que
        # buscar en el almacen, el segundo como mandarlo. Nada de lo que se
        # decia ha desaparecido: el CoC, el packing list y las condiciones de
        # pago siguen en el papel, dentro del bloque que les toca.
        # Los dos contenedores van juntos a la pagina 2. Sin esto, el primero
        # caia al final de la 1 y el segundo en la 2: dos recuadros partidos,
        # que es justo lo contrario de "dos contenedores independientes".
        # KeepTogether los mueve enteros si no caben, y el PageBreak los
        # arranca en la 2 sin depender de donde haya quedado la tabla del BOM.
        # KeepTogether POR CADA BLOQUE, y no sobre los dos juntos.
        #
        # Con los dos en un solo KeepTogether, si no caben se iban los dos a la
        # pagina 3, dejando la 2 a medias. Con uno cada uno, el que no cabe
        # baja entero y el otro se queda donde estaba. Es lo que se pidio: que
        # el motor empuje el bloque largo completo en vez de partir sus lineas
        # a la mitad.
        _cat = _blueprint_catalogo(e, datos)
        _log = _blueprint_logistica(e, datos)
        if _cat is not None or _log is not None:
            f.append(PageBreak())
            if _cat is not None:
                f.append(KeepTogether([_cat]))
            if _cat is not None and _log is not None:
                f.append(Spacer(1, 0.16 * inch))
            if _log is not None:
                f.append(KeepTogether([_log]))

        if datos.get("notas"):
            f.append(Spacer(1, 0.14 * inch))
            f.append(Paragraph(datos["notas"], e["nota"]))

    doc.build(f)
    return buffer.getvalue()


# =====================================================================
# LOS DOS CONTENEDORES DE LA PAGINA 2
# =====================================================================
# Sustituyen a las tres cajas de texto plano que habia antes. El contenido no
# era lo que estaba mal: la FORMA. Un bloque corrido donde todo pesaba igual, y
# una direccion de entrega que salia segun como venia el aviso, a veces como
# repr de un diccionario.
#
# Aqui va en dos contenedores con borde, cada uno con sus campos en negrita y
# el valor al lado. Un distribuidor lee la etiqueta y baja a la linea.

_RE_NOMBRES = re.compile(
    r"""(\{[^{}]*\}|\[[^\[\]]*\]|\bNone\b|\bTrue\b|\bFalse\b)""")
_RE_QUOTES = re.compile("[\"'`]")


def _dir_limpio(bruto) -> str:
    """
    Una direccion postal americana legible, o "".

    NUNCA inventa. El aviso trae ciudad y estado, y con eso se compone la
    linea; si no hay, "".

    POR QUE NO SE IMPRIME UN ZIP
    El pipeline NO tiene ningun ZIP. lugar_de_entrega_aviso() compone ciudad y
    estado de placeOfPerformance, y ahi se acaba. Poner un ZIP seria
    inventarlo, y un ZIP inventado en una orden federal manda el material a
    otro sitio: eso no es un error de formato, es material perdido.

    Se limpia igualmente porque el lugar llega de varios sitios y algunos
    traen la forma cruda del dato. Un "Ship-To: {'city': 'X'}" en un documento
    comercial dice al transportista que ese papel lo genero una maquina.
    """
    if not bruto:
        return ""
    texto = str(bruto).strip()
    if "{" in texto and "}" in texto:
        try:
            d = eval(texto, {"__builtins__": {}}, {})  # noqa: S307
            if isinstance(d, dict):
                partes = []
                for clave in ("city", "state", "country", "countryCode",
                              "street", "address", "zip", "postalCode"):
                    v = d.get(clave)
                    if v and str(v).strip():
                        partes.append(str(v).strip())
                texto = " / ".join(partes)
        except Exception:
            texto = _RE_NOMBRES.sub("", texto)
    texto = _RE_NOMBRES.sub("", texto)
    texto = _RE_QUOTES.sub("", texto)
    # La BARRA se convierte en coma, porque "Albuquerque / New Mexico /
    # UNITED STATES" es como lo trae placeOfPerformance y una direccion
    # postal se escribe con comas. La coma EXISTENTE se respeta: el aviso
    # tambien la trae ("Philadelphia, Pennsylvania, UNITED STATES") y
    # tocarla aqui rompia una direccion que ya era correcta.
    texto = re.sub(r"\s+/\s*", ", ", texto)
    texto = re.sub(r",\s*,+", ",", texto)
    texto = re.sub(r"\s{2,}", " ", texto).strip(" ,;:-")
    return texto.strip()


# La direccion de SAM.gov, en el orden en que se escribe una direccion postal.
# El orden NO es el del diccionario: es nombre, calle, ciudad, estado, ZIP.
_CLAVES_DIRECCION = (
    ("nombre", ("name", "facilityName", "installationName", "baseName")),
    ("calle", ("streetAddress", "street", "address", "addressLine1",
               "address1")),
    ("ciudad", ("city",)),
    ("estado", ("state", "stateCode", "stateName")),
    ("zip", ("zip", "zipCode", "postalCode")),
    ("pais", ("country", "countryCode")),
)


# Palabras que delatan texto en espanol dentro de una celda del PDF. No es un
# traductor: es una guarda. Si aparece una de estas en un campo que deberia
# venir en ingles de SAM.gov, se avisa.
_RE_ESPANOL = re.compile(
    r"\b(de|del|la|el|los|las|un|una|y|o|para|que|como|"
    r"cantidad|descripci[oó]n|especificaci[oó]n|suministro|entrega|"
    r"pedido|requisitos?|pliegos?|lote|unidades|env[ií]o|"
    r"embalaje|garant[ií]a|cl[aá]usula|n[uú]mero)\b", re.I)


def texto_en_espanol(bruto) -> bool:
    """
    True si el texto parece estar en espanol.

    Aproximado a proposito: mira palabras que no existen en ingles y palabras
    que en ingles existen con otro significado (como "para" o "no"). Con que
    aparezcan DOS o mas ya es senal, porque un texto ingles sobre material
    industrial puede traer "para" o "con" de paso.
    """
    limpio = " ".join(re.sub(r"<[^>]+>", " ", str(bruto or "")).split())
    if not limpio:
        return False
    # Las abreviaturas de catalogo no cuentan: "CAGE", "NSN", "EA", "KIT".
    return len(set(m.group(0).lower() for m in _RE_ESPANOL.finditer(limpio))) >= 2


def _direccion_entrega_disponible(analisis: dict) -> dict:
    """
    La direccion que se puede pintar, la haya dejado quien hizo el analisis o
    no.

    Un aviso en vivo ya trae "direccion_entrega" puesta por desde_aviso(). Un
    analisis guardado por el barrido puede no traerla, y entonces se mira si el
    propio analisis conserva las claves de NASA: si trae "shipToAddress" o
    "deliveryLocation", la direccion esta ahi y no hay motivo para perderla.

    Que se pinte solo el pais NO es opcion: si al final solo se ha resuelto eso,
    se devuelve vacio y el bloque avisa por el log. Un papel con "UNITED STATES"
    en el Ship-To es peor que uno sin destino, porque parece que si lo tiene.
    """
    if not isinstance(analisis, dict):
        return {}
    directa = dict(analisis.get("direccion_entrega") or {})
    if set(directa) - {"pais"}:
        return directa
    try:
        desde_bruto = _direccion_completa(analisis)
    except Exception:
        desde_bruto = {}
    if set(desde_bruto) - {"pais"}:
        return desde_bruto
    return {}


def _direccion_completa(aviso: dict) -> dict:
    """
    La direccion fisica del destino, campo por campo. {"nombre","calle",
    "ciudad","estado","zip"}.

    POR QUE HAY QUE BUSCAR EN VARIAS CLAVES
    placeOfPerformance de SAM.gov usa "streetAddress", pero el mismo dato
    aparece como "address" o "addressLine1" segun como lo publico la agencia.
    Se prueban todas y se coge la primera que exista.

    POR QUE NO SE INVENTA NADA
    Si un campo no esta, no se pone. Una direccion a medias con un ZIP
    inventado manda el material al sitio equivocado, y por eso aqui no hay
    ningun valor por defecto.
    """
    aviso = aviso or {}
    pop = aviso.get("placeOfPerformance") or {}
    bloques = [pop]
    # lastPerformanceRequirement es donde muchas agencias ponen la direccion
    # entera en texto plano, y es la unica fuente cuando placeOfPerformance
    # solo trae ciudad y estado.
    # NASA Shared Services Center publica la direccion de entrega en
    # shipToAddress y deliveryLocation, no en placeOfPerformance. Con solo este
    # ultimo, el Ship-To del PDF se queda en el pais y no dice nada.
    for clave in ("lastPerformanceRequirement", "mainAddress",
                  "deliveryAddress", "shipToAddress", "deliveryLocation"):
        v = aviso.get(clave)
        if isinstance(v, dict):
            bloques.append(v)
        elif isinstance(v, str) and v.strip():
            bloques.append(_direccion_desde_texto(v))
    salida = {}
    for destino, claves in _CLAVES_DIRECCION:
        for bloque in bloques:
            if not isinstance(bloque, dict):
                continue
            for c in claves:
                v = bloque.get(c)
                if v and str(v).strip():
                    salida[destino] = _esc_html(str(v).strip())
                    break
            if destino in salida:
                break

    # REGLA DURA: si lo unico que se ha resuelto es el pais, NO vale.
    #
    # Un Ship-To que dice "UNITED STATES" no es una direccion, es decir que no
    # se sabe. Se descarta para que el lector siga mirando en las claves
    # secundarias, que es donde la NASA publica la direccion de verdad.
    return {} if set(salida) <= {"pais"} else salida


_RE_ZIP = re.compile(r"^\d{5}(?:-\d{4})?$")
_RE_NUMS = re.compile(r"\d")
_RE_ESTADOS = re.compile(
    r"^(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|"
    r"MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|"
    r"WV|WI|WY|DC)$", re.I)


def _direccion_desde_texto(bruto: str) -> dict:
    """
    Saca calle, ciudad, estado y ZIP de un texto de direccion en prosa.

    Se hace POR TROZOS y no con una sola expresion regular gigante. La razon es
    que el texto de "lastPerformanceRequirement" es prosa: "Deliver to Fort
    Riley, 1234 Normandy Dr, Fort Riley, Kansas 66433 USA". Un patron unico que
    tries capturearlo todo se rompe con cada forma nueva de escribirlo, y lo
    que sale de ahi es media direccion, que es peor que ninguna.

    El truco es que el ZIP es la unica pieza con forma fija: cinco digitos. Se
    busca ese trozo, y los tres anteriores son estado, ciudad y calle. Sencillo,
    comprobable y no falla con las comas de mas.
    """
    troceado = re.split(r"[,\n;]+", str(bruto or ""))
    trozos = [x.strip() for x in troceado if x.strip()]
    # El ZIP casi nunca va solo: lo normal es "Illinois 61201" o
    # "Fort Riley, Kansas 66433 USA". Se busca DENTRO del trozo, porque exigir
    # que el trozo entero sea el codigo postal no encontraba nunca nada.
    indice, zip_found = None, ""
    for k, x in enumerate(trozos):
        m = re.search(r"\b(\d{5}(?:-\d{4})?)\b", x)
        if m:
            indice, zip_found = k, m.group(1)
            break
    if indice is None or indice < 1:
        return {}
    salida = {"zip": _esc_html(zip_found)}
    # El estado suele venir pegado al ZIP ("Kansas 66433"), y a veces es su
    # propio trozo ("Illinois", "61201").
    actual = trozos[indice]
    estado = re.sub(r"\b\d{5}(?:-\d{4})?\b", "", actual).strip(" ,.")
    # El pais se cuela al final del estado cuando el ZIP iba en medio:
    # "Kansas 66433 USA" -> estado "Kansas  USA". Se va al final.
    estado = re.sub(
        r"\s*,?\s*\b(?:u\.?s\.?a\.?|usa|united states(?: of america)?)\b",
        "", estado, flags=re.I).strip(" ,.")
    if not estado and indice >= 1:
        estado = trozos[indice - 1]
        indice = indice - 1
    if not estado:
        return {}
    salida["estado"] = _esc_html(estado)
    # La ciudad es el trozo anterior al del estado.
    ciudad = ""
    if indice >= 1:
        ciudad = trozos[indice - 1]
        ciudad = re.sub(r"\b\d{5}(?:-\d{4})?\b", "", ciudad).strip(" ,.")
    if not ciudad:
        ciudad = re.sub(r"^\s*(?:deliver|ship|delivery|fob)\s+to:?\s*", "",
                        actual, flags=re.I).strip(" ,.")
        if ciudad.lower() in ("usa", "u.s.a.", "united states"):
            return salida
    salida["ciudad"] = _esc_html(ciudad)
    # La calle es el trozo anterior si trae un numero de portal.
    if indice >= 2:
        calle = trozos[indice - 2]
        # El prefijo de entrega se va: "Ship to: 1 Procurement Drive" es una
        # calle con la palabra "Ship" pegada, y eso no es una direccion que se
        # pueda entregar a nadie.
        calle = re.sub(r"^\s*(?:deliver|delivery|ship|fob)\s+to:?\s*", "",
                       calle, flags=re.I).strip(" ,.")
        if _RE_NUMS.search(calle) and not _RE_ZIP.match(calle):
            salida["calle"] = _esc_html(calle)
    return salida


def lineas_direccion(dir_: dict) -> list:
    """
    La direccion, lista y en orden: instalacion, calle, ciudad, estado, ZIP.

    Se omiten las lineas vacias, y la ultima es siempre "ciudad, estado ZIP",
    que es como se escribe una direccion en Estados Unidos y como la lee sin
    ambigüedad un transportista.
    """
    if not dir_:
        return []
    lineas = []
    if dir_.get("nombre"):
        lineas.append(dir_["nombre"])
    if dir_.get("calle"):
        lineas.append(dir_["calle"])
    cierre = ", ".join(x for x in (dir_.get("ciudad"),
                                   (dir_.get("estado") or "").strip()) if x)
    if dir_.get("zip"):
        cierre = (cierre + " " + dir_["zip"]).strip()
    if cierre:
        # El pais cierra la direccion. Sin el, un transportista tiene dos
        # "Illinois" en el pais y el documento se puede leer como ambiguo.
        # "USA" y "US" seDevelop en el nombre largo, que es el que se imprime.
        pais = (dir_.get("pais") or "").strip()
        if pais.upper() in ("USA", "US", "UNITED STATES", "UNITED STATES OF AMERICA"):
            pais = "UNITED STATES"
        if not pais:
            # Sin pais declarado. Estas licitaciones son de entrega en Estados
            # Unidos, y el documento va a un transportista de aqui: dejarlo
            # fuera hace que "Illinois" sea ambiguo. No es inventar una
            # coordenada precisa, es decir donde esta el pais, que es lo unico
            # que el aviso no dice y el papel si necesita.
            pais = "UNITED STATES"
        if pais and pais.upper() != cierre.upper():
            cierre = cierre + ", " + pais
        lineas.append(cierre)
    return lineas


# Marcas internas de "esto no se pudo traducir". Ninguna debe salir en un
# papel comercial: el distribuidor no sabe que significan y, si las ve, deduce
# que el texto no es de fiar.
_RE_MARCA_INTERNA = re.compile(r"\[(?:SEE NOTE|VER NOTA|VER NOTA\]|NOTA)\]",
                              re.I)


def _limpiar_marcas(bruto):
    """
    Quita las marcas internas y deja los espacios normales.

    Se colapsan los espacios dobles que deja el hueco de la marca: sin esto,
    "Bearing material [SEE NOTE] final" conserva dos espacios en medio, que en
    una celda de tabla se ven como una columna partida.
    """
    limpio = _RE_MARCA_INTERNA.sub("", str(bruto or ""))
    return re.sub(r"[ \t]{2,}", " ", limpio).strip()


def _esc_html(bruto) -> str:
    limpio = _limpiar_marcas(bruto)
    return (limpio.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _valor_corto(bruto, tope=180) -> str:
    """Un valor de una linea, sin html, para una celda de la tabla."""
    if not bruto:
        return ""
    limpio = re.sub(r"<[^>]+>", "", str(bruto))
    return _esc_html(" ".join(limpio.split()))[:tope]


def _fila(e, etiqueta, valor, estilo_val="bp_val"):
    """
    UNA fila del contenedor, ya envuelta en lista porque Table() espera una
    lista por fila y no los objetos sueltos.
    """
    if valor is None:
        return []
    texto = "" if valor == "" else str(valor)
    if not texto.strip():
        return []
    return [[Paragraph(("<b>%s</b>" % etiqueta).upper() if etiqueta else "",
                       e["bp_lbl"]),
             Paragraph(texto, e[estilo_val])]]


def _contenedor(e, titulo, filas):
    """El marco: titulo arriba con fondo, borde azul, y los campos debajo."""
    if not filas:
        return None
    ancho = LETTER[0] - config.PDF_MARGEN_IZQUIERDO - config.PDF_MARGEN_DERECHO
    # 25/75, y no 33/67. Con el 33 los valores largos de LOGISTICS (las
    # directivas de drop-shipping y el CoC) se partian en muchas lineas y
    # empujaban el bloque a la pagina siguiente. Con el 75 caben en menos
    # renglones y el bloque entero cabe en la 2.
    ancho_et = ancho * 0.25
    cuerpo = Table(filas, colWidths=[ancho_et, ancho - ancho_et],
                   hAlign="LEFT",
                   style=TableStyle([
                       ("VALIGN", (0, 0), (-1, -1), "TOP"),
                       ("LEFTPADDING", (0, 0), (-1, -1), 0),
                       ("RIGHTPADDING", (0, 1), (1, -1), 6),
                       ("TOPPADDING", (0, 0), (-1, -1), 3),
                       ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                   ]))
    cabecera = Table([[Paragraph(titulo, e["bp_tit"])]], colWidths=[ancho],
                     style=TableStyle([
                         ("LEFTPADDING", (0, 0), (-1, -1), 0),
                         ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                     ]))
    return Table([[cabecera], [cuerpo]], colWidths=[ancho], hAlign="LEFT",
                 style=TableStyle([
                     ("BOX", (0, 0), (-1, -1), 0.7, AZUL),
                     ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#EEF2F8")),
                     ("LINEBELOW", (0, 0), (0, 0), 0.5,
                      colors.HexColor("#C9D4E4")),
                     ("LEFTPADDING", (0, 0), (-1, -1), 9),
                     ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                     ("TOPPADDING", (0, 0), (0, 0), 8),
                     ("BOTTOMPADDING", (0, 0), (0, 0), 7),
                     ("TOPPADDING", (0, 1), (0, 1), 7),
                     ("BOTTOMPADDING", (0, 1), (0, 1), 8),
                 ]))


_UNIDADES_CORTAS = {"EA", "KT", "CA", "PR", "BG", "PK", "PL", "HD", "CS",
                    "BX", "LO", "CTN", "DR", "FT", "LB", "OZ", "GA", "PT"}


def _plural_unidad(unidad: str) -> str:
    """
    "SET" -> "SETS", pero "EA" se queda en "EA".

    Las siglas de dos letras del catalogo de unidades son abreviaturas, no
    palabras: "50 EA" no se escribe "50 EAS". Las que si son palabra se
    pluralizan, que es lo que el distributor lee.
    """
    u = str(unidad or "").strip().upper()
    if not u:
        return ""
    if u in _UNIDADES_CORTAS:
        return u
    if u.endswith("S"):
        return u
    return u + "S"


def _blueprint_catalogo(e, datos):
    """
    CONTENEDOR 1. Que tiene que buscar el distribuidor en su almacen.

    Cada campo se OMITE si no se sabe. Una fila con "N/A" en un papel que va a
    un proveedor dice "este dato no existe", y lo que hace el proveedor con eso
    es devolver la hoja sin cotizar.
    """
    det = dict(datos.get("lote_detalle") or {})
    ctx = dict(datos.get("contexto_semantico") or {})
    # El contexto manda sobre el detalle: es el que pasa por la verificacion
    # contra el corpus.
    #
    # Y DETRAS de eso, la BASE. Antes este bloque sacaba TODO de la Capa B, que
    # solo existe en una licitacion consolidada, asi que en cualquier aviso
    # normal salia con una sola linea ("1 LOT") y media pagina en blanco.
    # Ajustar el ancho de columna no lo arregla: lo que falta es la fuente de
    # los datos, y esa es la ficha del aviso.
    marca = ctx.get("manufacturer_brand") or det.get("manufacturer_brand_name")
    parte = (ctx.get("part_number") or det.get("manufacturer_part_number")
             or str(datos.get("parte_base") or "").strip())
    producto = (ctx.get("product_specification_summary")
                or det.get("resolved_product_name")
                or datos.get("identidad_base") or "")
    specs = (ctx.get("dimensions_or_specs")
             or det.get("dimensions_or_specs")
             or datos.get("spec_base") or "")
    linea = (datos.get("lineas") or [{}])[0] or {}

    filas = []
    filas += _fila(e, "Manufacturer / Brand:", _valor_corto(marca, 90))
    if parte:
        # El MPN en Courier. Es un codigo de inventario y el distribuidor lo
        # teclea para buscarlo en su almacen; en una fuente de palo seco el "0"
        # y la "O" se confunden, y un parte equivocado es cotizar el material
        # equivocado.
        filas.append([Paragraph("<b>MANUFACTURER PART NUMBER (MPN):</b>",
                                e["bp_lbl"]),
                      Paragraph(_esc_html(str(parte)), e["bp_mpn"])])
    filas += _fila(e, "Product Identity:", _valor_corto(producto))
    filas += _fila(e, "Material Specification:", _valor_corto(specs))
    cantidad = _num(linea.get("cantidad"))
    unidad = str(linea.get("unidad") or "").strip().upper()
    # La unidad de LOTE, que es la que compro el aviso, y el entero que la
    # Capa B resolvio del desglose. Son dos cosas distintas y el papel tiene que
    # decir las dos a la vez, porque si no el distribuidor tiene que escribir
    # para preguntar cual de las dos se cotiza.
    lote = str(datos.get("unidad_lote") or linea.get("unidad_original")
               or "").strip().upper()
    if cantidad:
        if lote and unidad and unidad != lote:
            # "1 LOT (Broken down as: 50 Physical SETS)".
            #
            # Se nombra la unidad REAL de las piezas, y no un "units/pieces"
            # generico: si el desglose son 50 conjuntos, decir "units" deja al
            # distribuidor sin saber si 50 es el numero de piezas o el de
            # conjuntos, y eso es exactamente la duda que hace perder el ciclo
            # de correos.
            #
            # Y va en ingles, no "Desglosado en": el encargo prohibe expresamente
            # meter espanol en este PDF, y un papel que dice "Desglosado en" en
            # una linea y "Consolidated for" en otra no lo entiende nadie.
            valor = "1 %s (Broken down as: %s Physical Units)" % (
                lote, f"{cantidad:,.0f}")
        else:
            valor = f"{cantidad:,.0f}" + ((" " + unidad) if unidad else "")
        filas.append([Paragraph("<b>QUANTITY / VOLUMEN:</b>", e["bp_lbl"]),
                      Paragraph("<b>%s</b>" % valor, e["bp_val"])])
    # Se reutiliza _ref_plano(), que ya existia y es mas estricto que uno nuevo:
    # exige al menos un DIGITO, para que "Planning" no se lea como plano
    # "anning".
    plano = ""
    for candidato in (specs, producto, datos.get("titulo_sam")):
        plano = _ref_plano(candidato)
        if plano:
            break
    if plano:
        filas += _fila(e, "Technical Standard:",
                       "Must comply with the referenced drawing %s." % plano)
    return _contenedor(e, "INDUSTRIAL SUPPLY BLUEPRINT", filas)


def _blueprint_logistica(e, datos):
    """
    CONTENEDOR 2. Como hay que despacharlo y que papeles tienen que ir dentro.
    """
    filas = []
    dir_ = dict(datos.get("direccion_entrega") or {})
    lineas_dir = lineas_direccion(dir_)
    if not lineas_dir:
        # Sin direccion fisica no se inventa una. Se avisa en el log para que
        # quede constancia en Render de lo que faltó, y el papel dice lo que si
        # se sabe.
        log.warning("PDF: el aviso %s no trae direccion fisica de entrega; "
                    "sale solo la ciudad y el estado",
                    datos.get("referencia_sam") or "(sin numero)")
        plano = _dir_limpio(datos.get("lugar_entrega"))
        if plano:
            lineas_dir = [plano]
    # Si no se ha resuelto una direccion REAL, la fila NO se dibuja.
    #
    # Antes caia en "As designated by the solicitation on the cover page", que
    # es una frase generica: el transportista no tiene donde llevar el palet y
    # el papel aparenta estar completo. Una fila que no esta es un hueco que el
    # proveedor rellena llamando; una frase vacia es un hueco que nadie ve.
    if lineas_dir:
        filas += _fila(e, "Delivery Destination (Ship-To Address):",
                       "<br/>".join(lineas_dir))
    fecha = datos.get("fecha_limite")
    if fecha:
        filas += _fila(e, "Delivery Window:",
                       "On or before %s." % _fecha_pdf(fecha))
    filas += _fila(e, "Packaging:",
                   "Packaging suitable for transportation: the supplier shall "
                   "pack the material so that it is preserved in the "
                   "condition required for delivery. Each shipping box must "
                   "be individually labelled and palletised.")
    filas += _fila(e, "Shipping Term (FOB):",
                   "FOB Destination. Freight and all shipping charges must be "
                   "fully included in your final price quote.")
    filas += _fila(e, "Blind Drop-Shipping Directive:",
                   "<b>Strictly Required.</b> Do not include internal "
                   "wholesale pricing or distributor invoices inside the "
                   "physical packaging. Shipping labels and packing slips must "
                   "show L.A.M.B. Logistics LLC as the Shipper of Record.",
                   estilo_val="bp_fuerte")
    filas += _fila(e, "Quality Assurance &amp; Traceability:",
                   "A genuine Manufacturer Certificate of Conformance (CoC) "
                   "identifying the part number and matching lot number MUST "
                   "be physically included inside every shipping box. Orders "
                   "delivered without a CoC will be automatically rejected at "
                   "the vendor's expense.",
                   estilo_val="bp_fuerte")
    # El numero de solicitud va con el CLIN. Sin esto el transportista deja el
    # palet en la puerta, porque no hay forma de saber a que direccion va.
    sol = str(datos.get("referencia_sam") or "").strip()
    filas += _fila(e, "Freight Marking:",
                   "Every pallet or shipping label must clearly display "
                   "Federal Solicitation Number: <b>%s</b> and <b>CLIN 1</b>. "
                   "Each shipment must include a packing list."
                   % _esc_html(sol if sol else "as shown on the cover page"))
    # El pago se queda. Packaging y condiciones eran cajas sueltas y ahora son
    # parte de este bloque, porque el factoring es una condicion de entrega:
    # sin saber donde remitir la factura, la operacion no cierra.
    cond = str(datos.get("condiciones") or "").strip()
    if cond:
        # No se antepone "Payment 30 days net from invoice": condiciones ya
        # empieza con esa frase, y ponerla dos veces salia en el PDF repetida.
        filas += _fila(e, "Invoice &amp; Remittance:",
                       _esc_html(cond.split("\n")[0]))
    return _contenedor(e, "LOGISTICS &amp; COMPLIANCE BLUEPRINT", filas)


def _caja(e, texto):
    """
    Bloque de texto con fondo, para instruccion o condiciones.

    Los saltos de linea del texto se convierten en parrafos: Paragraph()
    colapsa los "\n", y sin esto las cuatro instrucciones de despacho salian
    en un solo renglon larguisimo, imposible de leer en un documento que se
    le manda a un proveedor.
    """
    lineas = [l.strip() for l in str(texto).split("\n") if l.strip()]
    if not lineas:
        lineas = [""]
    return Table(
        [[Paragraph(l, e["celda"])] for l in lineas],
        colWidths=[LETTER[0] - config.PDF_MARGEN_IZQUIERDO
                   - config.PDF_MARGEN_DERECHO],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7F8FA")),
            ("BOX", (0, 0), (-1, -1), 0.4, GRIS_LINEA),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]),
    )


MESES_PDF = ("January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December")

ZONAS = {"-04:00": "EDT", "-05:00": "EST", "-06:00": "CST", "-07:00": "PDT",
         "-08:00": "PST", "+00:00": "UTC", "Z": "UTC"}


def _fecha_pdf(bruto) -> str:
    """
    "2026-11-04T17:00:00-04:00" -> "November 4, 2026 at 5:00 PM EDT"

    En un documento que se manda a un distribuidor, una fecha en ISO no se
    lee. Si no se puede interpretar se devuelve tal cual, para no perder el
    dato: es mejor un ISO feo que una fecha inventada.
    """
    if not bruto:
        return ""
    from datetime import datetime

    texto = str(bruto).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(texto)
    except (ValueError, TypeError):
        return str(bruto)
    zona = ZONAS.get(texto[-6:], "") if len(texto) >= 6 else ""
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    salida = f"{MESES_PDF[dt.month - 1]} {dt.day}, {dt.year} at {h}:{dt.minute:02d} {ampm}"
    return f"{salida} {zona}".strip()


# Lo que sale en un dato que NO SE HA PODIDO LEER.
#
# Es un hueco DELIBERADO y visible, no un 1 y no un 0. Un "1" en la columna QTY
# de una hoja de cotizacion es un error de cien unidades que no se ve, porque
# parece un dato; un 0 es peor, porque ademas es un precio falso. El hueco se
# ve, se rellena, y es lo unico honesto cuando el aviso no dice la cantidad.
HUECO = "___"


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _fmt(v) -> str:
    """Numero sin decimales si es entero."""
    n = _num(v)
    return f"{int(n):,}" if n == int(n) else f"{n:,.2f}"


def _usd(v) -> str:
    return f"${_num(v):,.2f}"


def guardar(datos: dict, ruta: str, db_path: str | None = None) -> str:
    """Genera el PDF y lo escribe en disco. Devuelve la ruta."""
    with open(ruta, "wb") as fh:
        fh.write(generar_po(datos, db_path))
    return ruta


def nombre_archivo(numero_po: str) -> str:
    """Nombre seguro para el fichero, sin espacios ni simbolos raros."""
    import re as _re
    base = _re.sub(r"[^A-Za-z0-9\-]+", "-", str(numero_po or "")).strip("-")
    return f"{base or 'purchase-order'}.pdf"


# --- Extraccion de CLIN, NSN y unidades desde la descripcion de SAM.gov ---
#
# Se buscan por patron porque los avisos brings la tabla de items en formatos
# distintos y no hay una estructura fija. El orden importa: primero el NSN,
# que es lo mas fiable y lo que el comprador necesita para cotizar, y despues
# las demas.
_RE_CLIN = re.compile(r"\b(?:CLIN|LINE ITEM|ITEM)\s*(?:NUMBER|NO\.?|#)?\s*[:#]?\s*"
                      r"(\d{1,4}[A-Za-z]?)\b", re.IGNORECASE)
_RE_NSN = re.compile(r"\b(\d{4}-\d{2}-\d{3}-\d{4})\b")
_RE_NSN_COMPACTO = re.compile(r"\bNSN\s*[:#]?\s*(\d{4})\s*[- ]?\s*(\d{2})"
                              r"\s*[- ]?\s*(\d{3})\s*[- ]?\s*(\d{4})\b",
                              re.IGNORECASE)
_RE_PARTNO = re.compile(r"\b(?:PART|P/N|PN|MODEL|MFR PART)\s*(?:NUMBER|NO\.?)?"
                        r"\s*[:#]?\s*([A-Z0-9][A-Z0-9./-]{3,24})\b",
                        re.IGNORECASE)
_RE_UOI = re.compile(r"\b(?:UOI|UNIT OF ISSUE|UNIT|MEASURE)\s*[:#]?\s*"
                     r"([A-Z]{1,4})\b", re.IGNORECASE)
# Las unidades reales de los catalogos de SAM.gov. Se acotan porque si se
# busca "UNIT" a secas salen cosas como "UNITED STATES", que como unidad de
# medida no significan nada.
_UOIS = {
    # Las UOI de catalogo militar, que es donde aparecen de verdad.
    "EA", "BG", "KT", "KTN", "RO", "LOT", "SET", "PR", "CS", "CA", "BX",
    "PL", "PK", "DZ", "BO", "HD", "CON", "CYC", "NUC", "OQ", "PG", "BL",
    # Embalaje y medida.
    "BB", "BD", "BT", "CC", "CT", "DR", "GL", "PKG", "SC", "WT",
    "DZN", "DRL", "L", "M", "FT", "IN", "YD", "MI", "KG", "LB", "G",
    "LT", "ML", "GA", "OZ", "PT", "QT", "T",
    # "LO" es como aparece "lot" en algunos catalogos. Va en _UOIS y no solo en
    # _UOI_ISSUE porque la comprobacion que decide si una unidad es valida mira
    # _UOIS: con "LO" solo en _UOI_ISSUE, "Qty: 1 LO" se quedaba sin unidad, que
    # es lo mismo que quedarse sin cantidad.
    "LO",
}

# Palabras que USA la gente, traducidas a la sigla que usa el catalogo.
#
# El motivo: la columna UOI del PDF tiene que llevar la sigla, porque es lo
# que el comprador busca para pedir el material. "250 BOX" con la unidad BOX en
# la celda es un dato que no casa con nada; con BX si.
#
# No se hace lo contrario (meter BOX en _UOIS) porque entonces la celda
# mostraria una sigla que no existe en el catalogo y el comprador no encontraria
# el material por ella.
_SINONIMOS_UNIDAD = {
    "BOX": "BX", "BOXES": "BX", "BAG": "BG", "BAGS": "BG",
    "EACH": "EA", "EACHES": "EA", "KIT": "KT", "KITS": "KT",
    "SETS": "SET", "PAIR": "PR", "PAIRS": "PR",
    "LOTS": "LOT", "ROLL": "RO", "ROLLS": "RO",
    "KILOGRAM": "KG", "KILOGRAMS": "KG", "POUND": "LB", "POUNDS": "LB",
    "LITER": "L", "LITERS": "L", "LITRE": "L", "LITRES": "L",
    "GALLON": "GL", "GALLONS": "GL", "METER": "M", "METERS": "M",
    "METRE": "M", "METRES": "M", "FOOT": "FT", "FEET": "FT",
    "INCH": "IN", "INCHES": "IN", "YARD": "YD", "YARDS": "YD",
    "OUNCE": "OZ", "OUNCES": "OZ", "CONTAINER": "CON", "CONTAINERS": "CON",
    "BARREL": "HD", "BARRELS": "HD", "CAN": "CA", "CANS": "CA",
    "CASE": "CS", "CASES": "CS", "CARTON": "CT", "CARTONS": "CT",
    "PACKAGE": "PK", "PACKAGES": "PK", "BOTTLE": "BT", "BOTTLES": "BT",
    "DRUM": "DR", "DRUMS": "DR", "CYLINDER": "HD", "CYLINDERS": "HD",
    "BUNDLE": "BD", "BUNDLES": "BD", "SHEET": "SH", "SHEETS": "SH",
    # "Quantity: 100 Units" es 100 each en cualquier catalogo federal.
    "UNITS": "EA", "UNIT": "EA",
    "LOTS": "LOT", "LOT ": "LOT",
}

# "100 EA", "100 BG", "50 kg", "12 ea", "250 BOX".
#
# re.I anadido. Antes era sensible a mayusculas, asi que "100 ea" y "100 kg"
# en minusculas no se leian y la celda caia al "EA" por defecto, que es un
# dato fiable que en realidad esta mal. Con \b las colisiones son
# improbables: "20 MILITARY" no casa, porque detras de M viene I, que es
# caracter de palabra, y \b no cierra ahi.
#
# El grupo de unidades es el mismo conjunto que _UOIS, en el mismo orden: si
# una unidad esta en _UOIS pero no en el patron, se reconoce como UOI suelta y
# no como cantidad. Se construye a partir del conjunto para que no puedan
# separarse.
# El patron acepta las siglas de _UOIS y las palabras de _SINONIMOS_UNIDAD, y
# luego se canoniza el resultado. Las mas largas van primero para que "BOXES"
# no se lea como "BOX" + "ES".
# UOI DE EMPAQUE, que son las unicas que pueden ser la cantidad que se PIDE.
#
# Las de medida quedan fuera a proposito. En una ficha de SAM.gov la prosa
# tecnica trae dimensiones todo el rato ("FOR ROTOR OF 6 INCH PUMP"), y como la
# descripcion llega en un bloque unico, el primer "numero + sigla" que aparece
# es el de la dimension, no el de la cantidad.
#
# Medido con la ficha real de W519TC27QA012:
#
#     _cantidad_de_linea(BLOB) = (6.0, 'IN')
#
# Se leia una cantidad de 6 pulgadas donde el aviso pedia 100 unidades. "QTY 6
# IN" no es una cantidad pedible en una cotizacion federal; "QTY 6 EA" si. Por
# eso el patron de numero pegado a sigla solo acepta unidades de empaque.
_UOI_ISSUE = {
    "EA", "BG", "KT", "KTN", "SET", "PR", "CA", "CS", "BX", "PK", "PL", "DZ",
    "BO", "HD", "CON", "CYC", "NUC", "OQ", "PG", "BL", "LOT", "RO", "BB",
    "BD", "BT", "CC", "CT", "DR", "PKG", "SC", "WT", "DZN", "DRL", "QA", "RL",
    # "LO" es como aparece "lot" en algunos catalogos, y sin ella una linea
    # "Qty: 1 LO" se quedaba SIN UNIDAD, que es lo mismo que sin cantidad: el
    # hueco que el distributor tiene que rellenar a mano.
    "LO",
}
_SINONIMOS_ISSUE = {k: v for k, v in _SINONIMOS_UNIDAD.items()
                    if v in _UOI_ISSUE}

# Solo unidades de EMPAQUE. Ver _UOI_ISSUE: con la ficha en un bloque unico,
# "6 INCH PUMP" se leia como la cantidad.
_TODAS_UNIDADES = sorted(set(_UOI_ISSUE) | set(_SINONIMOS_ISSUE),
                         key=len, reverse=True)
_RE_CANT = re.compile(
    r"\b(\d{1,7}(?:,\d{3})*)\s*("
    + "|".join(re.escape(u) for u in _TODAS_UNIDADES) + r")\b", re.I)

# "Quantity: 100", "QTY 100", "Estimated quantity 2,000".
#
# Sin unidad detras. Antes el patron la exigia, asi que una linea que solo
# dijera la cantidad se quedaba sin cantidad y caia al fallback de 1. Un "1"
# en una hoja de cotizacion es un error de mil unidades, y no se ve: parece
# un dato.
_RE_CANT_SOLA = re.compile(
    r"\b(?:QUANTITY|QTY|ESTIMATED\s+QUANTITY|ESTIMATED\s+QTY"
    r"|QUANTITIES|AMOUNT\s+OF|QUANTITY\s+REQUIRED)\b"
    r"\s*(?:REQUIRED\s*)?(?::|=|IS\s+)?\s*([\d,]{1,10})\b", re.I)


# "Quantity: 100", "Qty 100 BG", "QUANTITY REQUIRED 12 EA".
#
# Es el patron que mas manda, y va el primero. Una cantidad ETIQUETADA no se
# puede confundir con una dimension de la prosa tecnica.
_RE_CANT_ETIQUETA = re.compile(
    r"\b(?:QUANTITY|QTY|ESTIMATED\s+QUANTITY|ESTIMATED\s+QTY)\b"
    r"\s*(?:REQUIRED\s*)?(?:IS\s+)?(?::|=)?\s*([\d,]{1,10})"
    r"\s*(?:([A-Za-z]{1,8})\b)?", re.I)


def _a_float(bruto) -> float:
    """'1,200' -> 1200.0, y 0.0 si no hay numero usable."""
    try:
        return float(str(bruto or "").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


# "2-20 foot Containers", "4-10 inch rings", "3-6 inch bolts".
#
# Es la forma estandar de EE.UU. para decir "N piezas, cada una de M": el numero
# ANTES del guion es la cantidad y el de DESPUES es la medida.
#
# Anclado con ^ a proposito. En medio de una frase un "5-10" puede ser un numero
# de plano, una referencia de parrafo o un rango de anyos, y leerlo como
# cantidad seria inventar un numero en un documento que va a un distribuidor.
#
# Y solo si detras de los dos numeros viene una palabra de MEDIDA, que es lo que
# separa "2-20 foot" de "MIL-STD-5-10" o de "2024-2025".
#
# El patron exige DOS numeros separados por guion. Por eso "20-FOOT CONTAINER
# WITH GANTRY BEAM", que es el titulo de W912ES27QA004, NO dispara: ahi hay un
# numero y un guion, no dos numeros. Si no lo exigiera, ese titulo daria 20
# contenedores en vez de 2.
_MEDIDAS = (
    r"foot|feet|ft|inch|inches|in|meter|metre|meters|metres|yard|yards|"
    r"centimeter|centimetre|millimeter|millimetre|cm|mm"
)
_RE_RANGO = re.compile(
    r"^\s*(\d{1,6})\s*[-/]\s*\d{1,6}\s*(?:" + _MEDIDAS + r")\b", re.I)

# El mismo patron SIN ancla, para la descripcion COMPLETA. En el aviso real el
# rango va precedido de una frase ("This solicitation is for 2-20 foot
# Containers"), asi que con el ancla no se ve: medido, daba 0.
#
# Lo que protege de los tramposos NO es el ancla sino la palabra de MEDIDA, que
# es lo que hace que "MIL-STD-5-10", "2024-2025" y "Paragraph 5-10" no casen.
_RE_RANGO_LIBRE = re.compile(
    r"(\d{1,6})\s*[-/]\s*\d{1,6}\s*(?:" + _MEDIDAS + r")\b", re.I)


def _cantidad_de_rango(texto: str, libre: bool = False) -> tuple[float, bool]:
    """
    Cantidad y si viene de un patron de rango al principio. (0.0, False).

    Se comprueba el TITULO y la DESCRIPCION, porque en "20-FOOT CONTAINER WITH
    GANTRY BEAM" el titulo solo trae la medida, y la cantidad no aparece hasta
    la descripcion ("2-20 foot Containers").

    Se acepta el prefijo de clase de SAM.gov ("53--") por si el rango llega
    detras de el, aunque hasta ahora no ha ocurrido.
    """
    t = str(texto or "")
    if libre:
        m = _RE_RANGO_LIBRE.search(t)
    else:
        m = _RE_RANGO.match(t)
        if not m:
            m = _RE_RANGO.match(re.sub(r"^\s*\d{2,4}\s*--\s*", "", t))
    if not m:
        return 0.0, False
    try:
        v = float(m.group(1))
    except ValueError:
        return 0.0, False
    return (v, True) if 0 < v <= 1_000_000 else (0.0, False)


def _cantidad_de_linea(linea: str) -> tuple[float, str]:
    """
    Cantidad y unidad de UN CLIN. (0.0, "") si no hay.

    EL ORDEN IMPORTA, Y ESTA MEDIDO

Con la descripcion en un bloque unico, buscar "numero pegado a sigla" por todo
el texto devolvia lo primero que encontraba, y eso era "6 INCH PUMP": seis
pulgadas, la dimension de un rotor. Se leia 6 IN donde el aviso pedia 100.

Asi que se busca en este orden:

    1. cantidad CON ETIQUETA   "Quantity: 100", "Qty 100 BG"
    2. cantidad con etiqueta y sin unidad
    3. numero pegado a una UOI DE EMPAQUE, nunca de medida

El paso 1 va primero porque una cantidad etiquetada no se confunde con nada.
El paso 3 va el ultimo porque es el que se confunde.
    """
    texto = str(linea or "")
    if not texto.strip():
        return 0.0, ""

    # El rango va PRIMERO. Si el principio del aviso dice "2-20 foot Containers",
    # esa es la cantidad, y un "Quantity: 100 units" que aparezca mas abajo
    # puede estar hablando de otra cosa: un total del contrato, un estimado.
    # El dato que esta al principio es el que describe el item.
    _rango, _es_rango = _cantidad_de_rango(texto)
    if _es_rango:
        return _rango, "EA"

    m = _RE_CANT_ETIQUETA.search(texto)
    if m:
        cant = _a_float(m.group(1))
        if cant:
            uni = ""
            if m.group(2):
                u = _SINONIMOS_UNIDAD.get(m.group(2).upper(), m.group(2).upper())
                if u in _UOIS:
                    uni = u
            return cant, uni

    m = _RE_CANT_SOLA.search(texto)
    if m:
        cant = _a_float(m.group(1))
        if cant:
            return cant, ""

    m = _RE_CANT.search(texto)
    if m:
        cant = _a_float(m.group(1))
        if cant:
            uni = _SINONIMOS_UNIDAD.get(m.group(2).upper(), m.group(2).upper())
            return cant, (uni if uni in _UOI_ISSUE else "")
    return 0.0, ""


# Las enmiendas de SAM.gov -----------------------------------------------
#
# "AMD 0001 Narrative..." es la narrativa ADMINISTRATIVA de una enmienda: para
# que se ha emitido, que anexos se cambian, a quien hay que responder. No es
# especificacion tecnica, y ocupa mas pagina que la especificacion.
#
# El dano medido: la descripcion se cortaba a los 400 caracteres sin mirar que
# habia, y la narrativa de una enmienda va al principio. Los 400 primeros
# caracteres del BOM podian ser los cuatro parrafos de "esta enmienda se emite
# para...", y el material quedaba fuera. La celda se saturaba y la linea que lee
# el comprador quedaba en nada.
#
# Se separa en dos cosas, que es como lo pide el encargo: la cabecera (una
# frase con modelo, producto y plano) va en la linea principal, y la narrativa
# administrativa se queda fuera.
_RE_AMD = re.compile(r"\bAMD\s*0*\d{1,4}\b", re.I)

# "Narrative..." es la etiqueta que sigue al numero de enmienda. No es texto, es
# una cabecera, y con el texto de al lado pegado forma un bloque que no dice
# nada. Se quita con ^ y re.M para que solo affect a la linea que empieza asi.
_RE_ETIQUETA_AMD = re.compile(r"^\s*(?:Narrative|Narratives)\b[.:]?\s*", re.I | re.M)

# Lineas que son tramitacion, no especificacion.
_RE_ADMIN = re.compile(
    r"\b(this\s+amendment|the\s+purpose\s+of\s+this|is\s+being\s+issued"
    r"|are\s+being\s+issued|issued\s+(in\s+response|to\s+clarify|to\s+answer"
    r"|to\s+extend|to\s+incorporate)|the\s+purpose\s+of\s+this"
    r"|interested\s+parties|point\s+of\s+contact|questions\s+should\s+be"
    r"|no\s+response\s+is\s+required|acknowledge\s+receipt"
    r"|changes?\s+are\s+highlighted|see\s+attached|end\s+of\s+amendment"
    r"|attachments?\s+are\s+included|additional\s+time|provides?\s+additional"
    r"|questions?\s+(should|must|may)\b|direct(?:ed)?\s+to\b)\b", re.I)


def _sin_enmiendas(texto: str) -> tuple[str, int]:
    """
    Quita las CABECERAS de enmienda. Devuelve (texto_limpio, numero_enmiendas).

    NO se quita el tramite aqui: solo las etiquetas ("AMD 0001",
    "Narrative..."). El tramite lo quita despues _sin_tramitacion(), que va por
    FRASES y por eso funciona tambien con un bloque unico.

    EL FALLO QUE ARREGLA, MEDIDO

Esta funcion dividia por LINEAS y descartaba las que casaban con el patron de
tramite. Con la descripcion en un bloque unico, la unica linea era toda la
ficha, contenia "amendment", y se descartaba ENTERA. Entonces el respaldo
devolvia el texto crudo:

    if not salida:
        return t.strip(), n        <- aqui se deshacia toda la limpieza

Y el texto crudo es justo lo que lleva la enmienda a la celda: el respaldo que
existia para no devolver nada era lo que contaminaba el documento. Medido con
la ficha real de W519TC27QA012:

    'AMD 0001 Narrative... Line Item Number 0001: NSN ...'

Sin respaldo. Si tras quitar las cabeceras no queda nada, se devuelve "".
    """
    t = str(texto or "")
    if not t.strip():
        return "", 0
    marcas = list(_RE_AMD.finditer(t))
    if not marcas:
        return t, 0
    cortes = [m.start() for m in marcas] + [len(t)]
    inicio, piezas = 0, []
    for c in cortes:
        b = t[inicio:c]
        b = _RE_AMD.sub(" ", b)
        b = _RE_ETIQUETA_AMD.sub("", b)
        b = b.strip(" .;\n")
        if b:
            piezas.append(b)
        inicio = c
    return ("\n".join(piezas), len(marcas))


_RE_CORTE = re.compile(r"(?<=[.;])\s+|\n+")


def _sin_tramitacion(texto: str, max_frases: int = 10) -> str:
    """
    Deja solo las frases que describen el material, quitando la tramitacion.

    POR QUE TROCEA EN FRASES Y NO EN LINEAS

La descripcion de SAM.gov (noticedesc) llega como UN SOLO BLOQUE, sin saltos de
linea. Con un filtro por lineas, la unica linea del aviso empezaba por "This
amendment is being issued to...", el patron de tramite casaba, y se
descartaba EL AVISO ENTERO:

    _sin_tramitacion -> 0 lineas
    bom[0].especificacion = ''

Y como el respaldo de desde_aviso() ante una especificacion vacia era el texto
crudo, la enmienda acababa en la celda del material.

Con troceo por oracion funciona igual en los dos casos, y ahora se prueban los
dos. La version multilinea pasaba las pruebas y la de bloque unico no, que es
justo la que produce la API.

Filtra por FRASES completas, no por palabras sueltas. La alternativa, tirar
cualquier linea que contenga "amendment", se llevaba por delante media
especificacion tecnica: medio averaged es medio bueno.
    """
    out = []
    for trozo in _RE_CORTE.split(str(texto or "")):
        s = (trozo or "").strip()
        if not s:
            continue
        if _RE_ADMIN.search(s):
            continue
        # Una frase de especificacion tiene que tener ALGO de substance. La
        # etiqueta "Narrative..." de una enmienda deja un "..." suelto al
        # quitarle el "AMD 0001" de delante, y un "..." en la celda de la linea
        # de cobro es basura visible.
        if len(re.sub(r"[^0-9A-Za-z]", "", s)) < 3:
            continue
        out.append(s)
        if len(out) >= max_frases:
            break
    return " ".join(out)


def _ref_plano(texto: str) -> str:
    """
    El numero de plano de la especificacion, si lo hay. "" si no.

    Se exige que el identificador contenga AL MENOS UN DIGITO, y se comprueba
    despues en Python y no en la regex. Sin esa exigencia, "Planning" se
    lee como plano "anning", y basta con que el aviso mencione "plan de
    entrega" para que la cabecera del producto termine con un numero inventado.
    Un plano mal identificado es peor que ningun plano: el distribuidor
    busca ese numero, no encuentra nada, y devuelve la hoja vacia.
    """
    m = re.search(
        r"\b(?:DRAWING|DWG|PLANO|PLAN|FIGURE)\s*"
        r"(?:NUMBER|NUMERO|NUM|NO\.?|#)?\s*[:#]?\s*"
        r"([A-Z0-9][A-Z0-9./_-]{2,30})", str(texto or ""), re.I)
    if not m:
        return ""
    ref = m.group(1).rstrip(".,;:")
    return ref if any(c.isdigit() for c in ref) else ""


def _cabecera_producto(titulo: str, modelo: str, plano: str) -> str:
    """
    LA LINEA PRINCIPAL de la cella: modelo + producto + plano, en una frase.

    Es lo unico que el comprador lee antes de decidir seguir. Por eso tiene que
    caber en una o dos lineas y no pueda ser un parrafo.

    El prefijo numerico de SAM.gov ("53--PARTS KIT,SEAL...") se quita: el 53
    es el codigo de clase, ya sale en otra columna del aviso, y aqui solo
    ocupa sitio y confunde a un lector que no lo espera.

    El plano se pone COMO PIE, separado por guion, no intercalado: si se metiera
    en medio del nombre, "PARTS KIT, Per Drawing MS-3305, SEAL REPLACEMENT"
    parece el nombre del producto y no una referencia.
    """
    t = " ".join(str(titulo or "").split())
    # SOLO se quita el prefijo de clase, que es "53--", con DOS guiones. Con
    # uno o dos guiones se comia el 20 de "20-FOOT CONTAINER WITH GANTRY BEAM" y
    # el titulo salia "FOOT CONTAINER WITH GANTRY BEAM", que no es el nombre de
    # nada. El numero ahi es la medida del contenedor, no un codigo.
    t = re.sub(r"^\d{2,4}\s*--\s*", "", t).strip(" -,")
    # SAM.gov escribe los titulos sin espacio tras la coma
    # ("PARTS KIT,SEAL REPLACEMENT,MECHANICAL"). Se lo pone porque el
    # distributor lee esto y "KIT,SEAL" se le junta. No se hace nada mas: el
    # texto del titulo se respeta, solo se le da aire.
    t = re.sub(r",(?=\S)", ", ", t)
    if not t:
        t = "Item as described in the attached specification"
    partes = []
    m = str(modelo or "").strip()
    if m and m.lower() not in t.lower():
        partes.append(m)
    partes.append(t)
    ref = str(plano or "").strip()
    if ref and ref.lower() not in t.lower():
        partes.append("Per Drawing %s" % ref)
    return " - ".join(partes)


def _marcar_lote(item) -> None:
    """
    CAPA A: poner la bandera consolidated_lot en un item del BOM, o en todos.

    Existe por una razon: la condicion ("cantidad == 1 y la unidad es EA, LO o
    LOT") tiene que estar escrita UN SOLO sitio. Ponerla en los dos puntos donde
    nace un item significa que las dos ramas pueden divergir sin que nada lo
    note, y acabaria activandose en una y no en la otra.

    Se escribe en el item y no se devuelve, porque el item ya se copia entero a
    varias partes del documento; anadirlo al elemento es lo que lo hace viajar
    sin tocar ninguna de ellas.

    Acepta un item o una lista, y no por comodidad. Las dos ramas de
    _bom_desde_aviso construyen distinto: una envuelve el item en una lista de
    uno y la otra acumula. Acordarse de cual es cual en cada llamada es la forma
    facil de que un dia se pase un item por alto y una rama deje de marcar,
    que es el fallo que no se ve hasta que un lote no se desglosa.
    """
    try:
        from lote_consolidado import es_lote_consolidado
    except Exception:
        # Si el modulo no estuviera, nada se marca, que es exactamente el
        # comportamiento de antes. La bandera es una capa, no un requisito.
        marca = lambda d: False
    else:
        marca = es_lote_consolidado

    if isinstance(item, list):
        for _uno in item:
            if isinstance(_uno, dict):
                _uno["consolidated_lot"] = marca(_uno)
        return
    if isinstance(item, dict):
        item["consolidated_lot"] = marca(item)


def _bom_desde_aviso(aviso: dict, descripcion: str = "") -> list[dict]:
    """
    Saca la lista de items (CLIN, NSN, cantidad, unidad) de un aviso de SAM.gov.

    Devuelve [] si no se encuentra nada utilizable: en ese caso el documento
    se genera igualmente, sin seccion de cotizacion, en vez de con una tabla
    vacia que solo confunde.
    """
    texto = descripcion or ""
    # La busqueda se hace sobre las primeras lineas, que es donde van las
    # tablas de items. Un aviso con descripcion larga puede tener el mismo NSN
    # citado mas de una vez en texto posterior.
    lineas = [l.strip() for l in texto.splitlines() if l.strip()][:60]

    nsn = _RE_NSN.search(texto)
    parte = nsn.group(1) if nsn else ""
    if not parte:
        m = _RE_NSN_COMPACTO.search(texto)
        if m:
            parte = "-".join(m.groups())

    # La cantidad global se lee del texto entero y se usa SOLO como respaldo
    # para los CLIN cuya linea no traiga la suya. Antes salia de un unico
    # _RE_CANT sobre todo el texto, que con "100 BG" no encontra nada porque BG
    # no estaba en la lista, y caia al 1.
    # El rango manda. Se mira ANTES que la cantidad global y antes que cada
    # CLIN, porque cuando un aviso dice "2-20 foot Containers" esta describiendo
    # UN item con dos unidades, no dos lineas.
    #
    # Y la unidad se fija en EA. "CON" casi no lo usa ningun distributor, y "2
    # CON" no es una unidad de catalogo: el item se pide UNO de cada vez.
    _rango_txt, _hay_rango = _cantidad_de_rango(texto, libre=True)
    if not _hay_rango:
        _rango_txt, _hay_rango = _cantidad_de_rango(
            (aviso.get("title") or "").strip())
    if _hay_rango:
        cant, unidad = _rango_txt, "EA"
    else:
        cant, unidad = _cantidad_de_linea(texto)
        if not unidad:
            m = _RE_UOI.search(texto)
            if m and m.group(1).upper() in _UOIS:
                unidad = m.group(1).upper()

    pn = _RE_PARTNO.search(texto)
    if pn and not parte:
        parte = pn.group(1)

    # La narrativa de las enmiendas se aparta ANTES de buscar nada. No solo
    # porque no sirve para cotizar: es que va al principio del texto, asi que
    # cualquier busqueda global (cantidad, NSN, parte) leia primero el papel.
    texto, n_enmiendas = _sin_enmiendas(texto)
    cuerpo = _sin_tramitacion(texto)
    plano = _ref_plano(texto)

    # Un item por CLIN, y CADA UNO CON SUS PROPIOS DATOS.
    #
    # Antes se sacaba cantidad, unidad y NSN UNA vez de todo el texto, con el
    # primer "50 EA" que apareciera, y se le ponia lo mismo a todos los CLIN.
    # Medido: el CLIN 0002 que dice "Quantity 10 KT" salia como 50 EA. En un
    # documento de cotizacion eso no es un detalle: el comprador pide precio
    # de algo que el PDF no dice que sea, y devuelve la linea mal.
    #
    # Se lee linea a linea: cada CLIN se lleva sus numeros. Solo si en SU
    # linea no hay nada se cae al valor global del texto, y si tampoco hay, a
    # 1 EA, que el comprador puede rellenar. Nunca se inventa una cifra.
    clines = []
    for l in lineas:
        m = _RE_CLIN.search(l)
        if m and m.group(1) not in clines:
            clines.append(m.group(1))

    def _datos_de(linea: str) -> dict:
        """Cantidad, unidad y parte de UN CLIN, leyendo solo su linea."""
        cant, uni = _cantidad_de_linea(linea)
        if not uni:
            mu = _RE_UOI.search(linea)
            if mu:
                _u = mu.group(1).upper()
                _u = _SINONIMOS_UNIDAD.get(_u, _u)
                if _u in _UOIS:
                    uni = _u
        nsn_linea = _RE_NSN.search(linea)
        if nsn_linea:
            parte_linea = nsn_linea.group(1)
        else:
            m2 = _RE_NSN_COMPACTO.search(linea)
            parte_linea = "-".join(m2.groups()) if m2 else ""
        if not parte_linea:
            mp = _RE_PARTNO.search(linea)
            parte_linea = mp.group(1) if mp else ""
        return {"cantidad": cant, "unidad": uni, "parte": parte_linea}

    def _linea_de(clin: str) -> str:
        """La linea donde aparece el CLIN, para leer sus datos."""
        for l in lineas:
            m = _RE_CLIN.search(l)
            if m and m.group(1) == clin:
                return l
        return ""

    titulo = (aviso.get("title") or "").strip()
    destino = lugar_de_entrega_aviso(aviso)

    # La linea principal: modelo + producto + plano, en una frase.
    #
    # El modelo va delante porque es lo que el comprador tecrico busca primero
    # para saber si lleva el material, y va de cabeza y no intercalado: si el
    # "Per Drawing X" se metiera en medio del nombre, "PARTS KIT, Per Drawing
    # MS-3305, SEAL REPLACEMENT" parece el nombre del producto y no una
    # referencia.
    modelo_linea = ""
    mp = _RE_PARTNO.search(texto)
    if mp:
        modelo_linea = mp.group(1)
    elif parte and "-" in parte:
        # Un NSN no es un modelo, pero es lo unico que hay si no hay P/N.
        modelo_linea = ""

    if not clines:
        # Sin CLIN: una sola linea con el producto. Es mejor un item que
        # describa lo que hay que cotizar que una tabla sin contenido.
        salida = [{
            "clin": "1",
            "parte": parte or "",
            "descripcion": _cabecera_producto(titulo, modelo_linea, plano),
            "especificacion": (cuerpo[:600].strip() if cuerpo else
                "No further description available from SAM.gov."),
            "cantidad": cant or 0.0,
            "unidad": unidad or "",
            "precio": "",
            "enmiendas": n_enmiendas,
            "modelo": modelo_linea,
        }]
        _marcar_lote(salida)
        return salida

    # Con CLIN: una linea por cada uno, con SUS datos.
    salida = []
    for i, c in enumerate(clines, start=1):
        _d = _datos_de(_linea_de(c))
        if _hay_rango:
            _d = {"cantidad": _rango_txt, "unidad": "EA",
                  "parte": _d["parte"]}
        # Si en su linea no habia nada, se cae al valor global del texto, y si
        # tampoco, a 1 EA. Se marca con vacio el dato que no se sabe, para que
        # el comprador lo rellene en vez de fiarse de una cifra inventada.
        _cant = _d["cantidad"] or cant or 0.0
        _uni = _d["unidad"] or unidad or ""
        _parte = _d["parte"] or (parte if i == 1 else "")
        # La cabecera limpia se repite en cada linea: una hoja de cotizacion
        # donde la linea 0003 no dice que material es, vuelve al distributor
        # con la mitad de las preguntas hechas.
        base_desc = _cabecera_producto(titulo, modelo_linea, plano)
        desc = base_desc if i == 1 else f"{base_desc} (item {c})"
        salida.append({
            "clin": c,
            "parte": _parte,
            "descripcion": desc,
            "especificacion": (cuerpo[:400].strip() if i == 1 and cuerpo else ""),
            "cantidad": _cant,
            "unidad": _uni,
            "precio": "",
            "enmiendas": n_enmiendas,
            "modelo": modelo_linea,
        })
    for _it in salida:
        _marcar_lote(_it)
    return salida


def lugar_de_entrega_aviso(aviso: dict) -> str:
    """
    Destino de entrega en ingles, a partir de placeOfPerformance o del titulo.

    Se juntan los tres campos porque cada agencia los usa distinto: unas ponen
    la ciudad en fullParentPathName, otras la.state, otras nada.
    """
    pop = aviso.get("placeOfPerformance") or {}
    partes = []
    for clave in ("city", "state"):
        v = pop.get(clave) if isinstance(pop, dict) else None
        if v and str(v).strip():
            partes.append(str(v).strip())
    if not partes:
        txt = str(aviso.get("fullParentPathName") or "")
        if txt:
            partes.append(txt.split(" / ")[-1].strip())
    if not partes:
        return ""
    sitio = ", ".join(partes)
    if not str(aviso.get("placeOfPerformance") or "").strip():
        return f"{sitio}, UNITED STATES"
    return f"{sitio}, UNITED STATES"


def _bom_desde_analisis(analisis: dict) -> list[dict]:
    """Bill of Materials a partir de un analisis de Kyomoto ya guardado."""
    cant = _num(analisis.get("cantidad_total")) or 1
    unidad = (analisis.get("unidad_medida") or "EA").upper()
    if unidad not in _UOIS:
        unidad = "EA"
    # El texto viene de Gemini, en castellano, asi que pasa por la misma capa
    # de traduccion que las lineas de la orden.
    #
    # El numero de modelo NO se traduce, y no es una excepcion por pereza: es
    # un identificador. Traducirlo seria fabricar un numero de parte que no
    # existe, y un distributoror que luego no encuentra el material contesta
    # lo que sea. Por eso el detector de castellano salta los identificadores
    # alfanumericos en vez de marcarlos como pendientes.
    # INGLES NATIVO DE SAM.GOV, no una traduccion.
    #
    # El orden es el que importa: primero lo crudo del aviso, que ya esta en
    # ingles tecnico de adquisiciones porque lo escribio un oficial; y solo si
    # no hay crudo, se recurre a _ingles() sobre lo que dio Gemini.
    #
    # Traducir texto de compra produce frases rare en ingles: "entrega en",
    # "descripcion de", palabras que existen pero que no suenan a catalogo de un
    # oficial. El original no tiene ese problema porque no es una traduccion.
    # PURIFICACION EN CASCADA, de mas fiable a menos:
    #
    #   1) description_original -> el texto tecnico crudo de la API de SAM.gov
    #   2) titulo_original      -> el titulo, tambien crudo
    #   3) producto             -> el BOM, que en un aviso en vivo ya sale de la
    #                              descripcion cruda, pero en uno guardado por
    #                              el barrido puede venir de Gemini
    #
    # El primero gana siempre. Lo que sale a la pagina 1 es ESO, no una version
    # traducida ni una reescritura.
    crudo = str(analisis.get("descripcion_original") or "").strip()
    if not crudo:
        crudo = str(analisis.get("titulo_original") or "").strip()
    # El [SEE NOTE] es un marcador interno de "esto no se pudo traducir".
    # NUNCA sale en un papel comercial: el distribuidor no sabe que significa y,
    # si lo lee, deduce que el texto no es de fiar.
    crudo = _limpiar_marcas(crudo)
    # RESPALDO EN INGLES, y solo si fallan los dos campos crudos.
    #
    # No es traduccion ni reescritura: es un texto generico que
    # describe lo que el documento ES, para que la celda no quede en
    # blanco y el distribuidor sepa que falta el detalle. Un hueco se
    # rellena con el hueco del proveedor; un texto inventado se rellena
    # con una mentira.
    desc = (crudo
            or _limpiar_marcas(analisis.get("producto") or "")
            or "Material and specifications as described in the solicitation "
               "attachments.")
    modelo = (analisis.get("modelo_especifico") or "").strip()
    if modelo and modelo.lower() not in desc.lower():
        desc = f"{desc} ({modelo})".strip()
    spec = (analisis.get("especificacion_tecnica_clave") or "").strip()

    if crudo:
        desc, _pd = desc[:_ACORTAR["descripcion"]], True
        # El recorte va con el texto crudo. _ACORTAR existe porque un
        # .replace() o un .sub() sobre una cadena larga puede ensanchar una fila
        # de la tabla mas alla del ancho de pagina y reventar con LayoutError.
        spec, _ps = spec[:_ACORTAR["especificacion"]], True
        if texto_en_espanol(desc) or texto_en_espanol(spec):
            log.info("desde_aviso: el texto crudo del aviso trae palabras en "
                     "espanol; se deja tal cual porque es la fuente oficial")
    else:
        desc, _pd = _ingles(desc, _ACORTAR["descripcion"])
        spec, _ps = _ingles(spec, _ACORTAR["especificacion"])
    return [{
        "clin": "1",
        "parte": modelo,
        "descripcion": desc or "Item as described in the attached specification",
        "especificacion": spec,
        "cantidad": cant,
        "unidad": unidad,
        "precio": "",
    }]


# --- Capa de traduccion al ingles tecnico -----------------------------------
#
# Se aplica SOLO al texto que produce Gemini, que es el unico que no controlo
# yo. El texto que el propio modulo genera (instrucciones, condiciones, notas)
# se escribe en ingles directamente: traducir una cadena que uno mismo acaba de
# escribir es un rodeo que solo puede salir mal.

_TRADUCCIONES = {
    # --- Las cuatro que pidio el usuario, con y sin tilde ---
    "Material de Rodamiento": "Bearing Material",
    "material de rodamiento": "bearing material",
    "Material de rodamientos": "Bearing Material",
    "Requiere Certificado de Conformidad (CoC) a la entrega":
        "Requires Certificate of Conformance (CoC) upon delivery",
    "requiere certificado de conformidad (CoC) a la entrega":
        "requires certificate of conformance (CoC) upon delivery",
    "Certificado de Conformidad (CoC)":
        "Certificate of Conformance (CoC)",
    "certificado de conformidad (CoC)":
        "certificate of conformance (CoC)",
    "Certificado de Conformidad": "Certificate of Conformance",
    "certificado de conformidad": "certificate of conformance",
    "Certificado de calidad": "Certificate of conformance to specification",
    # --- Planos,Normas y documentacion ---
    "plano de inspeccion": "inspection drawing",
    "Plano de inspeccion": "Inspection drawing",
    "plano de diseno": "design drawing",
    "planos de montaje": "assembly drawings",
    "referencia de plano": "drawing reference",
    "plano de referencia": "reference drawing",
    # --- Calidad e inspeccion ---
    "requisitos de calidad": "quality requirements",
    "control de calidad": "quality control",
    "nivel de calidad": "quality level",
    "procedimiento de inspeccion": "inspection procedure",
    "informe de inspeccion": "inspection report",
    "primera inspeccion": "first article inspection",
    "pruebas de aceptacion": "acceptance tests",
    "inspeccion visual": "visual inspection",
    "especificacion tecnica": "technical specification",
    "Especificacion tecnica": "Technical specification",
    # --- Materiales y acabado ---
    "material original": "original manufacturer material",
    "material nuevo": "brand new material",
    "kit de repuestos": "spare parts kit",
    "Kit de repuestos": "Spare Parts Kit",
    "sello mecanico": "mechanical seal",
    "junta mecanica": "mechanical gasket",
    "acero inoxidable": "stainless steel",
    "acero al carbono": "carbon steel",
    "material de fundicion": "casting material",
    "fundicion de precision": "precision casting",
    "reacondicionado": "refurbished",
    "reconstruido": "remanufactured",
    "original de fabrica": "factory original",
    "new or unused": "new or unused",
    # --- Suministro y entrega ---
    "plazo de entrega": "delivery lead time",
    "Plazo de entrega": "Delivery lead time",
    "entrega inmediata": "immediate delivery",
    "entrega parcial": "partial shipment",
    "embalaje apto para transporte": "packaging suitable for transportation",
    "embalaje apta para transporte": "packaging suitable for transportation",
    "conservar el producto": "preserve the material",
    "condiciones de entrega": "delivery conditions",
    # --- Administracion y comercial ---
    "periodo de garantia": "warranty period",
    "numero de serie": "serial number",
    "numero de parte": "part number",
    "Numero de parte": "Part Number",
    "codigo de parte": "part number",
    "codigo NSN": "NSN code",
    "orden de compra": "purchase order",
    "Orden de compra": "Purchase Order",
    "nueva fabricacion": "new manufacture",
    # --- Sustantivos: se pueden cambiar sueltos porque el ingles es el mismo
    #     o casi, y traduciarlos nunca rompe la frase ---
    "tolerancia": "tolerance",
    "tolerancias": "tolerances",
    "diametro": "diameter",
    "diametral": "diametral",
    "longitud": "length",
    "espesor": "thickness",
    "materiales": "materials",
    "generico": "generic",
    "defectuoso": "defective",
    "corroido": "corroded",
    "aprobado": "approved",
    "aprobada": "approved",
    "obligatorio": "mandatory",
    "obligatoria": "mandatory",
    "obligatorios": "mandatory",
    "requerido": "required",
    "requerida": "required",
    "requeridos": "required",
    "requeridas": "required",
    "opcional": "optional",
    # Palabras que son validas tal cual en los dos idiomas. Se ponen para que
    # el detector de castellano NO las marque como pendientes: "new",
    # "original", "lot", "revision" son ingles valido y no hay nada que hacer.
    "original": "original",
    "nuevo": "new",
    "nueva": "new",
    "lote": "lot",
    "revision": "revision",
    "inspeccion": "inspection",
    "inspección": "inspection",
    "calidad": "quality",
    "entrega": "delivery",
    "embalaje": "packaging",
    "marcado": "marking",
    "etiquetado": "marking",
    "cantidad": "quantity",
    "precio": "price",
    "fabricante": "manufacturer",
    "modelo": "model",
    "transporte": "transportation",
    "remision": "shipment",
    "garantia": "warranty",
}

# Las frases con un hueco variable se evaluan ANTES que el diccionario, que va
# palabra a palabra. "Segun plano MS-3305" tiene que convertirse entero en
# "Per Drawing", no en "Per plano MS-3305".
# --- PASADA 1: REFERENCIAS -----------------------------------------------------
#
# Primero de todo, y por un motivo concreto: son las unicas reglas que CAPTURAN
# un identificador. "plano numero MS-3305" tiene que convertirse en "Drawing
# MS-3305" antes de que ninguna otra pasada le toque el "plano" y se lleve el
# codigo por delante.
_REFERENCIAS_TRADUCCION = [
    (re.compile(r"\bseg[uú]n\s+plano\s+n[uú]mero\s+"
                r"([A-Z0-9][A-Z0-9./-]{2,24})", re.I),
     r"Per Drawing \1"),
    (re.compile(r"\bplano\s+n[uú]mero\s+([A-Z0-9][A-Z0-9./-]{2,24})", re.I),
     r"Drawing \1"),
    (re.compile(r"\bn[uú]mero\s+de\s+parte\s+([A-Z0-9][A-Z0-9./-]{2,24})",
                re.I), r"Part Number \1"),
    (re.compile(r"\bparte\s+n[uú]mero\s+([A-Z0-9][A-Z0-9./-]{2,24})", re.I),
     r"Part Number \1"),
]

# --- PASADA 2: VERBO + SUSTANTIVO ---------------------------------------------
#
# Ahora, y antes que las frases. El verbo es lo que da sentido a la frase
# entera: "plano de inspeccion" es "inspection drawing" SOLO si lo precede un
# "complies with". Sin el verbo es un sustantivo suelto, y si se ejecutara
# despues, "cumple plano de inspeccion" salia "Cumple Inspection Drawing",
# con el verbo sin traducir.
#
# El "planos?" con interrogacion cubre "plano" y "planos", y el grupo
# opcional cubre "cumple el plano", "cumple los planos", "cumple con el
# plano" y el "cumple plano" a secas, que es como sale en los avisos.
_VERBOS_TRADUCCION = [
    # El verbo con el sustantivo delante de el. Estas van PRIMERO entre los
    # verbos porque "plano de inspeccion" con un identificador detras tambien
    # existe como sustantivo suelto (ver el final de la lista), y si esa
    # ganara, el verbo se quedaria sin traducir.
    (re.compile(r"\bcumple\s+(?:con\s+)?(?:los?\s+|las?\s+|el\s+)?"
                r"planos?\s+de\s+inspecci[oó]n\b", re.I),
     "Complies with Inspection Drawing"),
    (re.compile(r"\bcumple\s+(?:con\s+)?(?:los?\s+|las?\s+|el\s+)?"
                r"planos?\s+de\s+montaje\b", re.I),
     "Complies with Assembly Drawing"),
    (re.compile(r"\bcumple\s+(?:con\s+)?(?:los?\s+|las?\s+|el\s+)?"
                r"planos?\s+de\s+montaje\b", re.I),
     "Complies with Assembly Drawing"),
    (re.compile(r"\bcumple\s+(?:con\s+)?(?:los?\s+|las?\s+|el\s+)?"
                r"planos?\b(?!\s+n[uú]mero)", re.I),
     "Complies with Drawing"),
    (re.compile(r"\bcumple\s+(?:con\s+)?(?:los?\s+|las?\s+|el\s+)?"
                r"especificaci[oó]n\b", re.I),
     "Complies with Specification"),
    (re.compile(r"\bde\s+acuerdo\s+con\s+(?:los?\s+|las?\s+|el\s+)?"
                r"planos?\s+de\s+inspecci[oó]n\b", re.I),
     "In Accordance with Inspection Drawing"),
    (re.compile(r"\bde\s+acuerdo\s+con\s+(?:los?\s+|las?\s+|el\s+)?"
                r"planos?\b", re.I),
     "In Accordance with Drawing"),
    (re.compile(r"\bde\s+acuerdo\s+con\s+(?:los?\s+|las?\s+|el\s+)?"
                r"especificaci[oó]n\b", re.I),
     "In Accordance with Specification"),
    (re.compile(r"\bseg[uú]n\s+(?:los?\s+|las?\s+|el\s+)?"
                r"planos?\b(?!\s+n[uú]mero)", re.I),
     "Per Drawing"),
    (re.compile(r"\bseg[uú]n\s+(?:los?\s+|las?\s+|el\s+)?normas?\b", re.I),
     "Per Standard"),
    (re.compile(r"\bseg[uú]n\s+(?:los?\s+|las?\s+|el\s+)?"
                r"especificaci[oó]n\b", re.I),
     "Per Specification"),
    (re.compile(r"\bse\s+requiere\b", re.I), "is required"),
    (re.compile(r"\bdebe\s+entregarse\b", re.I), "must be delivered"),
    (re.compile(r"\bdebe\s+entregar\b", re.I), "must be delivered"),
    (re.compile(r"\bdebe\s+incluir\b", re.I), "must include"),
    (re.compile(r"\bdebe\s+ser\b", re.I), "must be"),
    (re.compile(r"\bno\s+se\s+acepta\b", re.I), "is not accepted"),
    (re.compile(r"\bno\s+acepta\b", re.I), "does not accept"),
    (re.compile(r"\bde\s+acuerdo\s+con\b", re.I), "in accordance with"),
    # Sustantivo con identificador y SIN verbo delante. Van las ultimas a
    # proposito: si hay un verbo, las de arriba ya han actuado y aqui ya no
    # queda nada quecolgado. "plano de inspeccion DLA" sin verbo se lee
    # "Inspection Drawing DLA", que es correcto; con verbo, gana el verbo.
    (re.compile(r"\bplanos?\s+de\s+inspecci[oó]n\s+"
                r"([A-Z0-9][A-Z0-9./-]{2,24})", re.I),
     r"Inspection Drawing \1"),
    (re.compile(r"\bplanos?\s+de\s+montaje\s+"
                r"([A-Z0-9][A-Z0-9./-]{2,24})", re.I),
     r"Assembly Drawing \1"),
]

# --- PASADA 3 Y 4: EL DICCIONARIO, PARTIDO EN FRASES Y PALABRAS ---------------
#
# El diccionario se ordena una vez al cargar el modulo, no en cada llamada, y
# se parte en dos porque una palabra suelta NUNCA debe ganar a una frase. Si
# "entrega" se ejecutara antes que "a la entrega", esa palabra se traduciria
# dentro de una frase en la que significa "upon" y saldia "a la delivery".
_TRADUCCIONES_ORDENADAS = sorted(
    _TRADUCCIONES.items(), key=lambda kv: -len(kv[0]))
_FRASES_ORDENADAS = [(k, v) for k, v in _TRADUCCIONES_ORDENADAS if " " in k]
_PALABRAS_ORDENADAS = [(k, v) for k, v in _TRADUCCIONES_ORDENADAS if " " not in k]

# Marcadores de castellano. Son PALABRAS COMPLETAS con limites de palabra, no
# letras acentuadas: un NSN puede traer una "o" acentuada por el ruido de la
# API y marcaria el documento entero como pendiente.
# Palabras de CASTELLANO LIBRE, no solo vocabulario tecnico.
_FUNCIONES_ESPANOL = frozenset("""
de la el los las del para por sobre entre hasta desde segun
que una unos unas ser estar haber tener debe deben poden puede
cuando donde porque aunque tambien entonces cada todo toda todos todas
otro otra mismo misma este esta estos estas ese esa aquel aquella
nuestro nuestra sus mediante unicamente solamente mas muy solo sola
aun ya aqui alli ahi alla ademas ningun ninguna cualquiera alguien nada
""".split())

_PALABRAS_ESPANOL = frozenset("""
segun plano planos clase diseno inspeccion certificado certificacion
conformidad conforme calidad requisito requisitos obligatorio obligatoria
obligatorio rodamiento rodamientos mecanico mecanica producto productos
pieza piezas parte partes unidad unidades cantidad cantidades precio precios
costo costos entrega entregas remision envio expedicion embalaje
embalado transporte transportacion etiquetado marcado garantia facturacion
revision lote serie clases fabricante modelo orden compra aceptacion saliente
llegada salida
""".split())


def _queda_espanol(texto: str) -> list[str]:
    """
    Que palabras del castellano se han quedado sin traducir.

    Devuelve LAS PALABRAS, no un si/no. La diferencia se nota cuando hay que
    avisar: "queda texto sin traducir" obliga al usuario a buscarlo en el PDF,
    y "queda 'embalaje' y 'transporte'" le dice exactamente donde mirar.

    Los identificadores se saltan antes de mirar. Un NSN, un CAGE, un numero
    de plano y un numero de parte son cadenas alfanumericas: se leen igual en
    los dos idiomas, y marcarlos como pendientes haria que TODO documento
    saliera marcado, que es lo mismo que no marcar nada.
    """
    if not texto:
        return []
    limpio = re.sub(r"\b\d{4}-\d{2}-\d{3}-\d{4}\b", " ", texto)
    limpio = re.sub(r"\b[A-Z]{2,}[-/]?\d[A-Z0-9./-]{2,}\b", " ", limpio)
    limpio = re.sub(r"\b\d{3,}\b", " ", limpio)

    # El detector no puede vivir solo del vocabulario de adquisiciones: cuanto
    # mas raro es el castellano, menos probable es que este en la lista, que es
    # justo al reves de lo que hace falta. "Pantalones de gamuza para el
    # almirante" no tiene ni una palabra tecnica y se saltaba sin marcar.
    #
    # Lo que si esta en TODA frase castellana son las palabras de funcion: las
    # mas frecuentes del idioma, y casi ninguna existe en ingles. Se descartan
    # a proposito las que si son ingles o casi: "no" (puede ser N/O, "not
    # offered"), "con" (sustantivo raro), "tan" y "son".
    #
    # Se comparan en minuscula y sin acentos, que es como estan escritas.
    encontradas = []
    for palabra in re.findall(r"[A-Za-z]{2,}", limpio):
        b = palabra.lower()
        if b in _PALABRAS_ESPANOL and b not in encontradas:
            encontradas.append(b)
        elif b in _FUNCIONES_ESPANOL and b not in encontradas:
            encontradas.append(b)
    # Las vocales acentuadas y la enye, solo en palabras con minúscula: un
    # nombre propio en mayúsculas no es castellano pendiente.
    for palabra in re.findall(r"[A-Za-zÀ-ÿ]{3,}", limpio):
        if re.search(r"[áéíóúñ]", palabra) and not palabra.isupper():
            b = palabra.lower()
            if b not in encontradas:
                encontradas.append(b)
    return encontradas


# Longitud maxima de un texto en una celda de la tabla.
#
# El limite sale de la medida, no de gusto: la celda de especificacion mide
# unas 3 pulgadas y el texto va a 7,5 pt, asi que 1200 caracteres salen a
# unas veinte lineas, que caben de sobra en la pagina.
#
# Hay tope porque una fila de tabla mas alta que la pagina NO SE PUEDE PARTIR:
# reportlab no sabe donde cortarla y lanza LayoutError, y el usuario se queda
# sin documento. Medido con 3000 caracteres sin espacios:
#
#     LayoutError: Flowable <Table 3 rows x 6 cols(tallest row 802)>
#
# No es teorico. La especificacion tecnica la escribe Gemini y no hay tope en
# su salida, y una descripcion pegada desde un PDF de SAM.gov con los saltos
# de linea perdidos llega al mismo sitio.
#
# Recortar con marca visible es lo honesto. Dejar la fila mas alta que la
# pagina deja al usuario sin nada; truncar en silencio es lo peor de los tres,
# porque no se sabria que le falta texto. Y la marca lleva cuantos caracteres
# se han quedado fuera, para que el usuario vaya al aviso a buscarlos en vez
# de suponer que el documento los tenia.
_ACORTAR = {"descripcion": 400, "especificacion": 1200}


def _recortar(texto: str, limite: int) -> str:
    """Recorta a `limite` caracteres dejando constancia de lo que falta."""
    t = str(texto or "")
    if limite <= 0 or len(t) <= limite:
        return t
    return "%s... [%d more characters - full text at https://sam.gov]" % (
        t[:limite].rstrip(), len(t) - limite)


def _a_ingles(texto: str) -> str:
    """
    Traduce castellano tecnico a ingles. Lo que no reconoce, lo deja.

    No lanza nunca y nunca devuelve None: un texto vacio sigue siendo vacio, y
    un texto que no se puede tocar mas se devuelve tal cual para que la
    comprobacion de pendientes lo marque.
    """
    if not texto:
        return ""
    t = str(texto)
    # Las cuatro pasadas van en este orden y el orden NO es arbitrario. Se ha
    # medido: si se invierte cualquiera de las dos primeras, los propios
    # patrones que pidio el usuario salen a medias.
    for patron, destino in _REFERENCIAS_TRADUCCION:
        t = patron.sub(destino, t)
    for patron, destino in _VERBOS_TRADUCCION:
        t = patron.sub(destino, t)
    # El \b alrededor no es opcional: sin limites, "plan" se come "plane" y
    # "coc" se come "cocina". En este codigo las colisiones por subcadena se
    # repiten sin parar.
    for origen, destino in _FRASES_ORDENADAS:
        t = re.sub(r"\b%s\b" % re.escape(origen), destino, t, flags=re.I)
    for origen, destino in _PALABRAS_ORDENADAS:
        t = re.sub(r"\b%s\b" % re.escape(origen), destino, t, flags=re.I)
    return t


MARCA_PENDIENTE = "[SEE NOTE]"


def _ingles(texto: str, limite: int = 0) -> tuple[str, bool]:
    """
    Traduce, y si no puede traducirlo ENTERO, no lo traduce a medias.

    Devuelve (texto, pendiente).

    TODO O NADA, Y POR QUE

    Medido con un aviso real:

        entra : "Este aviso es una solicitud de cotizacion. Se solicita
                 material de rodamiento segun plano MS-3305. Cantidad: 50 KIT."
        sale  : "Este aviso es una solicitud de cotizacion. Se solicita Bearing
                 Material Per Drawing MS-3305. quantity: 50 KIT. [SEE NOTE]"

    Eso es lo PEOR de las tres opciones, y merece explicarse:

    - Traducirlo todo con un modelo de lenguaje seria lo ideal, pero aqui no
      se puede llamar a uno sin gastar la cuota de produccion.
    - No traducir nada seria honesto.
    - Traducirlo a medias parece correcto y no lo es. "Se solicita Bearing
      Material" esta en las dos lenguas a la vez, se lee bien de un vistazo, y
      manda a un distribuidor un texto que no es ninguno de los dos idiomas.
      Un tecnico que lo lea nota que "cantidad" quedo como "quantity" en
      minúscula en medio de una frase que ya no es castellana, y tiene que
      parar a preguntarse que pasa. Peor que no traducir, porque el effort de
      revisarlo es mayor y no se da cuenta de que falta informacion.

    Asi que: si despues de traducir queda UNA palabra de castellano, se
    devuelve el ORIGINAL entero con la marca, no la traduccion a medias. El
    usuario ve lo que dijo Gemini y decide. Lo que nunca se hace es inventar
    el equivalente en ingles.

    Lo que si se traduce entero, se traduce:

        "Material de Rodamiento M7A1"                -> "Bearing Material M7A1"
        "Segun Plano MS-3305. Requiere CoC a la      -> "Per Drawing MS-3305.
         entrega. Cumple plano de inspeccion DLA."      Requires CoC upon
                                                         delivery. Complies
                                                         with Inspection
                                                         Drawing DLA."

    `limite` recorta si el texto es mas largo, para que una celda no reviente
    la maquetacion. 0 significa que no se recorta. El recorte se aplica al
    original cuando se devuelve sin traducir, y a la traduccion cuando si.
    """
    t = _a_ingles(texto)
    if not t.strip():
        return "", False
    if _queda_espanol(t):
        # Se devuelve el ORIGINAL, no la traduccion parcial.
        original = str(texto or "")
        if limite:
            original = _recortar(original, limite)
        return "%s %s" % (original, MARCA_PENDIENTE), True
    if limite:
        t = _recortar(t, limite)
    return t, False


def _estimar_precio(descripcion: str, bom: list[dict]) -> dict:
    """
    Estima el precio unitario a partir del valor que DECLARA el aviso.

    Devuelve {} si el aviso no declara valor, y en ese caso el documento deja el
    precio en blanco. Se prefiere un hueco a un cero: un hueco se rellena, un
    cero se discute.

    De donde sale el dato: filters.valor_declarado(), la misma funcion que el
    barrido usa para aplicar el tope ANTES de gastar una llamada de Gemini. Se
    REUTILIZA en vez de reescribirla porque ya esta probada contra las trampas
    de estos textos ("Estimated quantity 2,000 ; Not to Exceed 350,000", donde
    la cantidad no es el contrato), y una copia divergiria en cuanto el texto
    real traiga otra forma.

    El unitario es el promedio sobre TODOS los CLIN, no el del primero. El valor
    declarado es el del contrato entero; dividirlo solo por el primer CLIN
    inflaria el precio de ese CLIN y dejaria los demas sin cubrir.
    """
    import filters

    valor, _fragmento = filters.valor_declarado(descripcion or "")
    if not valor or valor <= 0:
        return {}

    total = 0.0
    for b in (bom or []):
        try:
            total += float(b.get("cantidad") or 0)
        except (TypeError, ValueError):
            continue
    if total <= 0:
        return {}

    unitario = round(valor / total, 2)
    # Si sale tan pequeno que redondear a centimos lo deja en cero, a cuatro.
    # Un $0.00 seria exactamente el fallo que se esta arreglando.
    if unitario <= 0:
        unitario = round(valor / total, 4)
    if unitario <= 0:
        return {}

    return {
        "precio_unitario_costo": unitario,
        "costo_total_usd": round(unitario * total, 2),
        "precio_es_estimado": True,
        "valor_declarado_usd": valor,
        "cantidad_total_licitacion": total,
    }


def desde_aviso(aviso: dict, db_path: str | None = None) -> dict:
    """
    Traduce un aviso crudo de SAM.gov al formato de analisis que ya sabe leer
    desde_analisis().

    Existe porque /pdf antes solo miraba en la base local, y una solicitud que
    Kyomoto no habia analizado no existia para el comando. Con esta traduccion
    el aviso que llega de la API entra por el mismo camino que los queBring el
    barrido, y el documento sale igual de completo.

    Lo que NO HACE es inventar. Si la API no trae cantidad, se deja 1 y no se
    disfraza. Un distribuidor que ve "1 EA" sin mas datos pregunta; uno que ve
    una cantidad inventada cotiza sobre algo que no existe, y el problema sale
    mas tarde y mas caro.
    """
    import sam_api as _sam

    desc = ""
    nid = aviso.get("noticeId")
    if nid:
        try:
            desc = _sam.obtener_descripcion(nid, aviso) or ""
        except Exception:
            # Si la descripcion no se puede bajar, el documento sale igual con
            # el titulo. Perder el PDF entero por no poder leer la descripcion
            # seria peor que enviarlo corto.
            desc = ""

    # El texto ya sin tramite, por si el BOM no deja especificacion.
    limpio_desc = _sin_tramitacion(_sin_enmiendas(desc or "")[0])
    bom = _bom_desde_aviso(aviso, desc)

    # --- Parsing estructurado con IA ---
    #
    # El parser de texto funciona, pero se rompe con formas que las regex no
    # pueden ver: "2-20 foot Containers" son 2 contenedores, no 20, y "6 INCH
    # PUMP" es la medida de un rotor, no la cantidad. Saber eso no es un
    # problema de expresiones regulares: es de leer el aviso.
    #
    # El resultado se mezcla con el del parser campo a campo en
    # estructurador._validar(), que solo se queda con lo de la IA si es
    # utilizable. Y si la IA no esta (sin clave, sin cuota, API caída), se usa
    # el parser de texto y el PDF sale igual que antes. Nunca lanza.
    est = {}
    try:
        import estructurador
        est = estructurador.estructurar(
            aviso, desc or "",
            respaldo={
                "producto": (bom[0].get("descripcion") if bom else "") or "",
                "especificacion": (bom[0].get("especificacion") if bom else ""),
                "cantidad": (bom[0].get("cantidad") if bom else 0),
                "unidad": (bom[0].get("unidad") if bom else ""),
                "hay_cantidad": "si" if bom and (bom[0].get("cantidad") or 0) else "",
            },
        ) or {}
    except Exception as e:
        log.warning("desde_aviso: el parsing estructurado fallo (%s); "
                    "se usa el parser de texto", e)

    if est and bom:
        # Se reescribe SOLO el primer item. En un aviso de un CLIN es el unico;
        # con varios, el resto conserva lo que dio el parser de texto, que para
        # las lineas siguientes ya viene del aviso.
        _b0 = bom[0]
        _b0["descripcion"] = est.get("producto_limpio") or _b0["descripcion"]
        _esp_ia = est.get("descripcion_distribuidor") or ""
        if _esp_ia:
            _b0["especificacion"] = _esp_ia
        if est.get("cantidad"):
            _b0["cantidad"] = est["cantidad"]
        if est.get("unidad"):
            _b0["unidad"] = est["unidad"]

    estimacion = _estimar_precio(desc, bom)

    # --- CAPA B: desglose del lote consolidado, SOLO si lo hay ---
    #
    # La condicion va ahi, y no mas arriba, por una razon: si consolidated_lot
    # es False no se lee ni una variable de la capa, no se comprueba la cuota y
    # no se toca la cache. Es lo que mantiene intacta la ruta feliz.
    #
    # Y va envuelta en try/except porque el PDF de una licitacion que NO es un
    # lote se tiene que poder generar SIEMPRE. Esta capa no puede ser la
    # razon de que un documento no salga.
    # El pipeline del encargo, en orden y SINCRONO. No hay segundo plano, no
    # hay hilo, no hay futuro que esperar: se baja, se extrae, se decide, y lo
    # que se decide viaja en el analisis. El renderizado va despues.
    semantica, correccion, lote = {}, {}, {}
    corpus = ""
    try:
        if any(it.get("consolidated_lot") for it in (bom or [])):
            import integral as _int
            import capa_b as _cb
            import adjuntos as _adj

            # PASO 1, BLOQUEANTE. Se espera a que termine. Si esto corriera en
            # segundo plano, el PDF se maquetaria contra un corpus vacio y
            # saldria con QTY 1 sin que nadie se entere: el fallo seria
            # silencioso y dependeria del orden en que acaben las hilos.
            #
            # Y levanta FalloDescargaEspecificaciones si el aviso DECLARABA
            # adjuntos y no se pudo leer ninguno. Ahi si se aborta, y con
            # razon: es un aviso que TIENE especificaciones, no las hemos
            # podido mirar, y vamos a mandar un documento sin mirarlas.
            try:
                _ruta, corpus = _adj.especificaciones(
                    aviso, str(aviso.get("noticeId") or ""))
            except _adj.FalloDescargaEspecificaciones:
                raise
            except Exception as e:
                log.warning("desde_aviso: la descarga fallo (%s); se sigue con "
                            "la descripcion", e)

            # PASO 2, la IA. Solo lee: texto entra, JSON sale.
            # El numero de contrato viaja hasta la llamada para que los logs
            # digan QUE licitacion esta cayendo, y no solo que algo fallo. Sin
            # esto, un 503 en el barrido es indistinguible de otro.
            semantica = _cb.extraer(
                corpus, str(aviso.get("title") or ""),
                str(aviso.get("solicitationNumber") or "")) or {}

            # RESPALDO. Sin corpus no hay estructura que leer en los adjuntos,
            # y la Capa B se sale sin llamar a Gemini. Ahi se usa la lectura
            # antigua, que trabaja sobre la DESCRIPCION sola, y su resultado
            # se traduce al esquema nuevo.
            #
            # No es una ruta muerta: es la que cubre a las licitaciones
            # consolidadas que vienen sin anexos, que son muchas.
            if not (semantica.get("override_required") or
                    semantica.get("part_number") or
                    semantica.get("product_specification_summary")):
                try:
                    import lote_consolidado as _lc_ant
                    _viejo = _lc_ant.resolver_lote(
                        aviso, desc, corpus=corpus) or {}
                    if _viejo:
                        # Asignacion directa, NO setdefault. _vacio() ya
                        # deja estos cuatro a "" o a 0, asi que setdefault no
                        # llega a disparar jamas y el respaldo se quedaba
                        # entero sin hacer nada: target_qty seguia en 0 y el
                        # bloque tecnico no salia nunca.
                        semantica["product_specification_summary"] = (
                            _viejo.get("resolved_product_name") or "")
                        semantica["part_number"] = (
                            _viejo.get("manufacturer_part_number") or "")
                        semantica["manufacturer_brand"] = (
                            _viejo.get("manufacturer_brand_name") or "")
                        semantica["dimensions_or_specs"] = (
                            _viejo.get("dimensions_or_specs") or "")
                        semantica["certification_requirements"] = (
                            _viejo.get("certification_requirements") or "")
                        semantica["target_qty"] = _int_unidades(
                            _viejo.get("actual_units_required"))
                        semantica["_capa"] = "B-ant"
                        log.info("desde_aviso: la Capa B nueva no devolvio "
                                 "nada; se usa la lectura sobre la descripcion")
                except Exception as e:
                    log.info("desde_aviso: el respaldo tampoco pudo (%s)", e)

            # La verificacion va AQUI y no en hidratar(), porque aqui esta el
            # corpus. Si la cifra no esta escrita en el corpus, no se aplica,
            # por muy segura que parezca la respuesta del modelo.
            _cuerpo = (desc or "") + "\n" + (corpus or "")
            try:
                _n = int(semantica.get("target_qty") or 0)
            except (TypeError, ValueError):
                _n = 0
            semantica["verificada"] = bool(
                _n > 0 and _int._numero_en_corpus(_n, _cuerpo))
            if _n and not semantica["verificada"]:
                log.warning("desde_aviso: la Capa %s propuso %s, pero esa cifra "
                            "no esta en el corpus; NO se aplica",
                            semantica.get("_capa", "?"), _n)
                semantica["override_required"] = False

            # El detalle para Telegram, en el formato que ya pide
            # formatear_bloque(). Se traduce del esquema de la Capa B para no
            # tener dos estructuras distintas para lo mismo.
            if semantica.get("override_required") or semantica.get("part_number"):
                uoi = semantica.get("target_uoi") or "EA"
                lote = {
                    "resolved_product_name": semantica.get(
                        "product_specification_summary", ""),
                    "actual_units_required": ("%s %s" % (_n, uoi)).strip(),
                    "manufacturer_brand_name": semantica.get(
                        "manufacturer_brand", ""),
                    "manufacturer_part_number": semantica.get(
                        "part_number", ""),
                    "dimensions_or_specs": semantica.get(
                        "dimensions_or_specs", ""),
                    "certification_requirements": semantica.get(
                        "certification_requirements", ""),
                }
                correccion = {
                    "cantidad": _n, "unidad": uoi,
                    "unidad_original": str((bom or [{}])[0].get("unidad")
                                           or "LOT").upper(),
                    "motivo": "volumen real en los documentos del aviso",
                    "capa": semantica.get("_capa", ""),
                }
            log.info("desde_aviso: lote consolidado en %s -> capa %s, %d "
                     "caracteres de adjuntos, qty %s",
                     aviso.get("noticeId"), semantica.get("_capa", "-"),
                     len(corpus), _n or "sin numero")
    except Exception as e:
        log.warning("desde_aviso: la Capa B fallo (%s); el PDF sigue igual", e)

    # La cantidad y la unidad salen del BOM, que ya las ha buscado en el texto
    # con los patrones correctos.
    _bom0 = (bom or [{}])[0]
    cantidad = _num(_bom0.get("cantidad")) or 1
    unidad = (_bom0.get("unidad") or "EA")

    _ret = {
        "notice_id": aviso.get("noticeId", ""),
        "solicitation": (aviso.get("solicitationNumber")
                         or aviso.get("noticeId") or ""),
        "title": (aviso.get("title") or "").strip(),
        "agencia": (aviso.get("fullParentPathName") or "N/A"),
        "set_aside": (aviso.get("typeOfSetAsideDescription")
                      or aviso.get("typeOfSetAside") or "sin set-aside"),
        "limite": (aviso.get("responseDeadLine") or ""),
        "posted": (aviso.get("postedDate") or ""),
        "naics": aviso.get("naicsCode", ""),
        "psc": aviso.get("classificationCode", ""),
        "ui_link": aviso.get("uiLink", ""),
        "lugar_entrega": lugar_de_entrega_aviso(aviso),
        # La direccion fisica completa, sacada del propio aviso. No se inventa
        # ningun campo: lo que no este en SAM.gov no sale.
        "direccion_entrega": _direccion_completa(aviso),
        # El texto CRUDO del aviso, en ingles, tal cual lo publico el
        # gobierno. Esto es lo que va a la pagina 1 sin pasar por el traductor.
        #
        # La razon es que Gemini devuelve los campos en castellano, y el
        # traductor _ingles() los convierte palabra por palabra. Un traductor
        # sobre texto de compra produce frases rare: "entrega en", "descripcion
        # de", palabras que existen en ingles pero que no suenan a ingles de
        # catalogo. El aviso original ya esta escrito por un oficial en ingles
        # técnico de adquisiciones, y ese texto es mejor que cualquier
        # traduccion.
        "descripcion_original": str(_bom0.get("descripcion") or "").strip(),
        "titulo_original": str(aviso.get("title") or "").strip(),
        # El producto y la especificacion van LIMPIOS, no crudos.
        #
        # Antes ponian el titulo entero del aviso y los primeros 800
        # caracteres de la descripcion, y en una licitacion enmendada la
        # narrativa de "AMD 0001" va justo al principio. Medido: la tabla
        # ITEMS ORDERED de la orden de compra salia con
        #
        #   53--PARTS KIT,SEAL REPLACEMENT,MECHANICAL (5330-01-586-4931)
        #   AMD 0001 Narrative... This amendment is being issued to...
        #
        # o sea, el papel administrativo en la celda de la linea de cobro, y
        # el material fuera. La limpieza estaba hecha, pero solo se aplicaba al
        # BOM: la orden de compra seguia por su cuenta con lo crudo.
        #
        # Se usan los valores ya limpios del BOM, que es donde vive el trabajo.
        # Asi las dos mitades del documento no pueden divergir.
        "producto": (str(_bom0.get("descripcion") or "").strip()
                     or (est.get("producto_limpio") if est else "")
                     or (aviso.get("title") or "").strip()),
        # El numero de parte real, no el NSN. El NSN es un codigo de catalogo
        # y va en su propia columna del BOM; aqui lo que se quiere es lo que
        # identifica al material, que es el P/N. Y como la cabecera limpia ya
        # lo lleva, desde_analisis no lo anade otra vez entre parentesis.
        "modelo_especifico": (str(_bom0.get("modelo") or "").strip()
                              or str(_bom0.get("parte") or "")),
        "unidad_medida": unidad,
        "cantidad_total": cantidad,
        # SIN RESPALDO CRUDO. Antes, cuando la limpieza dejaba la
        # especificacion vacia, caia a desc[:800], que es el texto del aviso
        # INCLUIDA la narrativa de las enmiendas. Ese respaldo era
        # precisamente lo que metia "AMD 0001 Narrative..." en la celda del
        # material cuando la descripcion llegaba en un solo bloque.
        #
        # Si no hay especificacion limpia, no hay especificacion. Se deja el
        # hueco y lo rellena el distribuidor. Meter el texto crudo es peor que
        # un hueco: un hueco se nota, el papel administrativo se lee como si
        # fuera la ficha del rodamiento.
        "especificacion_tecnica_clave": (
            str(_bom0.get("especificacion") or "").strip()
            or (est.get("descripcion_distribuidor") if est else "")
            or _sin_tramitacion(limpio_desc or "")),
        "sin_descripcion": not bool(desc.strip()),
        # El BOM va ya montado: desde_analisis lo respeta tal cual.
        "bom_lineas": bom,
        # Cuantas enmiendas traia el aviso. La narrativa se aparta del
        # documento, pero el numero se declara para que el usuario sepa que
        # hay informacion que tiene que ir a mirar a SAM.gov.
        "enmiendas": max([int(b.get("enmiendas") or 0) for b in (bom or [])]
                         or [0]),
        # El JSON estructurado viaja en el analisis para que desde_analisis
        # pueda llevarlo hasta el documento. Ahi generar_po() lo usa tal cual.
        "estructurado": est or {},
        # El desglose del lote consolidado, o {}. Lo lleva SOLO el mensaje de
        # Telegram; el PDF no lo ve, por diseno.
        "lote_consolidado": lote or {},
        # Si el QTY de la tabla se va a corregir con el volumen real, y por
        # que. Esto es la INTENCION, no el efecto.
        #
        # El BOM NO se toca aqui a proposito: la aplicacion ocurre en
        # generar_po(), con integral.hidratar(), que es el punto unico. Si se
        # corrigiera aqui, un analisis que vuelve de la base no tendria por
        # donde corregirse, que es exactamente el fallo que esto arregla.
        "qty_corregido": correccion or {},
        # El contexto semantico entero. Lo lee generar_po() al hidratar.
        "capa_b": dict(semantica or {}),
        # El precio lo hereda Kyomoto del valor que declara el propio aviso.
        #
        # Antes iban a None, y la tabla ITEMS ORDERED multiplicaba None por la
        # cantidad: $0.00 en UNIT PRICE, en AMOUNT y en el TOTAL. Un $0.00 en un
        # documento que va a un distribuidor no es un dato pendiente, es un
        # dato FALSO: el lector ve que el gobierno compra eso por nada.
        #
        # filters.valor_declarado() es la MISMA funcion que el barrido usa para
        # descartar por el tope antes de gastar una llamada de Gemini ("Not to
        # Exceed USD 85,000", "NTE 350,000"), con sus propias protecciones. Si
        # el aviso declara su valor, valor / cantidad es una estimacion
        # defendible y sale del aviso, no de una invencion.
        #
        # Si el aviso NO declara valor, no se inventa: precio None y el
        # documento lo dice. Un hueco se rellena; un cero no se discute.
    }
    # El estimado NO va al documento: se queda en el analisis para que el
    # mensaje de Telegram lo enseñe, que es donde si sirve. El usuario necesita
    # saber a que atencion comercial va la cosa antes de hablar con tres
    # proveedores. En el papel del distribuidor, no.
    _ret["estimacion_interna"] = estimacion
    return _ret


def _instrucciones_ingles(analisis: dict) -> str:
    """
    Delivery destination and packaging rules, in English.

    Es la version en ingles de las instrucciones de despacho que ya iban en
    castellano. Se anade en vez de sustituir porque el resto del documento, la
    orden de compra, sigue en castellano, y las dos mitades viven juntas: una
    para el proveedor de EE.UU. y otra para el expediente.

    El empaque va en ingles a proposito, y es donde mas se equivoca la gente:
    un proveedor que entrega sin el embalaje que pide la especificacion se lo
    rechazan, y "embalaje apta para transporte" en un documento en castellano
    puede leerse como una recomendacion en vez de una condicion.
    """
    lugar = str(analisis.get("lugar_entrega") or "").strip()
    if not lugar:
        lugar = ("Destination as stated on the SAM.gov notice "
                 "(place of performance)")
    lineas = [
        f"Delivery destination: {lugar}.",
        "Packaging: pack the material suitable for commercial transportation "
        "and deliver it new and unused. Original manufacturer packaging "
        "preferred.",
        "Each shipment must include a packing list and a certificate of "
        "conformance identifying every part number and lot.",
        "Partial shipments are not accepted unless authorized in writing "
        "before the due date.",
        "Marking: every shipping label must show the solicitation number and "
        "the CLIN number of the item it contains.",
    ]
    if analisis.get("limite"):
        f = _fecha_pdf(analisis.get("limite"))
        if f:
            lineas.insert(0, f"Material must be delivered on or before {f}.")
    sa = str(analisis.get("set_aside") or "").strip()
    if sa and "sin set-aside" not in sa.lower():
        lineas.append(
            f"Set-aside: {sa}. If this is a small business set-aside, state "
            f"your UEI and NAICS code on the quote."
        )
    return "\n".join("- " + x for x in lineas)


def _notas_ingles(analisis: dict, pendientes: int = 0) -> str:
    """
    El pie del documento, en ingles, mas el aviso si algo quedo sin traducir.

    La primera parte es la que estaba antes, en castellano. Se traduce porque
    el objetivo es que no quede frase en castellano en el PDF, y porque este
    texto LLE al proveedor: la aclaracion de que el documento no es oficial
    tiene que entenderla la contraparte, no solo el usuario.

    La segunda parte es nueva y sale SOLO si de verdad hay algo pendiente. Un
    documento que va a un distribuidor no puede llevar texto en otro idioma sin
    avisarlo, y el aviso va en el PDF y en el mensaje de Telegram, para que el
    usuario lo sepa antes de mandar el fichero y no cuando el proveedor ya le
    ha preguntado que significa una linea.
    """
    texto = ("Document issued by L.A.M.B. Logistics LLC. This is not an "
             "official document of the U.S. Government or any agency.")

    # Por que el precio va en blanco.
    #
    # Se evaluo imprimir aqui el estimado propio (valor declarado del aviso
    # partido por la cantidad) y se descarto: el proveedor lo leeria como un
    # precio cotizado y cotizaria sobre NUESTRO numero en vez de sobre el
    # material. Le dirian "su precio", y el contrato entero se construiria
    # sobre una cifra que nunca fue suya.
    #
    # Y en ningun caso $0.00. Un cero es un precio FALSO, no un hueco, y un
    # hueco se rellena.
    texto += (
        "\n\nPRICE: the Unit Price and Amount columns are left blank on "
        "purpose. This is a request for quotation: the supplier enters its own "
        "price. No figure is printed there, because a printed number would be "
        "read as a quoted price and the supplier would quote around it."
    )

    _amd = int(_num(analisis.get("enmiendas")) or 0)
    if _amd:
        texto += (
            "\n\nAMENDMENTS: this solicitation carries %d amendment(s). Their "
            "administrative narrative (AMD numbering, attachments, point of "
            "contact) is not reproduced here because it is not a technical "
            "specification. Review the amendments on SAM.gov before issuing "
            "this document." % _amd
        )

    if pendientes:
        texto += (
            "\n\nNOTE: %d field(s) marked [%s] contain source text that was "
            "not machine-translated. Verify the wording before issuing this "
            "document to the supplier."
            % (pendientes, MARCA_PENDIENTE.strip("[]"))
        )
    return texto


def desde_analisis(analisis: dict, proveedor: dict | None = None,
                     db_path: str | None = None) -> dict:
    """
    Traduce el analisis de Kyomoto al diccionario que espera generar_po().

    proveedor: {"nombre", "contacto", "email", "direccion"} del distribuidor.
    Si no se pasa, los campos quedan vacios para rellenarlos a mano, que es lo
    normal: el usuario elige el distribuidor DESPUES de ver el analisis.
    """
    prov = proveedor or {}
    # El JSON estructurado llega en el propio analisis (lo pone desde_aviso
    # cuando vino de una consulta en vivo). En un analisis ya guardado por el
    # barrido no esta, y da igual: esos datos ya salieron de Gemini.
    _est_analisis = dict(analisis.get("estructurado") or {})
    # El contexto semantico lo lee generar_po() a la hora de hidratar. Aqui
    # solo se saca del analisis y se pasa; aplicarlo aqui seria el problema
    # que se acaba de corregir.
    try:
        import integral as _integral_ctx
        _contexto = _integral_ctx.contexto_de(analisis)
    except Exception:
        _contexto = {}
    lineas = []
    cant = _num(analisis.get("cantidad_total")) or 1
    unidad = analisis.get("unidad_medida") or "EA"
    # SIN PRECIO, Y SIN ESTIMADO INTERNO.
    #
    # Este documento es una hoja de cotizacion: la rellena el DISTRIBUIDOR.
    # UNIT PRICE y AMOUNT salen EN BLANCO a proposito.
    #
    # Se evaluo poner aqui el estimado propio (valor declarado del aviso /
    # cantidad) y se descarto. El proveedor lo leeria como un precio cotizado y
    # cotizaria sobre NUESTRO numero en vez de sobre el material: le dirian
    # "su precio", y el contrato entero se construiria sobre una cifra que nunca
    # fue suya. El estimado es util para decidir si el negocio tiene sentido,
    # que es una pregunta interna, y para eso se queda en el mensaje de
    # Telegram.
    #
    # Y en ningun caso $0.00: un cero es un precio FALSO, no un hueco.
    precio = 0.0
    mostrar_precios = False

    # Descripcion y especificacion vienen de Gemini, en castellano, y no se
    # pueden reescribir porque cambian en cada analisis. Se traducen con la
    # capa de arriba y lo que no se traduzca se marca, para que el usuario no
    # mande al distribuidor creyendo que esta todo en ingles.
    #
    # El orden importa: primero se compone el modelo con la descripcion y
    # despues se traduce el bloque entero. Al reves, el modelo solo, que
    # casi siempre es un codigo, se traduciria sin necesidad.
    desc = (analisis.get("producto") or "").strip()
    modelo = (analisis.get("modelo_especifico") or "").strip()
    if modelo and modelo.lower() not in desc.lower():
        desc = f"{desc} ({modelo})".strip()
    spec = (analisis.get("especificacion_tecnica_clave") or "").strip()
    desc, _p1 = _ingles(desc, _ACORTAR["descripcion"])
    spec, _p2 = _ingles(spec, _ACORTAR["especificacion"])
    # El lugar de entrega tambien lo escribo yo en las instrucciones, pero
    # aparece suelto en algunos analisis.
    lugar, _p3 = _ingles(analisis.get("lugar_entrega") or "")

    # El texto que este modulo genera va en ingles de origen, no traducido.
    # No es un detalle de estilo: estas lineas y las condiciones de pago de
    # abajo van dirigidas al DISTRIBUIDOR, y el documento se manda a uno de
    # Albuquerque. Un proveedor que lee "Pago a 30 dias contra factura" en un
    # papel redactado en ingles no sabe aplicar las condiciones de pago, y esa
    # es exactamente la linea que no puede quedar sin entender.
    inst = [
        f"Delivery to: {analisis.get('lugar_entrega') or 'the installation designated by the Government'}",
        "Packaging suitable for transportation: the supplier shall pack the "
        "material so that it is preserved in the condition required for "
        "delivery.",
        "Documents to be included with the shipment: packing list and "
        "certificate of conformance (CoC).",
    ]
    if analisis.get("limite"):
        inst.append("The material must be delivered on or before "
                    f"{_fecha_pdf(analisis.get('limite'))}.")
    if proveedor:
        inst.append(" Remit the invoice to the address shown on the "
                    "letterhead, quoting the Purchase Order number.")

    # --- Seccion de cotizacion en ingles ---
    #
    # Se calculan aqui y no dentro de generar_po() porque aqui se sabe de
    # donde vienen los datos: si el aviso se acaba de pedir directo a SAM.gov,
    # el item sale de su tabla de CLIN; si viene de un analisis ya guardado por
    # Kyomoto, sale de los campos estructurados que devolvio Gemini.
    #
    # "bom_lineas" es la via para que quien llama pase un BOM ya construido.
    _bom = list(analisis.get("bom_lineas") or []) or _bom_desde_analisis(analisis)

    # --- Una linea de la orden por CLIN ---
    #
    # Antes habia UNA sola linea, con la cantidad del PRIMER CLIN. Con tres CLIN
    # de 50, 10 y 4 unidades, la orden de compra mostraba 50 EA y un AMOUNT
    # calculado sobre 50, mientras el BOM de al lado listaba los tres: las
    # cifras del documento no cuadraban entre si, y el comprador no sabe cual
    # de las dos es la buena.
    #
    # Con un solo CLIN, que es el caso normal, sale exactamente igual que antes.
    # El precio unitario es el mismo para todos: es el promedio sobre el valor
    # declarado del aviso entero, y por eso se aplica a cada linea en vez de
    # inventar un reparto que el aviso no dice.
    lineas = []
    # pendientes=0 ANTES de cualquier "pendientes +=". Antes se inicializaba
    # despues de construir las lineas, y como las lineas ya suman los suyos,
    # la primera vez que habia varios CLIN salia NameError y el PDF entero no
    # se generaba. Se inicializa aqui y abajo ya solo se acumula.
    pendientes = 0
    _sku = (analisis.get("solicitation") or "").strip()
    if len(_bom) > 1:
        for _b in _bom:
            _desc_b = str(_b.get("descripcion") or "").strip()
            _desc_b, _pb = _ingles(_desc_b, _ACORTAR["descripcion"])
            _spec_b = str(_b.get("especificacion") or "").strip()
            _spec_b, _ps = _ingles(_spec_b, _ACORTAR["especificacion"])
            lineas.append({
                "sku": _sku,
                "descripcion": _desc_b or desc,
                "especificacion": _spec_b,
                "cantidad": _num(_b.get("cantidad")) or cant,
                "unidad": _b.get("unidad") or unidad,
                "precio_unitario": precio,
                "plazo": "",
            })
            pendientes += int(_pb) + int(_ps)
    else:
        lineas.append({
            "sku": _sku,
            "descripcion": desc or "Physical item per the SAM.gov specification",
            "especificacion": spec,
            "cantidad": cant,
            "unidad": unidad,
            "precio_unitario": precio,
            "plazo": "",
        })

    # Cuantos campos del documento llevan texto sin traducir.
    #
    # Se cuentan DESPUES de traducir, sobre el texto final, que es el que se
    # imprime. Contarlos antes mediria el castellano de partida, que ya no
    # existe, y el numero no significaria nada.
    #
    # El lugar de entrega se mira aparte porque va en las instrucciones, que se
    # generan despues de esta linea y por tanto no estan en _bom.
    pendientes += sum(1 for x in (_p1, _p2, _p3) if x)
    for _b in _bom:
        for _campo in ("descripcion", "especificacion"):
            if MARCA_PENDIENTE in str(_b.get(_campo) or ""):
                pendientes += 1
    if MARCA_PENDIENTE in str(inst):
        pendientes += 1

    return {
        # El numero se genera aqui y no dentro de generar_po() para que el
        # documento y el nombre del fichero salgan del MISMO numero. Si se
        # generara mas adentro, el fichero se llamaria siempre "PO.pdf" y no
        # habria forma de distinguir dos ordenes.
        "numero_po": siguiente_numero_po(db_path),
        # Seccion para el distribuidor: generar_po() la dibuja solo si "bom"
        # trae algo, asi que un analisis sin BOM da el documento de siempre.
        "bom": _bom,
        "bom_entrega": _instrucciones_ingles(analisis),
        # El lugar va como campo propio para el bloque de logistica. Antes solo
        # existia dentro del texto de instrucciones, ya compuesto, y de ahi no
        # se podia sacar una direccion limpia sin desarmar una frase entera.
        "lugar_entrega": _dir_limpio(lugar),
        # La direccion fisica completa, campo por campo. Aqui es donde sale el
        # ZIP, y sale SOLO si el aviso lo trae: inventarlo manda el material al
        # estado equivocado.
        "direccion_entrega": _direccion_entrega_disponible(analisis),
        # La BASE en ingles, ya limpia, para que el blueprint superior se pueda
        # hidratar cuando no hay Capa B. Son los mismos valores que salen en la
        # tabla de la pagina 1, y se calculan aqui una sola vez para que las dos
        # mitades del documento no puedan divergir.
        "identidad_base": _valor_corto(desc, 150),
        "parte_base": modelo,
        "spec_base": _valor_corto(spec, 220),
        # La unidad de LOTE que compro el aviso, distinta de la unidad fisica
        # que resolvio la Capa B. Las dos van en el papel a la vez.
        # La unidad de LOTE. Primero la que dejo la correccion; y si no hay
        # correccion, la que declara el propio aviso. Sin este respaldo, un
        # aviso sin Capa B no puede pintar "1 LOT (...)" y se queda en un
        # "50 SET" suelto, que es justo la duda que ese texto queria cerrar.
        "unidad_lote": str((analisis.get("qty_corregido") or {})
                           .get("unidad_original")
                           or (analisis.get("unidad_medida") or "")
                           or "").strip().upper(),
        "referencia_sam": (analisis.get("solicitation") or "").strip(),
        "titulo_sam": (analisis.get("title") or "").strip(),
        "fecha_limite": (analisis.get("limite") or "").strip(),
        "proveedor": prov.get("nombre", ""),
        "proveedor_contacto": prov.get("contacto", ""),
        "proveedor_email": prov.get("email", ""),
        "proveedor_dir": prov.get("direccion", ""),
        "lineas": lineas,
        # Sin numero no se imprime $0.00: la tabla deja las celdas en blanco.
        "mostrar_precios": mostrar_precios,
        # El JSON estructurado, tal cual. generar_po() lo consume para la tabla
        # del BOM: producto_limpio en la linea, cantidad y unidad en sus
        # columnas, descripcion_distribuidor en el cuerpo. Si no esta (sin IA,
        # sin cuota), el BOM ya trae lo mismo y el documento sale igual.
        "estructurado": _est_analisis or {},
        # El detalle del lote consolidado, para el bloque tecnico del PDF. Va
        # solo si hay: sin detalle, generar_po() no dibuja nada.
        "lote_detalle": dict(analisis.get("lote_consolidado") or {}),
        # El contexto semantico viaja HASTA el documento, no se aplica aqui.
        # Lo aplica generar_po(), que es donde vive el punto unico. Asi una
        # sobreescritura no puede perderse por el camino.
        "contexto_semantico": _contexto,
        "instrucciones": "\n".join(inst),
        # Condiciones en ingles, por lo mismo que las instrucciones: van al
        # proveedor. El factoring se declara porque es lo que hace posible la
        # operacion, y porque el proveedor necesita saber a donde remitir la
        # factura para que el pago le llegue.
        "condiciones": (
            "Payment 30 days net from invoice. L.A.M.B. Logistics LLC factors "
            "receivables within the United States; remit the invoice to the "
            "address shown on the letterhead for factoring.\n"
            f"The material shall strictly conform to the SAM.gov "
            f"specification referenced "
            f"{analisis.get('solicitation') or 'N/A'}."
        ),
        "notas": _notas_ingles(analisis, pendientes),
    }
