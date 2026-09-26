"""
label.py - Genera la etiqueta de envio y el packing list en PDF.

Sirve para el comando /etiqueta: toma la oportunidad que Kyomoto ya analizo y
produce un PDF con los datos de envio, listos para imprimir o para pegar sobre
la caja. Usa reportlab, que ya estaba en tu requirements.txt.

NO inventa datos: si no hay peso o direccion, lo deja en blanco para que lo
rellenes. Kyomoto no se la juega con la informacion de una etiqueta.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import config

_ANCHO_ETIQUETA = (4, 2)  # pulgadas: 4x2 es el estandar de UPS/FedEx
_ALTO_ETIQUETA = 6


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


def generar(notice_id: str, analisis: dict) -> str | None:
    """Crea el PDF y devuelve la ruta, o None si reportlab no esta disponible."""
    try:
        from reportlab.lib.pagesizes import inch
        from reportlab.lib.units import inch as _in
        from reportlab.pdfgen import canvas
    except ImportError:
        return None

    os.makedirs(config.ETIQUETAS_DIR, exist_ok=True)
    ruta = os.path.join(config.ETIQUETAS_DIR, nombre_archivo(notice_id, analisis.get("solicitation", "")))
    ancho, alto = _ANCHO_ETIQUETA[0] * inch, _ANCHO_ETIQUETA[1] * inch

    c = canvas.Canvas(ruta, pagesize=(ancho, alto))
    c.setTitle(f"Etiqueta {analisis.get('solicitation', '')}")
    m = 0.18 * inch

    # --- Marco ---
    c.setLineWidth(1.2)
    c.rect(m, m, ancho - 2 * m, alto - 2 * m)

    # --- Cabecera ---
    c.setFont("Helvetica-Bold", 8)
    c.drawString(m + 8, alto - m - 14, "KYOMOTO LOGISTICS  |  ETIQUETA DE ENVIO")
    c.setFont("Helvetica", 6.5)
    c.drawRightString(ancho - m - 8, alto - m - 14,
                      f"Generada {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    c.setLineWidth(0.5)
    c.line(m + 6, alto - m - 20, ancho - m - 6, alto - m - 20)

    y = alto - m - 40

    # --- DE / ENVIADO DESDE ---
    c.setFont("Helvetica-Bold", 6.5)
    c.drawString(m + 8, y, "DESDE (ORIGEN)")
    c.setFont("Helvetica", 8)
    c.drawString(m + 8, y - 12, "Kyomoto Logistics")
    c.setFont("Helvetica", 7)
    c.drawString(m + 8, y - 23, "Santiago, Chile")
    c.setFont("Helvetica", 6.5)
    c.drawString(m + 8, y - 34, "REMITENTE: completar con tus datos y RUT")
    y -= 56

    # --- PARA / DIRECCION DE ENTREGA ---
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(m + 8, y, "ENTREGAR A (DESTINO OFICIAL DE LA LICITACION)")
    y -= 14
    destino = str(analisis.get("lugar_entrega") or "")
    c.setFont("Helvetica", 8)
    if destino and destino != "No especificado":
        for linea in _cortar(destino, 52)[:4]:
            c.drawString(m + 8, y, _seguro(linea))
            y -= 11
    else:
        c.setFont("Helvetica-Oblique", 7.5)
        c.drawString(m + 8, y, "*** SIN DIRECCION: bajar la direccion oficial de SAM.gov ***")
        y -= 12
        c.setFont("Helvetica", 7)

    c.setFont("Helvetica", 6.5)
    c.drawString(m + 8, y, "PUNTO DE CONTACTO EN DESTINO:")
    y -= 22

    # --- Referencia del contrato ---
    c.setFont("Helvetica-Bold", 7)
    c.drawString(m + 8, y, "DATOS DEL CONTRATO")
    y -= 12
    c.setFont("Helvetica", 7)
    for etiqueta, valor in (
        ("Solicitacion", analisis.get("solicitation", "N/A")),
        ("Notice ID", _numero(notice_id)),
        ("Fecha limite", str(analisis.get("limite", "N/A"))[:30]),
    ):
        c.drawString(m + 8, y, f"{etiqueta}: {_seguro(valor)}")
        y -= 10

    y -= 6

    # --- Contenido ---
    c.setFont("Helvetica-Bold", 7)
    c.drawString(m + 8, y, "CONTENIDO")
    y -= 12
    c.setFont("Helvetica", 7.5)
    for linea in _cortar(analisis.get("producto", "*** completar ***"), 52)[:5]:
        c.drawString(m + 8, y, _seguro(linea))
        y -= 10.5
    c.setFont("Helvetica", 6.5)
    c.drawString(m + 8, y, f"Cantidad: {_seguro(analisis.get('cantidad_estimada') or 'a confirmar')}")
    y -= 10
    c.drawString(m + 8, y, f"Peso total: __________ kg    Volumen: __________ m3")
    y -= 18

    # --- Aviso de valor ---
    c.setFont("Helvetica-Bold", 6.5)
    c.drawString(m + 8, y, "DECLARACION DE VALOR (verificar antes de enviar)")
    y -= 10
    c.setFont("Helvetica", 7)
    valor = analisis.get("valor_contrato_usd")
    c.drawString(m + 8, y, f"Valor declarado (USD): {'%.2f' % valor if valor else 'a definir'}")
    y -= 10
    c.setFont("Helvetica-Oblique", 6)
    c.drawString(m + 8, y, "Verificar el umbral de sobreprecio de USPS segun el valor declarado.")
    y -= 20

    # --- Barra inferior de codigos ---
    c.setFont("Helvetica-Bold", 7)
    c.drawString(m + 8, y, "IDENTIFICACION")
    y -= 12
    c.setFont("Courier", 11)
    c.drawString(m + 8, y, f"||KYO{_numero(notice_id)}||")
    y -= 12
    c.setFont("Helvetica", 6)
    c.drawString(m + 8, y, "Kyomoto no es transportista. Kyomoto coordina; el porte lo contrata el usuario.")

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
