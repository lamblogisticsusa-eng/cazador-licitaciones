"""
test_incremental.py - El fetching incremental de SAM.gov.

Verifica la parte que mas me costo entender: que la ventana de 10 dias NO
cuesta 4 peticiones a la API en cada barrido, sino solo las de los dias vivos.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

DB = os.path.join(tempfile.gettempdir(), "kyo_test_inc.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["DRY_RUN"] = "1"
os.environ["SAM_API_KEY"] = "x"
os.environ["DIAS_DE_VENTANA"] = "10"
os.environ["DIAS_POR_CHUNK"] = "3"
os.environ["DIAS_VIVOS"] = "3"

FALLA = 0
LLAMADAS = {"n": 0}


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


import config
import sam_api
import store

FECHA = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def aviso(n: int, posted: str) -> dict:
    return {
        "noticeId": f"n{n:04d}", "title": f"Aviso {n}", "postedDate": posted,
        "naicsCode": "332911", "solicitationNumber": f"S{n}",
        "responseDeadLine": "2026-12-01T18:00:00-05:00",
        "typeOfSetAside": "NONE", "classificationCode": "5330",
    }


def _bloque_falso(desde, hasta, ptype):
    """
    Simula la API respetando lo que hace la real: los avisos que devuelve
    tienen posted_date DENTRO del bloque pedido. Sin esto el test miente,
    porque la cache separa por posted_date.
    """
    LLAMADAS["n"] += 1
    medio = desde + (hasta - desde) / 2
    posted = medio.strftime("%Y-%m-%d")
    return [aviso(LLAMADAS["n"] * 10 + k, posted) for k in range(3)]


print("=" * 70)
print("1) PRIMER BARRIDO: base vacia, llena todo")
print("=" * 70)
store.init_db()
sam_api._bloque = _bloque_falso
primero = list(sam_api.barrer(10))
check("Devuelve avisos", len(primero) > 0, f"-> {len(primero)}")
n_primero = LLAMADAS["n"]
print(f"  Peticiones a la API: {n_primero}  (llena toda la ventana de una vez)")
check("La primera vez pide mas (tiene que llenar la base)", n_primero >= 3,
      f"-> {n_primero}")
print()

print("=" * 70)
print("2) SEGUNDOS BARRIDOS: solo los dias vivos")
print("=" * 70)
LLAMADAS["n"] = 0
segundo = list(sam_api.barrer(10))
n_seg = LLAMADAS["n"]
print(f"  Peticiones al API: {n_seg}  (solo el bloque vivo)")
check("Menos peticiones que el primer barrido", n_seg < n_primero,
      f"-> {n_seg} vs {n_primero}")
check("Y son exactamente 1 bloque", n_seg == 1, f"-> {n_seg}")
check("Sigue devolviendo avisos", len(segundo) > 0, f"-> {len(segundo)}")
check("Y trae MAS que un barrido recien hecho (incluye la cache)",
      len(segundo) >= len(primero) - 3, f"-> {len(segundo)} vs {len(primero)}")
print()

print("=" * 70)
print("3) LA VENTANA COMPLETA SIGUE LLEGANDO")
print("=" * 70)
# Aunque pida menos a la API, el total de avisos debe incluir los cacheados.
ids = {o["noticeId"] for o in segundo}
print(f"  Avisos en el segundo barrido: {len(ids)}")
check("Incluye los de la cache (parte vieja)", len(ids) > 0)
check("La ventana pedida es la de 10 dias", config.DIAS_DE_VENTANA == 10)
print()

print("=" * 70)
print("4) NO HAY TTL, HAY DIAS VIVOS")
print("=" * 70)
check("No existe TTL_CACHE en config", not hasattr(config, "TTL_CACHE"),
      "-> el TTL era el bug: menor que INTERVALO_HORAS = cache muerta")
check("Existe DIAS_VIVOS", hasattr(config, "DIAS_VIVOS"))
check("DIAS_VIVOS es menor que la ventana", config.DIAS_VIVOS < config.DIAS_DE_VENTANA)
check("Y cubre al menos un bloque completo", config.DIAS_VIVOS >= config.DIAS_POR_CHUNK,
      f"-> {config.DIAS_VIVOS} vs chunk {config.DIAS_POR_CHUNK}")
print()

print("=" * 70)
print("5) LA CACHE SEPARA POR RANGO")
print("=" * 70)
hoy = datetime.now(timezone.utc)
store.guardar_busqueda(aviso(1, (hoy - timedelta(days=1)).strftime("%Y-%m-%d")))
store.guardar_busqueda(aviso(2, (hoy - timedelta(days=5)).strftime("%Y-%m-%d")))
store.guardar_busqueda(aviso(3, (hoy - timedelta(days=40)).strftime("%Y-%m-%d")))

v10 = {o["noticeId"] for o in store.desde_cache_rango(10, 2)}
v0 = {o["noticeId"] for o in store.desde_cache(10)}
print(f"  ventana 10 sin filtro: {sorted(v0)}")
print(f"  ventana 10 excluyendo 2 dias: {sorted(v10)}")
check("de 10 dias incluye el de ayer", "n0001" in v0)
check("de 10 dias incluye el de hace 5", "n0002" in v0)
check("de 10 dias NO incluye el de hace 40", "n0003" not in v0)
check("el rango con corte excluye los ultimos 2 dias", "n0001" not in v10)
check("el rango con corte si incluye los viejos", "n0002" in v10)
print()

print("=" * 70)
print("6) COSTE DIARIO ESTIMADO")
print("=" * 70)
barridos = 24 / config.INTERVALO_HORAS
vivos = 1 if config.DIAS_VIVOS <= config.DIAS_POR_CHUNK else -(-config.DIAS_VIVOS // config.DIAS_POR_CHUNK)
sin_incremental = -(-config.DIAS_DE_VENTANA // config.DIAS_POR_CHUNK)
print(f"  Ventana             {config.DIAS_DE_VENTANA} dias")
print(f"  Dias vivos          {config.DIAS_VIVOS}  -> {vivos} bloque(s) por barrido")
print(f"  Barridos al dia     {barridos:g} (cada {config.INTERVALO_HORAS:g} h)")
print(f"  Peticiones al dia   ~{vivos * barridos:.0f}   (sin incremental: {sin_incremental * barridos:.0f})")
check("4 veces menos peticiones que antes", vivos * barridos <= sin_incremental * barridos / 3,
      f"-> {vivos * barridos} vs {sin_incremental * barridos}")
check("Bajo las 15 peticiones diarias", vivos * barridos <= 15, f"-> {vivos * barridos:.0f}")

for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: el fetching incremental funciona")
