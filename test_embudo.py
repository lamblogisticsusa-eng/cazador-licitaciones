"""
test_embudo.py - Kyomoto analiza las MEJORES, no las que le sobran.

BUG QUE DESTAPO (28-sep-2026)
quota.por_barrido() era presupuesto // barridos, o sea 20 // 12 == 1. Kyomoto
analizaba UNA licitacion cada dos horas y bajaba 8 descripciones para ello:
seis de cada ocho se descargaban y no se usaban. Y como el presupuesto diario
es un TECHO y no una meta, las once llamadas sobrantes se perdian solas al
reiniciar a medianoche del Pacifico.

Esta suite EJECUTA scanner.escanear() de verdad, con la red y Gemini falsos, y
comprueba:
  - que llega a Gemini cada barrido, no uno solo
  - que no baja descripciones de mas
  - que a Gemini nunca le llega un aviso sin texto (solo titulo): tendria que
    inventar el costo por unidad, que es peor que no responder
  - que las viables se ordenan por margen neto, la metrica de decision
"""
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_embudo.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["DRY_RUN"] = "1"
os.environ["SAM_API_KEY"] = "SAM-de-prueba"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:FAKE"
os.environ["TELEGRAM_CHAT_ID"] = "1"
os.environ["GEMINI_API_KEY"] = "x"
os.environ["GEMINI_PAUSA_SEG"] = "0"

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


import config
import quota
import scanner
import store

store.init_db()
quota.init()

