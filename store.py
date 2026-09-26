"""
store.py - Persistencia de oportunidades ya notificadas.

AVISO IMPORTANTE sobre Render:
  El plan gratuito de Render usa un disco EFIMERA. Cada deploy, cada reinicio
  y cada spin-down borra el archivo SQLite. Eso significa que el bot "olvida"
  que ya te notifico algo y te lo vuelve a mandar.
  Opciones:
    1) Plan pagado de Render ($7/mes) -> el disco persiste.
    2) Montar un disco externo (no disponible en free).
    3) Guardar el historial en GitHub/Turso/Supabase (recomendado a futuro).
  Este modulo funciona en los 3 casos: si el archivo se pierde, simplemente
  vuelve a empezar, que es el comportamiento deseado.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import config

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS licitaciones_procesadas (
    notice_id      TEXT PRIMARY KEY,
    fecha_procesado TEXT NOT NULL,
    titulo         TEXT,
    viable         INTEGER DEFAULT 0,
    puntaje        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_procesadas_fecha ON licitaciones_procesadas(fecha_procesado);
CREATE TABLE IF NOT EXISTS etiquetas (
    notice_id TEXT PRIMARY KEY,
    archivo    TEXT,
    creado    TEXT
);
"""


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conexion():
    """Abre una conexion segura. El lock evita 'database is locked' cuando
    varios hilos (scheduler + escaneo manual) escriben a la vez."""
    conexion = sqlite3.connect(config.DB_PATH, timeout=30)
    try:
        conexion.execute("PRAGMA journal_mode=WAL")
        conexion.execute("PRAGMA busy_timeout=30000")
        yield conexion
        conexion.commit()
    finally:
        conexion.close()


def init_db() -> None:
    with _lock, _conexion() as c:
        c.executescript(_SCHEMA)


def ya_procesada(notice_id: str) -> bool:
    try:
        with _lock, _conexion() as c:
            fila = c.execute(
                "SELECT 1 FROM licitaciones_procesadas WHERE notice_id = ?", (notice_id,)
            ).fetchone()
            return fila is not None
    except sqlite3.Error:
        return False


def ids_procesados() -> set:
    try:
        with _lock, _conexion() as c:
            return {f[0] for f in c.execute("SELECT notice_id FROM licitaciones_procesadas")}
    except sqlite3.Error:
        return set()


def marcar(notice_id: str, titulo: str = "", viable: bool = False, puntaje: int = 0) -> None:
    try:
        with _lock, _conexion() as c:
            c.execute(
                "INSERT OR REPLACE INTO licitaciones_procesadas "
                "(notice_id, fecha_procesado, titulo, viable, puntaje) VALUES (?,?,?,?,?)",
                (notice_id, _ahora(), (titulo or "")[:300], 1 if viable else 0, puntaje),
            )
    except sqlite3.Error as e:
        print(f"[store] No se pudo guardar {notice_id}: {e}")


def limpiar() -> int:
    with _lock, _conexion() as c:
        n = c.execute("SELECT COUNT(*) FROM licitaciones_procesadas").fetchone()[0]
        c.execute("DELETE FROM licitaciones_procesadas")
        return n


def guardar_etiqueta(notice_id: str, archivo: str) -> None:
    with _lock, _conexion() as c:
        c.execute(
            "INSERT OR REPLACE INTO etiquetas (notice_id, archivo, creado) VALUES (?,?,?)",
            (notice_id, archivo, _ahora()),
        )


def total_procesadas() -> int:
    with _lock, _conexion() as c:
        return c.execute("SELECT COUNT(*) FROM licitaciones_procesadas").fetchone()[0]
