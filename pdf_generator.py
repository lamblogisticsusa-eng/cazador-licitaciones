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

    return {
        # El numero se genera aqui y no dentro de generar_po() para que el
        # documento y el nombre del fichero salgan del MISMO numero. Si se
        # generara mas adentro, el fichero se llamaria siempre "PO.pdf" y no
        # habria forma de distinguir dos ordenes.
        "numero_po": siguiente_numero_po(db_path),
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