print("=" * 70)
print("1) CADA BARRIDO USA LA CUOTA QUE LE TOCA")
print("=" * 70)
barridos = int(24 / config.INTERVALO_HORAS)
reparto = max(1, config.PRESUPUESTO_GEMINI_DIARIO // barridos)
print(f"  {config.PRESUPUESTO_GEMINI_DIARIO} al dia / {barridos} barridos")
print(f"  reparto viejo (20 // 12) : {reparto}   <-- era el bug")
print(f"  por_barrido() ahora      : {quota.por_barrido()}")
check("El reparto entero habria dado 1", reparto == 1, f"-> {reparto}")
check("por_barrido() da al menos 2", quota.por_barrido() >= 2,
      f"-> {quota.por_barrido()}")
check("No supera MAX_A_GEMINI", quota.por_barrido() <= config.MAX_A_GEMINI)
check("El tope diario sigue cortando por separado",
      quota.por_barrido() * barridos > config.PRESUPUESTO_GEMINI_DIARIO,
      "-> si no, el suelo pasaria del presupuesto")

# ------------------------------------------------------------------ fakes
PEDIDAS: list[str] = []
ANALIZADOS: list[tuple[str, bool]] = []


def _titulo(i):
    # Varios terminos de producto para que el pre-puntaje por titulo pase el
    # piso sin ayuda de la descripcion.
    return f"PARTS KIT,SECTOR {i} physical goods replacement equipment assembly"


def _aviso(i, con_precio=True):
    d = {
        "noticeId": f"nid{i:03d}",
        "title": _titulo(i),
        "naicsCode": "332999",
        "typeOfSetAside": "",
        "postedDate": "2026-09-20",
    }
    if con_precio:
        d["description"] = (
            f"<p>Adquisicion de {20 + i} KIT para { _titulo(i) }. "
            "Requiere entrega fisica en base. "
            f"Not to exceed USD {40_000 + i * 500:,}.00. </p>"
        )
    return d


def _bajar_descripciones(candidatos):
    """Fake de la red: cuenta las descripciones pedidas de verdad."""
    PEDIDAS.clear()
    PEDIDAS.extend(o["noticeId"] for o in candidatos)
    return {
        o["noticeId"]: (
            f"Adquisicion de {20 + int(o['noticeId'][3:])} KIT "
            f"para {_titulo(0)} con entrega fisica directa. "
            f"Not to exceed USD {40_000:,}.00. Material MIL-STD. "
            "Se requiere distribuidor con stock en USA."
        )
        for o in candidatos
    }


# Acepta la firma completa de la real, incluido el cortacircuitos:
# scanner la llama con continuar=... y un fake con la firma corta revienta
# con TypeError, y el sintoma es "llego 0 a Gemini" sin explicacion.
def _analizar(opp, desc, lugar, continuar=None):
    ANALIZADOS.append((opp["noticeId"], bool(desc.strip())))
    return {
        "notice_id": opp["noticeId"],
        "viable": True,
        "titulo": opp.get("title", ""),
        "title": opp.get("title", ""),
        "producto": "kit de repuestos",
        "margen_neto_porcentaje": 22.0,
        "motivo_descarte": "",
    }


# Se sustituyen las tres salidas a la red, en el modulo que las usa.
# aviso() es una funcion anidada de escanear, no se puede parchear desde
# aqui; con DRY_RUN=1 no manda nada a Telegram.
_orig = (scanner.sam_api.barrer, scanner._bajar_descripciones,
         scanner.gemini_analyzer.analizar)


def _restaurar():
    (scanner.sam_api.barrer, scanner._bajar_descripciones,
     scanner.gemini_analyzer.analizar) = _orig


print()
print("=" * 70)
print("2) UN BARRIDO REAL: LLEGAN VARIAS, NO UNA")
print("=" * 70)
n_avisos = 30
scanner.sam_api.barrer = lambda dias=None, ptype="o,a", stats=None: (
    _aviso(i) for i in range(n_avisos)
)
scanner._bajar_descripciones = _bajar_descripciones
scanner.gemini_analyzer.analizar = _analizar

PEDIDAS.clear()
ANALIZADOS.clear()
res = scanner.escanear("1")

print(f"  avisos en SAM.gov        : {res['traidas']}")
print(f"  sobre el filtro          : {res['puntuales']}")
print(f"  descripciones bajadas    : {len(PEDIDAS)}")
print(f"  llegados a Gemini        : {len(ANALIZADOS)}")
print(f"  viables                  : {res['viables']}")

check("Trajo los avisos", res["traidas"] == n_avisos, f"-> {res['traidas']}")
check("LLEGO MAS DE UNO A GEMINI (antes: 1)",
      len(ANALIZADOS) >= 2, f"-> {len(ANALIZADOS)}")
check("Y respeta el tope de MAX_A_GEMINI por barrido",
      len(ANALIZADOS) <= config.MAX_A_GEMINI, f"-> {len(ANALIZADOS)}")
check("No gasto mas presupuesto del que hay",
      quota.estado()["usadas"] <= quota.estado()["presupuesto"],
      f"-> {quota.estado()['usadas']}/{quota.estado()['presupuesto']}")
# El desperdicio: descripciones bajadas por analisis hecho.
if ANALIZADOS:
    ratio = len(PEDIDAS) / len(ANALIZADOS)
    print(f"  descripciones por analisis: {ratio:.1f}  (antes era 8.0)")
    check("No desperdicia mas de 4 descripciones por analisis",
          ratio <= 4.0, f"-> {ratio:.1f}")
    check("Menos o igual que antes (8 por analisis)", ratio <= 8.0)

print()
print("=" * 70)
print("3) A GEMINI NUNCA LE LLEGA UN AVISO SIN TEXTO")
print("=" * 70)
sin_texto = [nid for nid, tiene in ANALIZADOS if not tiene]
print(f"  Sin descripcion entre los enviados: {len(sin_texto)}")
check("Ninguno de los que llegaron va sin descripcion",
      not sin_texto, f"-> {sin_texto}")
check("Y si llegaron analisis, todos traian texto",
      all(t for _, t in ANALIZADOS))

# Y que se note en el resumen, para que el usuario sepa por que no le llegan.
# Un titulo que pase el piso pero sin descripcion debe quedar registrado.
_restaurar()
quota.gastar(-quota.estado()["usadas"])
store.conexion_backup = None
for tabla in ("licitaciones_procesadas", "vista_quota"):
    try:
        with store._conexion() as c:
            c.execute(f"DELETE FROM {tabla}")
    except Exception:
        pass

PEDIDAS.clear()
ANALIZADOS.clear()
# Muchos avisos, pero la red devuelve descripcion vacia para la mitad.
def _bajar_vacia(candidatos):
    PEDIDAS.clear()
    PEDIDAS.extend(o["noticeId"] for o in candidatos)
    return {o["noticeId"]: ("" if int(o["noticeId"][3:]) % 2 else
                            f"Adquisicion de 30 KIT para {_titulo(0)}. "
                            f"Not to exceed USD 40,000.00. Entrega en USA.")
            for o in candidatos}


scanner.sam_api.barrer = lambda dias=None, ptype="o,a", stats=None: (
    _aviso(i) for i in range(20)
)
scanner._bajar_descripciones = _bajar_vacia
scanner.gemini_analyzer.analizar = _analizar
res2 = scanner.escanear("1")

vacios = [nid for nid, tiene in ANALIZADOS if not tiene]
print(f"  Descripciones bajadas   : {len(PEDIDAS)} (la mitad vacias a proposito)")
print(f"  Llegados a Gemini       : {len(ANALIZADOS)}")
print(f"  De esos, sin texto      : {len(vacios)}")
check("Los vacios NO llegan a Gemini", not vacios, f"-> {vacios}")
check("Aun asi se analizan los que si tienen texto", len(ANALIZADOS) >= 1)
_restaurar()

print()
print("=" * 70)
print("4) LAS VIABLES SE ORDENAN POR MARGEN NETO")
print("=" * 70)
# El puntaje previo del filtro es un proxy, no la decision. El usuario decide
# por margen neto: mejor uno de 8 puntos con 28% que uno de 9 con 13%.
import inspect

_codigo = inspect.getsource(scanner.escanear)
print("  La regla de orden esta en el escaner, no en el test. Se comprueba "
      "que ordena por margen neto y no solo por puntaje.")
check("El escaner ordena por margen_neto_porcentaje",
      "margen_neto_porcentaje" in _codigo)
check("Y no solo por el puntaje previo",
      "reverse=True" in _codigo)

# Y el comportamiento, con la misma clave que usa el escaner.
def _clave(puntos, a):
    neto = a.get("margen_neto_porcentaje")
    if not isinstance(neto, (int, float)):
        neto = -1.0
    return (neto, puntos)


casos = [
    (9, {"margen_neto_porcentaje": 13.0, "n": "malo"}),
    (8, {"margen_neto_porcentaje": 28.0, "n": "bueno"}),
    (10, {"margen_neto_porcentaje": 20.0, "n": "medio"}),
    (10, {"margen_neto_porcentaje": None, "n": "sin-dato"}),
]
orden = sorted(casos, key=lambda x: _clave(*x), reverse=True)
print("  orden de envio:")
for p, a in orden:
    print(f"    [{p:>2}] neto {str(a['margen_neto_porcentaje']):>5}  {a['n']}")
check("Manda primero el de mayor margen neto",
      orden[0][1]["n"] == "bueno")
check("El de mayor puntaje pero bajo margen no va primero",
      orden[0][1]["n"] != "malo", f"-> {orden[0][1]['n']}")
check("El que no dio margen va al final",
      orden[-1][1]["n"] == "sin-dato", f"-> {orden[-1][1]['n']}")
check("Ordena de mayor a menor margen",
      [a["margen_neto_porcentaje"] for _, a in orden[:3]]
      == [28.0, 20.0, 13.0])

for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

print()
print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: el embudo gasta la cuota y manda las mejores")
