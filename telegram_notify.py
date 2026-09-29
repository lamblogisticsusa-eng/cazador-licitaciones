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
import re
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


def _corte_seguro(linea: str, corte: int, limite: int) -> int:
    """
    Ajusta un punto de corte para que no caiga DENTRO de una etiqueta HTML.

    _trocear corta en un espacio, y ese espacio puede caer dentro de un
    <a href="..."> largo. La primera mitad del trozo queda con la etiqueta sin
    cerrar y Telegram rechaza el mensaje. Si hay un "<" abierto sin cerrar en
    el punto de corte, el corte se retrasa hasta antes de ese "<".

    Garantiza devolver un valor > 0. Si no hay ningun sitio seguro (por
    ejemplo una sola linea que empieza con "<" y nunca cierra antes del
    limite), se corta en el limite aunque haya que partir la etiqueta: es un
    caso rarísimo, _sanear_html escapa el "<" roto del trozo y Telegram lo
    acepta. Lo que NO puede pasar es devolver 0, porque entonces el bucle de
    troceado no avanza y se queda colgado para siempre.
    """
    abierto = linea.rfind("<", 0, corte)
    if abierto == -1:
        return corte
    cerrado = linea.find(">", abierto, corte)
    if cerrado == -1:
        nuevo = linea.rfind(" ", 0, abierto)
        if nuevo > 0:
            return nuevo
        return min(limite, max(1, corte))
    return corte


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
            # Un corte en un espacio puede caer dentro de una etiqueta, si la
            # linea es un <a href="..."> largo. La primera mitad quedaria con
            # la etiqueta sin cerrar y Telegram rechaza el trozo entero.
            corte = _corte_seguro(linea, corte, limite)
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
    degradado = False
    for trozo in _trocear(texto):
        cuerpo = _sanear_html(trozo) if html_mode else trozo
        payload = {"chat_id": chat_id, "text": cuerpo}
        if html_mode:
            payload["parse_mode"] = "HTML"
            payload["disable_web_page_preview"] = True
        res = _pedir("sendMessage", payload)
        if res is None and html_mode:
            # Reintento en texto plano: si falla por HTML, asi al menos llega.
            # Pero se avisa, porque llega SIN negritas, SIN links y SIN
            # cursivas. Antes esto pasaba inadvertido: enviar() devolvia True
            # y el usuario recibia un bloque gris creyendo que era normal.
            log.error(
                "Telegram rechazo el HTML; se reenvia en texto plano. Llega, "
                "pero sin formato ni links. Revisa _sanear_html y las "
                "constantes de kyo.py."
            )
            plano = {"chat_id": chat_id, "text": cuerpo}
            res = _pedir("sendMessage", plano)
            degradado = True
        if res is None:
            ok = False
    return ok and not degradado


# Etiquetas que Telegram acepta en parse_mode=HTML. Todo lo demas se escapa:
# un "<" suelto es un intento de etiqueta y hace que Telegram rechace el
# mensaje ENTERO con un 400, sin enviar nada.
#
# Esto viene de un fallo real del 28-sep-2026: la firma de Kyomoto era
# "... (˶>ᴗ<˶) ⁾" y el "<" de "ᴗ<" lo leia Telegram como el inicio de una
# etiqueta. Resultado:
#   Bad Request: can't parse entities: Unsupported start tag "\u02f6)"
# La ficha no llegaba, y como enviar() reintenta en texto plano y devolvia
# True, el sistema decia "ok" mientras mandaba un bloque gris sin formato.
_ETIQUETAS_OK = re.compile(
    r"^</?([a-z][a-z0-9-]*)"
    r"(?:\s[^<>]*?)?/?>$",
    re.IGNORECASE,
)
# Las que Telegram acepta de verdad en parse_mode=HTML. Las demas, aunque
# tengan forma de etiqueta, se escapan: "unsupported start tag" tambien es un
# 400 que tira el mensaje entero.
_ETIQUETAS_TELEGRAM = {"b", "i", "u", "s", "a", "code", "pre",
                       "blockquote", "tg-spoiler"}


