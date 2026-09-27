"""
kyo.py - La voz de Kyomoto.
Todo el texto que ve el usuario pasa por aqui, para que el tono sea siempre
kawaii sin ensuciar el resto del codigo ni los datos.
"""
from __future__ import annotations

# Apertura segun cuantos resultados salieron.
SALUDOS = {
    "muchas": "Kyomoto se lucio hoy ✨",
    "varias": "Kyomoto trae cositas buenas ✿",
    "una": "Kyomoto te guardo una ♡",
    "ninguna": "Kyomoto sigue escaneando ✿",
}

CIERRE = "Kyomoto passandote la info para que tu llegue a tiempo (⁠˶>ᴗ<˶) ⁾"

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
        lineas.append(
            f"🔋 Cuota de hoy: {q.get('usadas', 0)}/{q.get('presupuesto', 0)} &nbsp;|&nbsp; "
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
