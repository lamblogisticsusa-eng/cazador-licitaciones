"""
quota.py - Presupuesto diario de llamadas a Gemini.

Por que existe: el plan gratuito de Gemini tiene limites de RPM, TPM y RPD
que no son publicos (solo se ven dentro de AI Studio). Pedir 25 analisis
por barrido sin saber el limite real termina en 429 a mitad del escaneo y
se desperdicia lo que ya se habia gastado.

La estrategia es no depender de conocer el limite:
  - un presupuesto propio de N llamadas al dia, persistente
  - ritmo lento y fijo entre llamadas
  - al primer 429 se para el dia en vez de reintentar y gastar mas
  - lo que no se pudo analizar NO se marca como visto, para que mañana
    siga en la lista
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import config

_lock = threading.Lock()

# Medianoche del Pacifico, que es donde Google reinicia el RPD.
_PACIFICO = ZoneInfo("America/Los_Angeles")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS uso_gemini (
    dia        TEXT PRIMARY KEY,
    llamadas   INTEGER NOT NULL DEFAULT 0,
    fallidas   INTEGER NOT NULL DEFAULT 0,
    ultima     TEXT
);
CREATE TABLE IF NOT EXISTS vista_quota (
    notice_id  TEXT PRIMARY KEY,
    dia        TEXT NOT NULL
);
"""


@contextmanager
def _conexion():
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with _lock, _conexion() as c:
        c.executescript(_SCHEMA)


def hoy_pacifico() -> str:
    return datetime.now(_PACIFICO).strftime("%Y-%m-%d")


def estado() -> dict:
    """Cuantas llamadas quedan hoy. No cuenta como llamada."""
    # Autoinicializable: texto_estado() y texto_cuota() se llaman desde los
    # botones del menu, y en un proceso recien arrancado las tablas todavia no
    # existen. Que reviente el /cuota seria justo el fallo que no queremos.
    init()
    with _lock, _conexion() as c:
        fila = c.execute(
            "SELECT llamadas, fallidas FROM uso_gemini WHERE dia = ?", (hoy_pacifico(),)
        ).fetchone()
    usadas, fallidas = (fila or (0, 0))
    presupuesto = presupuesto_ajustado()
    return {
        "dia": hoy_pacifico(),
        "usadas": usadas,
        "fallidas": fallidas,
        "presupuesto": presupuesto,
        "configurado": config.PRESUPUESTO_GEMINI_DIARIO,
        "restantes": max(0, presupuesto - usadas),
        "agotado": usadas >= presupuesto,
    }


def quedan() -> int:
    return estado()["restantes"]


def gastar(n: int = 1) -> None:
    with _lock, _conexion() as c:
        c.execute(
            "INSERT INTO uso_gemini (dia, llamadas, fallidas, ultima) VALUES (?,?,0,?) "
            "ON CONFLICT(dia) DO UPDATE SET llamadas = llamadas + ?, ultima = ?",
            (hoy_pacifico(), n, datetime.now(timezone.utc).isoformat(), n,
             datetime.now(timezone.utc).isoformat()),
        )


def gastar_fallo() -> None:
    with _lock, _conexion() as c:
        c.execute(
            "INSERT INTO uso_gemini (dia, llamadas, fallidas, ultima) VALUES (?,0,1,?) "
            "ON CONFLICT(dia) DO UPDATE SET fallidas = fallidas + 1, ultima = ?",
            (hoy_pacifico(), datetime.now(timezone.utc).isoformat(),
             datetime.now(timezone.utc).isoformat()),
        )


def reservar(notice_id: str) -> bool:
    """
    True si este aviso entra hoy al presupuesto. False si ya se contó hoy.
    Evita que un reintento dentro del mismo barrido gaste dos cupos.
    """
    with _lock, _conexion() as c:
        c.execute(
            "INSERT OR IGNORE INTO vista_quota (notice_id, dia) VALUES (?,?)",
            (notice_id, hoy_pacifico()),
        )
        return c.total_changes > 0


def limpiar_reservas() -> int:
    """Borra reservas de dias anteriores. Se llama al arrancar."""
    with _lock, _conexion() as c:
        n = c.execute("SELECT COUNT(*) FROM vista_quota WHERE dia != ?", (hoy_pacifico(),)).fetchone()[0]
        c.execute("DELETE FROM vista_quota WHERE dia != ?", (hoy_pacifico(),))
        return n


def limpiar() -> int:
    """Borra el conteo de hoy. Solo para pruebas; el bot nunca lo llama."""
    with _lock, _conexion() as c:
        n = c.execute("SELECT COUNT(*) FROM uso_gemini").fetchone()[0]
        c.execute("DELETE FROM uso_gemini")
        c.execute("DELETE FROM vista_quota")
        return n


def dias_registrados() -> list[dict]:
    init()
    with _lock, _conexion() as c:
        filas = c.execute(
            "SELECT dia, llamadas, fallidas FROM uso_gemini ORDER BY dia DESC LIMIT 14"
        ).fetchall()
    return [{"dia": f[0], "llamadas": f[1], "fallidas": f[2]} for f in filas]


def presupuesto_ajustado() -> int:
    """
    Aprende de la historia para no repetir el mismo error.

    Los limites RPM/TPM/RPD del plan gratuito no son publicos: no aparecen en
    la documentacion, solo dentro de AI Studio. Asi que en vez de adivinar un
    numero fijo, Kyomoto observa que paso ayer y baja el liston si se topo:

      - si ayer hubo fallos por cuota, hoy se pide menos
      - si ayer se completo sin fallos, hoy se pide un poco mas
      - nunca baja de PRESUPUESTO_MINIMO ni sube de PRESUPUESTO_GEMINI_DIARIO

    Asi el presupuesto se ajusta solo la primera semana y despues se estabiliza.
    """
    base = config.PRESUPUESTO_GEMINI_DIARIO
    piso = min(config.PRESUPUESTO_MINIMO, base)
    filas = dias_registrados()[:3]
    if not filas:
        return base
    top = filas[0]
    if top["fallidas"] > 0 and top["llamadas"] >= base:
        return max(piso, base - 3)
    if top["fallidas"] == 0 and top["llamadas"] >= base:
        return base  # se completo el liston sin problemas: no subir mas
    return base


def por_barrido() -> int:
    """
    Cuantas llamadas usar en UN barrido.

    Antes era presupuesto_ajustado() // barridos. Con 20 de presupuesto y
    barrido cada 2h eso da 1: Kyomoto analizaba UNA licitacion cada 2 horas y
    el resto del presupuesto se perdia solo al reiniciar a medianoche del
    Pacifico. Repartir a partes iguales tiene sentido cuando el presupuesto es
    un ritmo, pero aqui es un TECHO: lo que no se usa, se pierde.

    Ahora pide lo necesario para trabajar bien cada barrido, con un piso de 2
    y sin pasar de MAX_A_GEMINI. El tope diario sigue mandando por separado
    (queda() y el calculo de top en el escaner), asi que esto no puede gastar
    de mas: solo deja de desperdiciar la cuota.
    """
    barridos = max(1, int(24 / max(config.INTERVALO_HORAS, 0.5)))
    techo = min(presupuesto_ajustado() // barridos, config.MAX_A_GEMINI)
    return max(2, techo)