def _sanear_html(texto: str) -> str:
    """
    Deja el HTML en un estado que Telegram acepta, aunque el texto venga
    roto. Red de seguridad, no sustituto de _esc: los campos sueltos de
    Gemini siguen pasando por _esc, que escapa ampersand y comillas.

    Hace tres cosas:

    1. Escapa cualquier "<" que no abra una etiqueta de las que Telegram
       acepta. Ese era el fallo real: la firma "(\u02f6>\u1d17\u02f6)" traia un
       "<" suelto y Telegram rechazaba la ficha entera con
       "Unsupported start tag".
    2. Escapa tambien el ">" del mismo fragmento, para que se vea el texto tal
       cual y no dependa de que un ">" suelto sea legal.
    3. Cierra las etiquetas que se hayan quedado abiertas al final. Una "<b>"
       sin su "</b>" tambien es un 400, y aqui no se nota mirando el texto.

    No toca las entidades ya escapadas (&lt;, &gt;, &amp;): no empiezan por
    "<", y el recorrido solo mira los "<".
    """
    if "<" not in texto:
        return texto

    partes = []
    abiertas: list[str] = []
    i = 0
    largo = len(texto)
    while i < largo:
        j = texto.find("<", i)
        if j == -1:
            partes.append(texto[i:])
            break
        partes.append(texto[i:j])
        # Telegram corta en el primer ">", asi que se busca ese y no mas lejos.
        k = texto.find(">", j)
        if k == -1:
            partes.append("&lt;")
            i = j + 1
            continue

        trozo = texto[j:k + 1].strip()
        m = _ETIQUETAS_OK.match(trozo)
        nombre = (m.group(1) or "").lower() if m else ""
        cerrar = trozo.startswith("</")
        # Un "/>" final no abre nada: es un cierre disfrazado de apertura.
        auto_cierra = trozo.endswith("/>")

        if not m or nombre not in _ETIQUETAS_TELEGRAM:
            partes.append("&lt;" + texto[j + 1:k] + "&gt;")
        elif cerrar:
            if nombre in abiertas:
                # Anidamiento imperfecto como "<b>a<i>b</b>": el </b> cierra la
                # de fuera, pero la <i> de dentro se queda abierta y Telegram
                # rechaza el mensaje. Se emiten los cierres de lo que se
                # descarta antes del que cierra de verdad.
                while abiertas and abiertas[-1] != nombre:
                    partes.append(f"</{abiertas.pop()}>")
                if abiertas:
                    abiertas.pop()
                partes.append(trozo)
            else:
                partes.append("&lt;" + texto[j + 1:k] + "&gt;")
        else:
            if not auto_cierra:
                abiertas.append(nombre)
            partes.append(trozo)
        i = k + 1

    # Lo que quedo abierto se cierra aqui. Sin esto, "<b>texto" sin cierre
    # haria que Telegram rechazase el mensaje entero.
    while abiertas:
        partes.append(f"</{abiertas.pop()}>")
    return "".join(partes)


def _esc(valor) -> str:
    return html.escape(str(valor if valor is not None else ""), quote=False)


def _cantidad(valor) -> str:
    """Sin decimales si es entera. 50.0 se ve como 50, no como 50.0 EA."""
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return str(valor or "")
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.2f}"


def _usd(valor) -> str:
    if valor is None:
        return "N/E"
    return f"${valor:,.2f}"


MESES = ("ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic")


