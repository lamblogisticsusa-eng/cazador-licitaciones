"""
menu.py - El menu de Kyomoto en Telegram.
Botones en linea para no tener que memorizar comandos. Cada boton llama a un
callback "k:accion" que main.py despacha al handler del comando equivalente.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

PREFIJO = "k:"


def teclado(activo: bool) -> InlineKeyboardMarkup:
    filas = [
        [
            InlineKeyboardButton("🔍 Buscar ahora", callback_data=PREFIJO + "buscar"),
            InlineKeyboardButton("📊 Estado", callback_data=PREFIJO + "estado"),
        ],
        [
            InlineKeyboardButton("🔋 Cuota de hoy", callback_data=PREFIJO + "cuota"),
            InlineKeyboardButton("📋 Por qué descarté", callback_data=PREFIJO + "puntajes"),
        ],
        [
            InlineKeyboardButton("🏷 Etiqueta PDF", callback_data=PREFIJO + "pdf"),
            InlineKeyboardButton("🩺 Autodiagnóstico", callback_data=PREFIJO + "selftest"),
        ],
    ]
    boton_pausa = (
        InlineKeyboardButton("⏸ Pausar búsqueda", callback_data=PREFIJO + "off")
        if activo
        else InlineKeyboardButton("▶️ Reactivar búsqueda", callback_data=PREFIJO + "on")
    )
    filas.append([boton_pausa])
    return InlineKeyboardMarkup(filas)


def teclado_ayuda() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔍 Buscar ahora", callback_data=PREFIJO + "buscar")]]
    )
