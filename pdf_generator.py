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
)

import config

# --- Datos de la empresa -------------------------------------------------
EMPRESA = "L.A.M.B. Logistics LLC"
DIRECCION = ["1209 Mountain Rd. PL. NE, Ste. H", "Albuquerque, NM 87110"]
FIRMANTE = "Bastian Cordero"
CARGO_FIRMANTE = "CEO"
EMAIL = "operations@lamblogistics.com"
TELEFONO = "+1 (505) 555-0142"

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
    """
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
                               f"{EMPRESA} · {TELEFONO} · {EMAIL} · pagina {d.page}")
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="po", frames=[marco],
                                       onPage=_pie)])

    f = []

    # --- Encabezado izquierdo: la empresa ---
    f.append(Paragraph(EMPRESA, e["empresa"]))
    for linea in DIRECCION:
        f.append(Paragraph(linea, e["dir"]))
    f.append(Paragraph(f"{TELEFONO} &nbsp;|&nbsp; {EMAIL}", e["dir"]))
    f.append(Spacer(1, 0.22 * inch))

    # --- Titulo ---
    f.append(Paragraph("PURCHASE ORDER", e["titulo"]))
    f.append(Paragraph("Orden de compra emitida por L.A.M.B. Logistics LLC",
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
                   colWidths=[3.15 * inch, 3.15 * inch],
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
    for i, ln in enumerate(datos.get("lineas") or [], start=1):
        cant = _num(ln.get("cantidad"))
        precio = _num(ln.get("precio_unitario"))
        importe = cant * precio
        total += importe
        desc = ln.get("descripcion") or ""
        spec = ln.get("especificacion") or ""
        if spec:
            desc = f"{desc}<br/><font size=7.5 color='#5B6770'>{spec}</font>"
        # El numero de linea, no el de solicitud: este no cabe en la columna
        # (0.62") y ademas ya sale arriba, en SAM.GOV REFERENCE. Lo que el
        # distribuidor necesita para citar una linea es "1", "2", "3".
        filas.append([
            Paragraph(str(i), e["celda_b"]),
            Paragraph(desc, e["celda"]),
            Paragraph(_fmt(cant), e["celda"]),
            Paragraph(ln.get("unidad") or "EA", e["celda"]),
            Paragraph(_usd(precio), e["celda"]),
            Paragraph(_usd(importe), e["celda_b"]),
        ])
    filas.append(["", Paragraph("TOTAL (USD)", e["celda_b"]), "", "",
                  "", Paragraph(_usd(total), e["celda_b"])])

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
        _f_bom = [[Paragraph(h, e["celda_b"]) for h in cab]]
        for b in _bom:
            _d = b.get("descripcion") or ""
            _s = b.get("especificacion") or ""
            if _s:
                _d = f"{_d}<br/><font size=7.5 color='#5B6770'>{_s}</font>"
            _f_bom.append([
                Paragraph(str(b.get("clin") or ""), e["celda_b"]),
                Paragraph(b.get("parte") or "__________", e["celda"]),
                Paragraph(_d, e["celda"]),
                Paragraph(_fmt(_num(b.get("cantidad"))), e["celda"]),
                Paragraph(b.get("unidad") or "EA", e["celda"]),
                Paragraph(b.get("precio") or "", e["celda_b"]),
            ])
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
            "Identifier. <b>Cage</b> = Commercial and Government Entity code.",
            e["nota"]))
        f.append(Spacer(1, 0.12 * inch))

        _entrega = datos.get("bom_entrega") or ""
        if _entrega:
            f.append(KeepTogether([
                Paragraph("DELIVERY DESTINATION &amp; PACKAGING", e["h2"]),
                _caja(e, _entrega),
                Spacer(1, 0.14 * inch),
            ]))

    if datos.get("instrucciones"):
        f.append(KeepTogether([
            Paragraph("SHIPPING INSTRUCTIONS", e["h2"]),
            _caja(e, datos["instrucciones"]),
            Spacer(1, 0.14 * inch),
        ]))
    if datos.get("condiciones"):
        f.append(KeepTogether([
            Paragraph("TERMS AND CONDITIONS", e["h2"]),
            _caja(e, datos["condiciones"]),
            Spacer(1, 0.14 * inch),
        ]))
    if datos.get("notas"):
        f.append(Paragraph(datos["notas"], e["nota"]))
        f.append(Spacer(1, 0.10 * inch))

    doc.build(f)
    return buffer.getvalue()


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
_UOIS = {"EA", "KT", "KTN", "RO", "LOT", "SET", "PR", "CS", "CA", "BX",
         "PL", "PK", "DZ", "FT", "LB", "GA", "ML", "L", "M", "KG", "GAL"}
_RE_CANT = re.compile(r"\b(\d{1,7})\s*(EA|KTN|KT|RO|LOT|SET|PR|CS|CA|BX|PL|"
                      r"PK|DZ|FT|LB|GA|ML|L|M|KG|GAL)\b")


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

    cant = 0.0
    unidad = "EA"
    m = _RE_CANT.search(texto)
    if m:
        cant = float(m.group(1))
        if m.group(2).upper() in _UOIS:
            unidad = m.group(2).upper()
    else:
        m = _RE_UOI.search(texto)
        if m and m.group(1).upper() in _UOIS:
            unidad = m.group(1).upper()

    pn = _RE_PARTNO.search(texto)
    if pn and not parte:
        parte = pn.group(1)

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
        cant, uni = 0.0, ""
        mc = _RE_CANT.search(linea)
        if mc:
            cant = float(mc.group(1))
            if mc.group(2).upper() in _UOIS:
                uni = mc.group(2).upper()
        if not uni:
            mu = _RE_UOI.search(linea)
            if mu and mu.group(1).upper() in _UOIS:
                uni = mu.group(1).upper()
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

    if not clines:
        # Sin CLIN: una sola linea con el producto. Es mejor un item que
        # describa lo que hay que cotizar que una tabla sin contenido.
        desc = titulo or "Item as described in the attached specification"
        spec = (texto[:600].strip() if texto else
                "No further description available from SAM.gov.")
        return [{
            "clin": "1",
            "parte": parte or "",
            "descripcion": desc,
            "especificacion": spec,
            "cantidad": cant or 1,
            "unidad": unidad,
            "precio": "",
        }]

    # Con CLIN: una linea por cada uno, con SUS datos.
    salida = []
    for i, c in enumerate(clines, start=1):
        _d = _datos_de(_linea_de(c))
        # Si en su linea no habia nada, se cae al valor global del texto, y si
        # tampoco, a 1 EA. Se marca con vacio el dato que no se sabe, para que
        # el comprador lo rellene en vez de fiarse de una cifra inventada.
        _cant = _d["cantidad"] or cant or 1
        _uni = _d["unidad"] or unidad or "EA"
        _parte = _d["parte"] or (parte if i == 1 else "")
        desc = titulo if i == 1 else f"{titulo} (item {c})"
        salida.append({
            "clin": c,
            "parte": _parte,
            "descripcion": desc,
            "especificacion": (texto[:400].strip() if i == 1 and texto else ""),
            "cantidad": _cant,
            "unidad": _uni,
            "precio": "",
        })
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
    desc = (analisis.get("producto") or "").strip()
    modelo = (analisis.get("modelo_especifico") or "").strip()
    if modelo and modelo.lower() not in desc.lower():
        desc = f"{desc} ({modelo})".strip()
    spec = (analisis.get("especificacion_tecnica_clave") or "").strip()
    return [{
        "clin": "1",
        "parte": modelo,
        "descripcion": desc or "Item as described in the attached specification",
        "especificacion": spec,
        "cantidad": cant,
        "unidad": unidad,
        "precio": "",
    }]


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

    bom = _bom_desde_aviso(aviso, desc)

    # La cantidad y la unidad salen del BOM, que ya las ha buscado en el texto
    # con los patrones correctos.
    _bom0 = (bom or [{}])[0]
    cantidad = _num(_bom0.get("cantidad")) or 1
    unidad = (_bom0.get("unidad") or "EA")

    return {
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
        "producto": (aviso.get("title") or "").strip(),
        "modelo_especifico": (_bom0.get("parte") or ""),
        "unidad_medida": unidad,
        "cantidad_total": cantidad,
        "especificacion_tecnica_clave": (desc[:800].strip() if desc else ""),
        "sin_descripcion": not bool(desc.strip()),
        # El BOM va ya montado: desde_analisis lo respeta tal cual.
        "bom_lineas": bom,
        # Sin precios: este documento es una hoja de cotizacion, y el precio
        # lo pone el distribuidor. Poner un 0 seria truthiness falsa.
        "precio_unitario_costo": None,
        "costo_total_usd": None,
    }


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


def desde_analisis(analisis: dict, proveedor: dict | None = None,
                     db_path: str | None = None) -> dict:
    """
    Traduce el analisis de Kyomoto al diccionario que espera generar_po().

    proveedor: {"nombre", "contacto", "email", "direccion"} del distribuidor.
    Si no se pasa, los campos quedan vacios para rellenarlos a mano, que es lo
    normal: el usuario elige el distribuidor DESPUES de ver el analisis.
    """
    prov = proveedor or {}
    lineas = []
    cant = _num(analisis.get("cantidad_total")) or 1
    unidad = analisis.get("unidad_medida") or "EA"
    precio = _num(analisis.get("precio_unitario_costo"))
    if not precio:
        # Sin precio unitario se usa el total partido por la cantidad, para
        # que el importe de la linea no salga en cero.
        precio = _num(analisis.get("costo_total_usd")) / cant if cant else 0.0

    desc = (analisis.get("producto") or "").strip()
    modelo = (analisis.get("modelo_especifico") or "").strip()
    if modelo and modelo.lower() not in desc.lower():
        desc = f"{desc} ({modelo})".strip()
    spec = (analisis.get("especificacion_tecnica_clave") or "").strip()

    lineas.append({
        "sku": (analisis.get("solicitation") or "").strip(),
        "descripcion": desc or "Producto fisico segun especificacion de SAM.gov",
        "especificacion": spec,
        "cantidad": cant,
        "unidad": unidad,
        "precio_unitario": precio,
        "plazo": "",
    })

    inst = [
        f"Entrega en: {analisis.get('lugar_entrega') or 'instalacion indicada por el gobierno'}",
        "Embalaje apta para transporte: el proveedor debe embalar para "
        "conservar el producto en condiciones de entrega.",
        "Documentos a incluir con el envio: packing list y certificado de "
        "conformidad.",
    ]
    if analisis.get("limite"):
        inst.append("El material debe entregarse antes del "
                    f"{_fecha_pdf(analisis.get('limite'))}.")
    if proveedor:
        inst.append(" remitir la factura a la direccion de la cabecera, "
                    "indicando el numero de Purchase Order.")

    # --- Seccion de cotizacion en ingles ---
    #
    # Se calculan aqui y no dentro de generar_po() porque aqui se sabe de
    # donde vienen los datos: si el aviso se acaba de pedir directo a SAM.gov,
    # el item sale de su tabla de CLIN; si viene de un analisis ya guardado por
    # Kyomoto, sale de los campos estructurados que devolvio Gemini.
    #
    # "bom_lineas" es la via para que quien llama pase un BOM ya construido.
    _bom = list(analisis.get("bom_lineas") or []) or _bom_desde_analisis(analisis)

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
        "referencia_sam": (analisis.get("solicitation") or "").strip(),
        "titulo_sam": (analisis.get("title") or "").strip(),
        "fecha_limite": (analisis.get("limite") or "").strip(),
        "proveedor": prov.get("nombre", ""),
        "proveedor_contacto": prov.get("contacto", ""),
        "proveedor_email": prov.get("email", ""),
        "proveedor_dir": prov.get("direccion", ""),
        "lineas": lineas,
        "instrucciones": "\n".join(inst),
        "condiciones": (
            "Pago a 30 dias contra factura. L.A.M.B. Logistics LLC usa "
            "factoring dentro de EE.UU.; indiquese el despacho a la direccion "
            "de la cabecera para la factorizacion.\n"
            f"El material debe corresponder exactamente a la especificacion "
            f"de SAM.gov referencia "
            f"{analisis.get('solicitation') or 'N/A'}."
        ),
        "notas": (
            "Documento emitido por L.A.M.B. Logistics LLC. No es un documento "
            "oficial del gobierno de EE.UU. ni de ninguna agencia."
        ),
    }