def _fecha_legible(bruto) -> tuple[str, str]:
    """
    Convierte el limite de ofertar a algo que se lee de un vistazo.

    Devuelve ("Vence en 36 dias", "4 nov 2026, 17:00 UTC-4"). El dato mas
    accionable de la ficha no puede quedar como "2026-11-04T17:00:00-04:00" en
    medio de un bloque de logistica: eso no lo lee nadie.

    Si la fecha no se puede interpretar, devuelve dos cadenas vacias y la
    ficha lo omite, en vez de ensuciar con un formato raro.
    """
    if not bruto:
        return "", ""
    from datetime import datetime, timezone

    texto = str(bruto).strip().replace("Z", "+00:00")
    dt = None
    for parseo in (datetime.fromisoformat,):
        try:
            dt = parseo(texto)
            break
        except (ValueError, TypeError):
            continue
    if dt is None:
        return "", ""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    ahora = datetime.now(timezone.utc)
    dias = (dt - ahora).days
    # "4 nov 2026, 17:00 UTC-4" montado a mano: "%-d" (dia sin cero) es una
    # extension de glibc y NO existe en Windows, donde da
    # "ValueError: Invalid format string". Kyomoto corre en Render (Linux)
    # pero el diagnostico corre en el portatil del usuario, y una función que
    # solo funciona en un sistema no es una función.
    zona = ""
    if dt.utcoffset() is not None:
        total = int(dt.utcoffset().total_seconds() // 60)
        signo = "+" if total >= 0 else "-"
        zona = f" UTC{signo}{abs(total) // 60:02d}"
    cuando = (
        f"{dt.day} {MESES[dt.month - 1]} {dt.year}, "
        f"{dt.hour:02d}:{dt.minute:02d}{zona}"
    )

    if dias < 0:
        return "⚠️ VENCIÓ", cuando
    if dias == 0:
        return "⏰ VENCE HOY", cuando
    if dias == 1:
        return "⏰ Vence mañana", cuando
    if dias <= 10:
        return f"⏰ Vence en {dias} días", cuando
    return f"🗓️ Vence en {dias} días", cuando


def _decision(a: dict) -> str:
    """
    La linea de triage: un vistazo y sabes si abrir el enlace o no.

    Se responde la pregunta que importa al leer un aviso "¿esto me sirve?":
    cuanto queda, cuanto gano y si el margen pasa mi piso. Sin esto habia que
    leer hasta el final para decidir.
    """
    neta = a.get("ganancia_neta_usd")
    margen = a.get("margen_neto_porcentaje")
    if neta is None:
        return ""
    umbral = config.MARGEN_NETO_MIN * 100
    if margen is None:
        return f"Te quedarías <b>{_usd(neta)}</b> netos."
    pasa = "pasa tu piso" if margen >= umbral else "⚠️ por debajo de tu piso"
    return (
        f"Te quedarías <b>{_usd(neta)}</b> netos "
        f"(<b>{margen:.1f}%</b> · {pasa} del {umbral:.0f}%)"
    )


def formatear_analisis(a: dict) -> str:
    """
    La ficha, en el formato que pidio el usuario.

    Regla de oro: la cuenta se sostiene. Si el bot dice que vas a ofertar
    $74,500, la ganancia se mide contra $74,500 y no contra el presupuesto
    del gobierno, porque lo que entra a tu cuenta es lo que ofertaste.
    """
    import distribuidores
    import kyo

    L = ["✨ <b>¡Amo, encontré una oportunidad!</b> (≧◡≦)", ""]

    # --- Encabezado ---
    L.append(f"📦 <b>{_esc(a['title'])}</b>")
    L.append(f"🏛️ {_esc(a['agencia'])}")

    # Identificacion en una linea. El NAICS dice si es producto fisico y el
    # PSC que clase de compra es, que es justo lo que hay que comprobar antes
    # de perder la tarde.
    _codes = [f"🏷️ {_esc(a['set_aside'])}"] if a.get("set_aside") else []
    if a.get("naics"):
        _codes.append(f"NAICS {_esc(a['naics'])}")
    if a.get("psc"):
        _codes.append(f"PSC {_esc(a['psc'])}")
    if _codes:
        L.append(" · ".join(_codes))

    L.append(f"🆔 Solicitud: <code>{_esc(a['solicitation'])}</code>")

    # La fecha va ARRIBA. Es el dato que decide si hay tiempo, y antes vivia
    # en el bloque de logistica con formato ISO crudo.
    urgency,cuando = _fecha_legible(a.get("limite"))
    if urgency:
        L.append(f"<b>{urgency}</b> · {cuando}")
    L.append("")

    # La linea de decision: cuanto gano y si pasa mi piso. Sin esto habia que
    # leer la ficha entera para saber si valia la pena abrir el enlace.
    d = _decision(a)
    if d:
        L.append(f"💵 {d}")
        L.append("")

    # --- Descripcion del producto ---
    L.append("📝 <b>Descripción del Producto:</b>")
    descripcion = _esc(a.get("producto") or "")
    if a.get("modelo_especifico"):
        descripcion += f" ({_esc(a['modelo_especifico'])})"
    cantidad = a.get("cantidad_total")
    unidad = a.get("unidad_medida") or "EA"
    if cantidad:
        descripcion += f" — {_cantidad(cantidad)} {_esc(str(unidad))}"
    L.append(descripcion)
    if a.get("especificacion_tecnica_clave"):
        L.append(f"<i>{_esc(a['especificacion_tecnica_clave'])}</i>")
    L.append("")

    # --- Analisis financiero ---
    L.append("💰 <b>Análisis Financiero Estimado:</b>")
    L.append(f"• Presupuesto Est. Gobierno: <b>{_usd(a['valor_contrato_usd'])}</b>")
    L.append(f"• Costo Est. Proveedor/Distribuidor: <b>{_usd(a['costo_total_usd'])}</b>")
    L.append(
        f"• Ganancia Neta Proyectada: <b>{_usd(a['ganancia_neta_usd'])}</b>"
        + (f" ({a['margen_neto_porcentaje']:.1f}% de margen neto)"
           if a.get("margen_neto_porcentaje") is not None else "")
    )
    L.append("")

    # --- La cuenta por unidad ---
    if a.get("cantidad_total") and a.get("precio_unitario_costo"):
        L.append("🔢 <b>La cuenta por unidad:</b>")
        L.append(f"• Comprar cada una: <b>{_usd(a['precio_unitario_costo'])}</b>")
        L.append(f"• Precio de catálogo: <b>{_usd(a['precio_unitario_mercado'])}</b>")
        L.append(f"• Ofertar cada una: <b>{_usd(a['precio_unitario_oferta'])}</b>")
        L.append(f"• <b>Ganancia por unidad: {_usd(a['ganancia_por_unidad'])}</b>")
        L.append("")

    # --- Desglose del margen, con el factoring a la vista ---
    margen_bruto = a.get("margen_bruto_porcentaje")
    margen_neto = a.get("margen_neto_porcentaje")
    if margen_bruto is not None:
        L.append("🏦 <b>De dónde sale el margen:</b>")
        L.append(
            f"• Ganancia bruta: <b>{_usd(a['ganancia_total_usd'])}</b> "
            f"({margen_bruto:.1f}%)"
        )
        L.append(
            f"• Factoring ({config.FACTORING_PCT * 100:.1f}%): "
            f"<b>-{_usd(a['costo_factoring_usd'])}</b>"
        )
        L.append(
            f"• <b>Neta real: {_usd(a['ganancia_neta_usd'])}</b>"
            + (f" ({margen_neto:.1f}% neto)" if margen_neto is not None else "")
        )
        L.append(
            f"<i>Objetivo: {config.MARGEN_BRUTO_MIN * 100:.0f}%–"
            f"{config.MARGEN_BRUTO_MAX * 100:.0f}% bruto, "
            f"mínimo {config.MARGEN_NETO_MIN * 100:.0f}% neto.</i>"
        )
        L.append("")

    # --- Estrategia de oferta ---
    L.append("🎯 <b>Estrategia de Oferta Sugerida:</b>")
    L.append(f"• Precio Sugerido para Licitar: <b>{_usd(a['precio_oferta_sugerido_usd'])}</b>")
    if a.get("razonamiento_oferta"):
        L.append(f"({_esc(a['razonamiento_oferta'])})")
    elif a.get("estrategia_oferta"):
        L.append(f"({_esc(a['estrategia_oferta'])})")
    if a.get("margen_por_distribuidor"):
        L.append("")
        L.append("💡 <b>Dónde está el mejor margen:</b>")
        L.append(_esc(a["margen_por_distribuidor"]))
    L.append("")

    # --- Distribuidores ---
    L.append("🔍 <b>Búsqueda Automática de Distribuidores:</b>")
    d = distribuidores.para_oportunidad(
        a.get("busquedas_distribuidores") or [],
        a.get("producto") or "",
        a.get("lugar_entrega") or "",
    )
    etiqueta, url = d["principal"]
    # La etiqueta ya trae su propio emoji; anteponer otro lo duplicaba y se
    # leia como "🔎 🔎 Buscar en Google".
    L.append(f'<a href="{_esc(url)}">{_esc(etiqueta)}</a>')
    for nombre, u, nota in d["sitios"][:4]:
        L.append(f"• <a href=\"{_esc(u)}\">🏬 {_esc(nombre)}</a> — <i>{_esc(nota)}</i>")
    L.append("")
    L.append("<i>Para buscar a mano:</i>")
    L.append(f"<code>{_esc(distribuidores.texto_plano(d['terminos']))}</code>")
    L.append("")

    # --- Logistica y contacto ---
    L.append("📍 <b>Entrega:</b>")
    L.append(f"• Destino: {_esc(a['lugar_entrega'])}")
    if a.get("contacto"):
        L.append(f"• Contacto: {_esc(a['contacto'])}")
    if a.get("sin_descripcion"):
        L.append("")
        L.append(
            f"⚠️ <i>SAM.gov no publicó descripción de este aviso; las cifras "
            f"son estimaciones. Antes de ofertar, léelo en el enlace.</i>"
        )
    if a.get("preguntas_criticas"):
        L.append("")
        L.append(f"❓ <b>{kyo.PREGUNTA_ANTES}</b>")
        for p in a["preguntas_criticas"]:
            L.append(f"• {_esc(p)}")
    if a.get("nivel_riesgo"):
        L.append("")
        # Sin el "·" esto se leia como una frase enredada: "Riesgo: MEDIO
        # ojo con esto, hay que revisar".
        L.append(
            f"⚠️ Riesgo: <b>{_esc(a['nivel_riesgo'].upper())}</b> · "
            f"{kyo.emoji_riesgo(a['nivel_riesgo'])}"
        )
    L.append("")

    # --- Enlace ---
    L.append("🔗 <b>Enlace Directo SAM.gov:</b>")
    if a.get("ui_link"):
        L.append(f"📄 <a href=\"{_esc(a['ui_link'])}\"><b>Ver Ficha Completa de la Licitación</b></a>")
    else:
        L.append("📄 Ver Ficha Completa de la Licitación")

    L.append("")
    L.append(f"<i>{kyo.CIERRE}</i>")
    # Ultima red: aunque alguien escribiera un "<" a mano en una constante,
    # la ficha sale con el HTML valido. Sin esto, un solo angulo suelto
    # rechazaba el mensaje ENTERO con un 400 y la ficha no llegaba.
    return _sanear_html("\n".join(L))


def aviso_error(chat_id: str, titulo: str, detalle: str) -> None:
    cuerpo = (
        f"🚨 <b>{_esc(titulo)}</b>\n\n"
        f"<pre>{_esc(detalle[:2500])}</pre>\n\n"
        "<i>Kyomoto necesita que revises la configuracion.</i>"
    )
    enviar(chat_id, cuerpo, html_mode=True)
