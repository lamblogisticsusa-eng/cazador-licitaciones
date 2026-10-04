"""
kyo.py - La voz de Kyomoto.
Todo el texto que ve el usuario pasa por aqui, para que el tono sea siempre
kawaii sin ensuciar el resto del codigo ni los datos.
"""
from __future__ import annotations

import config

MIN = config.MIN_USD
MAX = config.TOPE_USD
HORAS = config.INTERVALO_HORAS

# Apertura segun cuantos resultados salieron.
SALUDOS = {
    "muchas": "Kyomoto se lucio hoy ✨",
    "varias": "Kyomoto trae cositas buenas ✿",
    "una": "Kyomoto te guardo una ♡",
    "ninguna": "Kyomoto sigue escaneando ✿",
}

CIERRE = (
    "Kyomoto pasandote la info para que tu llegue a tiempo "
    "(˶ ᴗ ˶) ⁾"
)

EMOCIONES = {
    "riesgo_bajo": "todo tranquilo (｡•̀ᴗ-)✧",
    "riesgo_medio": "ojo con esto, hay que revisar (’-⌒-’)",
    "riesgo_alto": "esto se ve {(°-°)} con cuidado (¬‿¬)",
    "sin_descripcion": "datos estimados por titulo, sin descripcion oficial",
}

PREGUNTA_ANTES = "Antes de ofertar, confirma esto ♡"


def saludo(cantidad: int) -> str:
    if cantidad >= 6:
        return SALUDOS["muchas"]
    if cantidad >= 2:
        return SALUDOS["varias"]
    if cantidad == 1:
        return SALUDOS["una"]
    return SALUDOS["ninguna"]


def emoji_riesgo(nivel: str) -> str:
    return EMOCIONES.get(f"riesgo_{nivel.lower()}", "")


def resumen_escaneo(r: dict) -> str:
    """Tarjeta de fin de barrido, con los numeros que importan."""
    viables = r.get("viables", 0)
    lineas = [f"{saludo(viables)}"]
    lineas.append("")
    lineas.append(f"🔎 Avisos revisados: <b>{r.get('traidas', 0)}</b> en SAM.gov")
    lineas.append(f"📦 Con producto fisico: <b>{r.get('puntuales', 0)}</b> pasaron el filtro")
    lineas.append(f"🧠 Analizados por IA: <b>{r.get('analizadas', 0)}</b>")
    lineas.append(f"💎 <b>Viables para ti: {viables}</b>")
    q = r.get("cuota") or {}
    if q:
        lineas.append("")
        # El separador va con "\u00b7" y NO con &nbsp;.
        #
        # Telegram, con parse_mode=HTML, solo entiende cuatro entidades:
        # &lt;, &gt;, &amp; y &quot;. Cualquier otra, &nbsp; incluida, hace que
        # el servidor conteste 400 "can't parse entities: Unsupported entity"
        # y RECHACE EL MENSAJE ENTERO. No sale un signo raro: no sale nada.
        #
        # Aca no se notaba porque main.py envuelve la llamada en un
        # try/except y reenvia en texto plano, o sea que el mensaje llegaba
        # igual... con la cadena literal "&nbsp;|&nbsp;" escrita en el chat.
        # Esa es la version visible del fallo, y es la que se reporto.
        #
        # OJO: esto NO aplica a pdf_generator.py, que tambien usa &nbsp;.
        # Ahi es reportlab, que SI la admite, y la necesita para poner el
        # telefono y el email en la misma linea. Si se toca ese archivo, el
        # PDF se rompe en lugar de arreglarse.
        lineas.append(
            f"🔋 Cuota de hoy: {q.get('usadas', 0)}/{q.get('presupuesto', 0)} \u00b7 "
            f"quedan <b>{q.get('restantes', 0)}</b>"
        )
    if not viables:
        lineas.append("")
        lineas.append(
            "<i>Hoy no salio nada que pase el filtro. Kyomoto vuelve en un rato ♡</i>"
        )
    return "\n".join(lineas)


def cabecera_ficha() -> str:
    return "✨ <b>Kyomoto encuentra una oportunidad</b> ✨"


# ==========================================================================
#  MENU
# ==========================================================================

TITULO = "🌸 <b>Kyomoto</b> · needy logistics"
SUBTITULO = (
    "Busco licitaciones de <b>compra de productos</b> en SAM.gov que puedas "
    "despachar a destino.\n"
    f"Rango: <b>USD {MIN:,.0f} – {MAX:,.0f}</b> · cada {HORAS:g} h\n"
    "Solo avisos que piensen en <i>cosas físicas</i>, no servicios (ᐢ..ᐢ)"
)
CIERRE_MENU = "Toca un boton ✿"


def saludo_menu(activo: bool) -> str:
    if activo:
        estado = "🟢 <b>Buscando activa</b> · te aviso cuando haya algo bueno"
    else:
        estado = "⏸ <b>En pausa</b> · no voy a buscar hasta que me digas"
    return f"{estado}\n\n{SUBTITULO}"


def resumen_menu(activo: bool) -> str:
    """Cuerpo del /start: saludo kawaii + estado + que hace."""
    if activo:
        linea = "Kyon~ aqui ando ✨ <b>Lista para trabajar.</b>"
    else:
        linea = "Kyon~ aqui ando ✨ <b>Estoy en pausa, amo.</b> (/on cuando quieras)"
    return f"{linea}\n\n{saludo_menu(activo)}"

