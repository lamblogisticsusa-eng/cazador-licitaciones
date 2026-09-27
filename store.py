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
from datetime import datetime, timedelta, timezone

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
CREATE TABLE IF NOT EXISTS cache_descripciones (
    notice_id  TEXT PRIMARY KEY,
    texto      TEXT NOT NULL,
    guardado   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache_busqueda (
    notice_id    TEXT PRIMARY KEY,
    posted_date  TEXT NOT NULL,
    datos        TEXT NOT NULL,
    guardado     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_busqueda_posted ON cache_busqueda(posted_date);
CREATE TABLE IF NOT EXISTS rangos_buscados (
    desde  TEXT NOT NULL,
    hasta  TEXT NOT NULL,
    dia    TEXT NOT NULL,
    PRIMARY KEY (desde, hasta, dia)
);
"""


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hoy() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _hace(dias: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d")


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


def cache_descripcion(notice_id: str, texto: str) -> None:
    """Guarda la descripcion para no volver a pagarla a la API.

    La API de SAM.gov tiene tope de peticiones por dia. Como se descarto una
    oportunidad puede volver al top en el siguiente barrido, cachear evita
    gastar la cuota en el mismo texto una y otra vez.
    """
    if not texto:
        return
    with _lock, _conexion() as c:
        c.execute(
            "INSERT OR REPLACE INTO cache_descripciones (notice_id, texto, guardado) "
            "VALUES (?,?,?)",
            (notice_id, texto, _ahora()),
        )


def leer_descripcion(notice_id: str) -> str:
    init_db()
    with _lock, _conexion() as c:
        fila = c.execute(
            "SELECT texto FROM cache_descripciones WHERE notice_id = ?", (notice_id,)
        ).fetchone()
    return fila[0] if fila else ""


def limpiar_cache() -> int:
    with _lock, _conexion() as c:
        n = c.execute("SELECT COUNT(*) FROM cache_descripciones").fetchone()[0]
        c.execute("DELETE FROM cache_descripciones")
        return n


def rango_buscado(desde: str, hasta: str) -> bool:
    """Ese tramo de fechas ya se consulto hoy y no hay que volver a pedirlo."""
    init_db()
    with _lock, _conexion() as c:
        f = c.execute(
            "SELECT 1 FROM rangos_buscados WHERE desde=? AND hasta=? AND dia=?",
            (desde, hasta, _hoy()),
        ).fetchone()
        return f is not None


def marcar_rango(desde: str, hasta: str) -> None:
    init_db()
    with _lock, _conexion() as c:
        c.execute(
            "INSERT OR REPLACE INTO rangos_buscados (desde, hasta, dia) VALUES (?,?,?)",
            (desde, hasta, _hoy()),
        )


def guardar_busqueda(opp: dict) -> None:
    """Cachea el aviso completo.

    Con una ventana de 10 dias la API se consulta en bloques de 2 dias: son
    5 peticiones por barrido, y con barridos cada 2 h serian 60 al dia. La
    cache hace que los tramos ya vistos cuesten 0 peticiones y que un aviso
    reanalizado tampoco.
    """
    import json as _json
    nid = opp.get("noticeId")
    posted = str(opp.get("postedDate") or "")
    if not nid or not posted:
        return
    try:
        datos = _json.dumps(opp, ensure_ascii=False)
    except (TypeError, ValueError):
        return
    with _lock, _conexion() as c:
        c.execute(
            "INSERT OR REPLACE INTO cache_busqueda (notice_id, posted_date, datos, guardado) "
            "VALUES (?,?,?,?)",
            (nid, posted, datos, _ahora()),
        )


def desde_cache(dias: int) -> list[dict]:
    """Avisos cacheados publicados dentro de la ventana de N dias."""
    return desde_cache_rango(dias, 0)


def desde_cache_rango(dias_max: int, dias_min: int = 0) -> list[dict]:
    """
    Avisos cacheados publicados entre hace(dias_max) y hace(dias_min).

    Es lo que hace posible el fetching incremental: la parte vieja de la
    ventana (que ya no cambia) sale de la base y la reciente se pide a la API.
    """
    import json as _json
    init_db()
    with _lock, _conexion() as c:
        filas = c.execute(
            "SELECT datos FROM cache_busqueda WHERE posted_date >= ? AND posted_date < ?",
            (_hace(dias_max), _hace(dias_min)),
        ).fetchall()
    salida = []
    for (crudo,) in filas:
        try:
            salida.append(_json.loads(crudo))
        except ValueError:
            continue
    return salida


def limpiar_cache_busqueda(dias: int = 30) -> int:
    init_db()
    with _lock, _conexion() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM cache_busqueda WHERE posted_date < ?", (_hace(dias),)
        ).fetchone()[0]
        c.execute("DELETE FROM cache_busqueda WHERE posted_date < ?", (_hace(dias),))
        c.execute("DELETE FROM rangos_buscados WHERE dia < ?", (_hace(3),))
        return n


def total_procesadas() -> int:
    # Autoinicializable por el mismo motivo que quota.estado(): lo llama
    # texto_estado() desde los botones del menu, y en un proceso recien
    # arrancado la tabla todavia no existe.
    init_db()
    with _lock, _conexion() as c:
        return c.execute("SELECT COUNT(*) FROM licitaciones_procesadas").fetchone()[0]
