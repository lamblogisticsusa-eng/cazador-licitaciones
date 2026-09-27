"""
telegram_notify.py - Envio de mensajes a Telegram sin que se pierdan.

PROBLEMAS DEL CODIGO ORIGINAL QUE ESTE MODULO ARREGLA:

1) Enviaba con parse_mode="Markdown". El texto lo genera Gemini, y el Markdown
   de Telegram es BIZOSO: solo acepta *b* _i_ `c` [t](u). Cualquier guion bajo
   o asterisco suelto (una URL, un nombre de archivo, un "10_000") hace que
   Telegram responda 400 y el mensaje se pierda. Aqui usamos HTML con escape
   correcto, que es mucho mas indulgente.

2) Telegram tiene un limite duro de 4096 caracteres por mensaje. El analisis
   de Gemini puede pasarse. Si el mensaje es largo, Telegram responde 400 por
   texto demasiado largo y el original reintentaba SIN parse_mode: fallaba de
   nuevo y el mensaje se perdia igual. Aqui dividimos en trozos de forma
   deliberada.

3) Si sendMessage falla, no se avisa. Aqui los errores quedan en el log.
"""
from __future__ import annotations

import html
import logging

import requests

import config

log = logging.getLogger("kyomoto.telegram")

LIMITE_TELEGRAM = 4096
RESERVA = 32  # margen para el <pre> / cierre de etiquetas

_url = "https://api.telegram.org/bot{token}/{metodo}"


def _pedir(metodo: str, payload: dict) -> dict | None:
    if not config.TELEGRAM_BOT_TOKEN:
        log.error("No hay TELEGRAM_BOT_TOKEN. No se puede enviar.")
        return None
    url = _url.format(token=config.TELEGRAM_BOT_TOKEN, metodo=metodo)
    try:
        r = requests.post(url, json=payload, timeout=20)
    except requests.RequestException as e:
        log.error("Falla de red enviando a Telegram: %s", e)
        return None
    if r.status_code != 200:
        log.error("Telegram %s -> %s", r.status_code, r.text[:300])
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _trocear(texto: str, limite: int = LIMITE_TELEGRAM - RESERVA) -> list[str]:
    """Divide respetando saltos de linea y, a falta de ellos, palabras."""
    if len(texto) <= limite:
        return [texto]
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        while len(linea) > limite:
            corte = linea.rfind(" ", 0, limite)
            if corte <= 0:
                corte = limite
            if actual:
                trozos.append(actual)
                actual = ""
            trozos.append(linea[:corte])
            linea = linea[corte:].lstrip()
        if len(actual) + len(linea) + 1 > limite:
            trozos.append(actual)
            actual = linea
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        trozos.append(actual)
    return [t for t in trozos if t.strip()]


def enviar(chat_id: str, texto: str, html_mode: bool = True) -> bool:
    """Envia texto, troceado si hace falta. Nunca lanza excepcion."""
    if not chat_id:
        log.error("No hay TELEGRAM_CHAT_ID de destino.")
        return False
    ok = True
    for trozo in _trocear(texto):
        payload = {"chat_id": chat_id, "text": trozo}
        if html_mode:
            payload["parse_mode"] = "HTML"
            payload["disable_web_page_preview"] = True
        res = _pedir("sendMessage", payload)
        if res is None:
            # Reintento en texto plano: si falla por HTML, asi al menos llega.
            if html_mode:
                plano = {"chat_id": chat_id, "text": trozo}
                res = _pedir("sendMessage", plano)
            if res is None:
                ok = False
    return ok


def _esc(valor) -> str:
    return html.escape(str(valor if valor is not None else ""), quote=False)


def _usd(valor) -> str:
    if valor is None:
        return "N/E"
    return f"${valor:,.2f}"


