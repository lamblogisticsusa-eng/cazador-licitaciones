"""
label.py - Genera la etiqueta de envio (freight / pallet) en PDF.

Sirve para el comando /etiqueta: toma la oportunidad que Kyomoto ya analizo y
produce un documento INDEPENDIENTE de la orden de compra, listo para imprimir
y para pegar sobre la caja o el pallet. Usa reportlab, que ya estaba en tu
requirements.txt.

LA ETIQUETA ES UN DOCUMENTO DE ENVIO, NO UNA FICHA INTERNA

Va pegada a la caja y la leen el transportista y el personal de la base
destino, en Estados Unidos. Por eso:

- Va en INGLES, igual que la orden de compra (Zero-Spanish). El texto de la
  IA llega en castellano y pasa por la misma capa de traduccion que la
  orden; lo que no se puede traducir ENTERO no se imprime a medias.
- El Shipper of Record es L.A.M.B. Logistics LLC con sus datos reales, la
  UEI y el NCAGE. El pliego exige que las etiquetas muestren al remitente
  (Blind Drop-Shipping Directive), y "Kyomoto Logistics, Santiago, Chile"
  no era el remitente de nadie.
- El Consignee sale de la direccion FISICA del aviso de SAM.gov
  (direccion_entrega), campo por campo: instalacion, calle, ciudad, estado
  y ZIP. Si lo unico que se sabe es el pais ("USA"), NO se pinta: un
  Ship-To generico no es una direccion, es decir que no se sabe, y en un
  documento de envio eso manda el material a ninguna parte. Se deja el
  hueco con la instruccion honesta.
- Referencias cruzadas: solicitud, CLIN y bultos/unidades, que es lo que el
  personal de la base usa para dar de alta la entrega. El CLIN sale del BOM
  que ya extrajo el pipeline; los bultos no estan en el aviso, asi que se
  dejan en blanco para rellenarlos al preparar el envio.

NO inventa datos: lo que no esta en el aviso se deja en blanco o se dice que
hay que confirmarlo. Una direccion inventada en una etiqueta es material
perdido.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import config

# 4x6 pulgadas: el tamano ESTANDAR de la etiqueta de flete (UPS/FedEx/ZPL).
# Antes el alto se leia de _ANCHO_ETIQUETA[1], que era 2 pulgadas, y el
# contenido seguia bajando igual: lo que no cabia, salia de la pagina y no se
# imprimia. _ALTO_ETIQUETA ya valia 6 y nadie lo usaba.
_ANCHO_ETIQUETA = (4, 2)
_ALTO_ETIQUETA = 6

# Paleta de la casa, la misma que la orden de compra: navy sobrio y gris.
_NAVY = "#1B3B6F"
_GRIS = "#5B6770"

# El texto de la IA trae markdown (**negrita**, _cursiva_, `codigo`). En un
# papel eso es ruido.
_RE_MD = re.compile(r"[*_`]+")

# Lo que NO vale como destino: solo el pais, o el marcador de "sin dato" que
# deja el analisis. Pintar eso en el Consignee es el error que esta funcion
# existe para evitar.
_GENERICOS = {"", "usa", "u.s.a.", "us", "u.s.", "united states",
              "united states of america", "no especificado", "not specified",
              "n/a", "na", "none", "a confirmar"}


def _seguro(texto: str) -> str:
    """reportlab no maneja unicode fuera del Latin-1 en la fuente base."""
    return (
        str(texto or "")
        .encode("latin-1", "replace")
        .decode("latin-1")
        .replace("\r", " ")
    )


def _numero(ultimos: str) -> str:
    digitos = re.sub(r"\D", "", ultimos or "")
    return digitos[-12:] or "000000000000"


def nombre_archivo(notice_id: str, solicitation: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", solicitation or "opp").strip("-") or "opp"
    return f"etiqueta_{base}_{_numero(notice_id)}.pdf"


def _consignee(analisis: dict, pdfg) -> list[str]:
    """
    Las lineas del Consignee, en orden, o [] si no hay destino real.

    Primero la direccion fisica completa (direccion_entrega), que es la que
    pone desde_aviso() a partir de placeOfPerformance / shipToAddress /
    deliveryLocation. Solo si no existe se cae al lugar grosero del aviso
    (ciudad / estado), que al menos si es un sitio.

    Y en ningun caso el pais solo: "USA" en el Ship-To no es una direccion.
    """
    dir_ = dict(analisis.get("direccion_entrega") or {})
    lineas = [str(x).strip() for x in pdfg.lineas_direccion(dir_)
              if str(x).strip()]
    if not lineas:
        lugar = _RE_MD.sub("", str(analisis.get("lugar_entrega") or "")).strip()
        if lugar:
            # El lugar puede venir con separadores de casa ("Norfolk / VA /
            # USA") o con comas ("Norfolk, Virginia, UNITED STATES").
            trozos = [x.strip() for x in re.split(r"\s*/\s*", lugar) if x.strip()]
            lineas = trozos
    if not lineas or all(t.lower() in _GENERICOS for t in lineas):
        return []
    return lineas[:4]


def generar(notice_id: str, analisis: dict) -> str | None:
    """Crea el PDF y devuelve la ruta, o None si reportlab no esta disponible."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import inch
        from reportlab.pdfgen import canvas
    except ImportError:
        return None

    analisis = analisis or {}
    # Las constantes de la casa se importan de pdf_generator, no se copian:
    # si cambia el telefono o el UEI, cambia en los dos papeles a la vez y no
    # puede divergir. Import diferido para no acoplar la importacion del
    # modulo (pdf_generator trae reportlab e integral).
    import pdf_generator as _pg

    navy = colors.HexColor(_NAVY)
    gris = colors.HexColor(_GRIS)

    os.makedirs(config.ETIQUETAS_DIR, exist_ok=True)
    ruta = os.path.join(
        config.ETIQUETAS_DIR,
        nombre_archivo(notice_id, analisis.get("solicitation", "")))
    ancho = _ANCHO_ETIQUETA[0] * inch
    alto = _ALTO_ETIQUETA * inch

    c = canvas.Canvas(ruta, pagesize=(ancho, alto))
    c.setTitle(f"Shipping label {analisis.get('solicitation', '')}")
    m = 0.18 * inch
    izq = m + 9
    der = ancho - m - 9

    # --- Marco ---
    c.setStrokeColor(navy)
    c.setLineWidth(1.4)
    c.rect(m, m, ancho - 2 * m, alto - 2 * m)

    # --- Banda de cabecera (navy, como la cabecera de tabla de la orden) ---
    c.setFillColor(navy)
    c.rect(m, alto - m - 27, ancho - 2 * m, 27, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(izq, alto - m - 18, "L.A.M.B. LOGISTICS LLC")
    c.setFont("Helvetica-Bold", 7)
    c.drawRightString(der, alto - m - 17, "SHIPPING / PALLET LABEL")
    c.setFont("Helvetica", 5.8)
    c.drawRightString(der, alto - m - 24,
                      f"Generated {datetime.now().strftime('%m/%d/%Y %H:%M')}")
    c.setFillColor(colors.black)

    def _titulo(txt, y):
        """Titulo de seccion en navy con su regla debajo."""
        c.setFillColor(navy)
        c.setFont("Helvetica-Bold", 6.5)
        c.drawString(izq, y, txt)
        c.setFillColor(colors.black)
        c.setStrokeColor(gris)
        c.setLineWidth(0.4)
        c.line(izq, y - 3.5, der, y - 3.5)
        return y - 14

    y = alto - m - 42

    # --- SHIPPER OF RECORD ---
    # El remitente EXACTO que pide el pliego. Los datos salen de las
    # constantes de la orden: mismo papel, misma identidad.
    y = _titulo("SHIPPER OF RECORD", y)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(izq, y, _seguro(_pg.EMPRESA))
    y -= 11
    c.setFont("Helvetica", 7)
    for linea in _pg.DIRECCION:
        c.drawString(izq, y, _seguro(linea))
        y -= 10
    c.setFont("Helvetica", 6.8)
    c.drawString(izq, y, f"{_pg.TELEFONO}  |  {_pg.EMAIL}")
    y -= 10
    c.setFont("Helvetica-Bold", 6.8)
    c.drawString(izq, y, f"UEI {_pg.UEI}  |  NCAGE {_pg.NCAGE}")
    y -= 14

    # --- CONSIGNEE ---
    # El elemento dominante de la etiqueta: es lo que lee el transportista y
    # el personal de la base, y tiene que verse a distancia. Por eso va mas
    # grande que el resto.
    y = _titulo("CONSIGNEE (SHIP TO)", y)
    lineas = _consignee(analisis, _pg)
    if lineas:
        c.setFont("Helvetica-Bold", 9.5)
        for linea in lineas[:4]:
            c.drawString(izq, y, _seguro(linea))
            y -= 12.5
    else:
        # Sin direccion fisica no se inventa una. Se dice lo que falta, que
        # es lo mismo que hace la orden de compra en su fila de Ship-To.
        c.setFont("Helvetica-Oblique", 7)
        c.drawString(izq, y, _seguro(_pg.SIN_DESTINO_CONFIRMADO)[:64])
        y -= 12
    y -= 4

    # --- REFERENCIAS CRUZADAS ---
    # Solicitud, CLIN y bultos/unidades: lo que el personal de la base usa
    # para casar el bulto con la linea del contrato.
    y = _titulo("REFERENCES", y)
    clins = []
    for b in (analisis.get("bom_lineas") or []):
        if isinstance(b, dict):
            cl = str(b.get("clin") or "").strip()
            if cl and cl not in clins:
                clins.append(cl)
    cant = analisis.get("cantidad_total")
    try:
        cant_txt = ("%d" % cant) if float(cant) == int(float(cant)) else str(cant)
    except (TypeError, ValueError):
        cant_txt = ""
    uni = str(analisis.get("unidad_medida") or "").strip().upper()
    unidades = f"{cant_txt} {uni}".strip() if cant_txt else ""
    try:
        vence = _pg._fecha_pdf(analisis.get("limite")) or ""
    except Exception:
        vence = ""
    refs = (
        ("Solicitation No.", str(analisis.get("solicitation") or "").strip()),
        ("Notice ID", _numero(notice_id)),
        ("CLIN", ", ".join(clins)),
        ("Units", unidades),
        ("Packages (boxes / pallets)", ""),   # no esta en el aviso: se rellena
        ("Delivery due", vence),
    )
    for etiqueta, valor in refs:
        c.setFont("Helvetica", 6.4)
        c.setFillColor(gris)
        c.drawString(izq, y, etiqueta)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 7.4)
        c.drawString(izq + 108, y, _seguro(valor) or "____________")
        y -= 11
    y -= 3

    # --- CONTENIDO ---
    y = _titulo("CONTENTS", y)
    prod = _RE_MD.sub("", str(analisis.get("producto") or "")).strip()
    prod_en = ""
    if prod:
        try:
            traducido, pendiente = _pg._ingles(prod, 150)
            # Si no se pudo traducir ENTERO, el castellano no va a un papel
            # que lee un transportista de EE.UU. Se queda en blanco y mandan
            # los identificadores, que no tienen idioma.
            if not pendiente:
                prod_en = traducido
        except Exception:
            prod_en = ""
    lineas_c = [x for x in _cortar(prod_en, 56)[:3] if x] if prod_en else []
    if lineas_c:
        c.setFont("Helvetica", 7.2)
        for linea in lineas_c:
            c.drawString(izq, y, _seguro(linea))
            y -= 9.5
    # Piezas / NSN: identificadores tecnicos, validos en cualquier idioma.
    piezas = []
    for b in (analisis.get("bom_lineas") or []):
        if isinstance(b, dict):
            pn = str(b.get("parte") or "").strip()
            if pn and pn not in piezas:
                piezas.append(pn)
    c.setFont("Helvetica", 6.8)
    if piezas:
        c.drawString(izq, y, "Part / NSN: " + _seguro(", ".join(piezas)[:64]))
        y -= 9.5
    elif not lineas_c:
        c.setFont("Helvetica-Oblique", 6.8)
        c.drawString(izq, y, "Item description: see the solicitation.")
        y -= 9.5
    c.setFont("Helvetica", 6.2)
    c.setFillColor(gris)
    c.drawString(izq, y,
                 "Mark every box with the solicitation number and the CLIN.")
    c.setFillColor(colors.black)
    y -= 13

    # --- Relleno a mano (peso y volumen no estan en el aviso) ---
    c.setFont("Helvetica", 6.6)
    c.drawString(izq, y, "Total weight: ____________     "
                         "Volume: ____________")
    y -= 10
    c.setFont("Helvetica", 5.6)
    c.setFillColor(gris)
    c.drawString(izq, y,
                 "Shipper of Record for blind drop-shipping: "
                 f"{_pg.EMPRESA}.")
    c.setFillColor(colors.black)

    c.showPage()
    c.save()
    return ruta


def _cortar(texto: str, ancho: int) -> list[str]:
    palabras = str(texto or "").split()
    if not palabras:
        return [""]
    lineas, actual = [], palabras[0]
    for p in palabras[1:]:
        if len(actual) + 1 + len(p) <= ancho:
            actual = f"{actual} {p}"
        else:
            lineas.append(actual)
            actual = p
    lineas.append(actual)
    return lineas