def formatear_analisis(a: dict) -> str:
    """Render HTML de la ficha. Todo pasa por html.escape, por eso ya no hay
    riesgo de que el texto de Gemini rompa el formato."""
    import kyo
    import distribuidores

    L: list[str] = []
    L.append(kyo.cabecera_ficha())
    L.append("")

    # --- Encabezado y enlace a SAM.gov ---
    L.append(f"📦 <b>{_esc(a['title'])}</b>")
    L.append(f"🔢 Solicitud: <code>{_esc(a['solicitation'])}</code>")
    if a.get("ui_link"):
        L.append(
            f"🔗 <a href=\"{_esc(a['ui_link'])}\">📄 Ver aviso completo en SAM.gov</a>"
        )
    L.append(f"🏛 <i>{_esc(a['agencia'])}</i>")
    L.append(f"🗂 NAICS <code>{_esc(a['naics'])}</code> | PSC <code>{_esc(a['psc'])}</code>")
    L.append(f"⭐ {_esc(a['set_aside'])}")
    L.append("")

    # --- Que hay que entregar ---
    L.append("📝 <b>Que hay que entregar</b>")
    L.append(_esc(a["producto"]))
    if a.get("modelo_especifico"):
        L.append(f"Modelo: <b>{_esc(a['modelo_especifico'])}</b>")

    # Cantidad y unidad: es la base de toda la cuenta.
    cantidad = a.get("cantidad_total")
    unidad = a.get("unidad_medida") or "EA"
    if cantidad:
        L.append(f"Cantidad: <b>{cantidad:,.0f} {_esc(str(unidad))}</b>")
    if a["especificacion_tecnica_clave"]:
        L.append("")
        L.append("🔧 <b>Especificacion tecnica clave</b>")
        L.append(_esc(a["especificacion_tecnica_clave"]))
    L.append(f"📍 Destino: <b>{_esc(a['lugar_entrega'])}</b>")
    L.append(f"⏰ Limite para ofertar: <code>{_esc(a['limite'])}</code>")
    if a.get("sin_descripcion"):
        L.append(f"⚠️ <i>{kyo.EMOCIONES['sin_descripcion']}. Las cifras son estimaciones.</i>")
    L.append("")

    # --- La cuenta, por unidad ---
    L.append("💰 <b>La cuenta, por unidad</b>")
    L.append(f"• Comprar cada una en USA: <b>{_usd(a['precio_unitario_costo'])}</b>")
    L.append(f"• Precio de catalogo de cada una: <b>{_usd(a['precio_unitario_mercado'])}</b>")
    L.append(f"• Ofertar cada una a: <b>{_usd(a['precio_unitario_oferta'])}</b>")
    L.append(f"• <b>Ganancia por unidad: {_usd(a['ganancia_por_unidad'])}</b>")
    L.append("")

    # --- El total, con factoring ---
    L.append("📊 <b>El total</b>")
    L.append(f"• Valor del contrato: <b>{_usd(a['valor_contrato_usd'])}</b>")
    L.append(f"• Costo de compra: {_usd(a['costo_total_usd'])}")
    mb = a.get("margen_bruto_porcentaje")
    mn = a.get("margen_neto_porcentaje")
    L.append(
        f"• <b>Ganancia bruta: {_usd(a['ganancia_total_usd'])}</b>"
        + (f" ({mb:.1f}%)" if mb is not None else "")
    )
    L.append(
        f"• Factoring ({config.FACTORING_PCT * 100:.1f}%): "
        f"-{_usd(a['costo_factoring_usd'])}"
    )
    L.append(
        f"• <b>Ganancia neta: {_usd(a['ganancia_neta_usd'])}</b>"
        + (f" ({mn:.1f}%)" if mn is not None else "")
    )
    L.append(f"• <b>Ofertar: {_usd(a['precio_oferta_sugerido_usd'])}</b>")
    L.append("")
    L.append(
        f"<i>Objetivo: {config.MARGEN_BRUTO_MIN * 100:.0f}%–"
        f"{config.MARGEN_BRUTO_MAX * 100:.0f}% bruto; minimo "
        f"{config.MARGEN_NETO_MIN * 100:.0f}% neto tras factoring.</i>"
    )
    L.append("")

    if a.get("margen_por_distribuidor"):
        L.append("💡 <b>Donde esta el mejor margen</b>")
        L.append(_esc(a["margen_por_distribuidor"]))
        L.append("")

    # --- Estrategia ---
    L.append("🎯 <b>Estrategia de oferta</b>")
    L.append(f"Ofertar: <b>{_usd(a['precio_oferta_sugerido_usd'])}</b>")
    if a["estrategia_oferta"]:
        L.append(_esc(a["estrategia_oferta"]))
    L.append(f"Riesgo: <b>{_esc(a['nivel_riesgo'].upper())}</b> {kyo.emoji_riesgo(a['nivel_riesgo'])}")
    L.append("")

    # --- Distribuidores en USA ---
    L.append("🔍 <b>Distribuidores en USA</b>")
    d = distribuidores.para_oportunidad(
        a.get("busquedas_distribuidores") or [],
        a.get("producto") or "",
        a.get("lugar_entrega") or "",
    )
    etiqueta, url = d["principal"]
    L.append(f'<a href="{_esc(url)}">{_esc(etiqueta)}</a>')
    L.append("")
    L.append("_Toca para abrir, ya filtrado por el producto y por USA:_")
    for nombre, u, nota in d["sitios"]:
        L.append(f'• <a href="{_esc(u)}">🏬 {_esc(nombre)}</a> — <i>{_esc(nota)}</i>')
    L.append("")
    L.append("_Para buscar a mano:_")
    L.append(f"<code>{_esc(distribuidores.texto_plano(d['terminos']))}</code>")
    L.append("")

    if a["preguntas_criticas"]:
        L.append(f"❓ <b>{kyo.PREGUNTA_ANTES}</b>")
        for p in a["preguntas_criticas"]:
            L.append(f"• {_esc(p)}")
        L.append("")

    if a["observaciones"]:
        L.append(f"⚠️ {_esc(a['observaciones'])}")
        L.append("")

    L.append(f"👤 Contacto: {_esc(a['contacto'])}")
    L.append("")
    L.append(f"<i>{kyo.CIERRE}</i>")
    return "\n".join(L)


def aviso_error(chat_id: str, titulo: str, detalle: str) -> None:
    cuerpo = (
        f"🚨 <b>{_esc(titulo)}</b>\n\n"
        f"<pre>{_esc(detalle[:2500])}</pre>\n\n"
        "<i>Kyomoto necesita que revises la configuracion.</i>"
    )
    enviar(chat_id, cuerpo, html_mode=True)
