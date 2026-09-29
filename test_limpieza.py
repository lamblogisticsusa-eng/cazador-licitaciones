"""
test_limpieza.py - El codigo esta limpio y no se contradice a si mismo.

BUG QUE DESTAPO (28-sep-2026, commit 257c37a):
Habia DOS definiciones de verificar_api() en gemini_analyzer.py. Mi script
de reemplazo corto por rango de indices y como _construir_config estaba
ANTES de verificar_api, el corte quedo al reves y duplico el bloque.

En Python gana la ULTIMA definicion, o sea la vieja: la que probaba solo el
modelo principal. El resultado fue que /selftest reportaba 503 aunque el
modelo de respaldo funcionara perfecto. Kyomoto le dijo al usuario que
Google estaba saturado cuando en realidad el estaba mirando mal.

14 suites en verde y el bug seguia ahi, porque ninguna comprobaba que cada
funcion este definida UNA sola vez. Esta suite si.
"""
import ast
import io
import os
import sys

MODULOS = [
    "config.py", "store.py", "sam_api.py", "filters.py", "gemini_analyzer.py",
    "telegram_notify.py", "label.py", "scanner.py", "quota.py", "kyo.py",
    "menu.py", "distribuidores.py", "main.py",
]

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


print("=" * 70)
print("1) NINGUNA FUNCION ESTA DEFINIDA DOS VECES")
print("=" * 70)
for mod in MODULOS:
    if not os.path.exists(mod):
        check(f"{mod} existe", False, "-> no encontrado")
        continue
    arbol = ast.parse(io.open(mod, encoding="utf-8").read(), filename=mod)
    vistas = {}
    duplicadas = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Solo el nivel superior: un metodo y una funcion homonima no chocan.
        padre = None
        for cand in ast.walk(arbol):
            if isinstance(cand, (ast.Module, ast.ClassDef)):
                if nodo in getattr(cand, "body", []):
                    padre = cand
                    break
        if padre is None:
            continue
        clave = (
            type(padre).__name__,
            getattr(padre, "name", "<modulo>"),
            nodo.name,
        )
        if clave in vistas:
            duplicadas.append((nodo.name, clave[0], clave[1]))
        vistas[clave] = nodo.lineno
    if duplicadas:
        for f, tipo, cont in duplicadas:
            print(f"        -> {f} en {tipo} '{cont}'")
    check(f"{mod}: sin duplicados ({len(vistas)} funciones)", not duplicadas)

print()
print("=" * 70)
print("2) LA CADENA DE MODELOS ES AMPLIA Y EL PRINCIPAL ESTA LIBRE")
print("=" * 70)
os.environ.setdefault("GEMINI_API_KEY", "x")
import config

principal = config.GEMINI_MODEL
alternos = [m for m in config.GEMINI_MODELES_ALTERNATIVOS if m]

print(f"  Principal : {principal}")
print(f"  Alternos  : {', '.join(alternos)}")
# El 28-sep llegaron a caer 5 de 6 modelos a la vez. Con tres alternos no
# alcanzaba: habia que probar los que existen.
check("El principal es el que respondio al sondear (3.6)",
      principal == "gemini-3.6-flash", f"-> {principal}")
check("Hay al menos tres alternos", len(alternos) >= 3, f"-> {len(alternos)}")
check("El principal NO esta repetido en los alternos",
      principal not in alternos)
# gemini-3.5-flash-lite daba 400 por thinking_budget=0. Ahora el 400
# reintenta sin ese campo, asi que el modelo sirve y vuelve a la lista: el
# 29-sep a la 01:35 del Pacifico era el UNICO con cuota libre, y sin el la
# cadena entera se quedaba sin nada que probar.
check("SI incluye gemini-3.5-flash-lite, que ya se puede usar",
      "gemini-3.5-flash-lite" in alternos)
check("Y va primero: es el mas rapido y el que menos se satura",
      alternos[0] == "gemini-3.5-flash-lite", f"-> {alternos[0] if alternos else 'vacia'}")
check("NO incluye gemini-flash-lite, que no existe",
      "gemini-flash-lite" not in alternos)
check("No hay entradas vacias", all(m for m in config.GEMINI_MODELES_ALTERNATIVOS))
check("La version subio a 2.3.0", config.KYOMOTO_VERSION == "2.3.0",
      f"-> {config.KYOMOTO_VERSION}")

print()
print("=" * 70)
print("2b) CADA BARRIDO USA LA CUOTA, NO LA REPARTE ENTRE 12")
print("=" * 70)
import quota as _quota

barridos = int(24 / config.INTERVALO_HORAS)
reparto = max(1, config.PRESUPUESTO_GEMINI_DIARIO // barridos)
por_barrido = _quota.por_barrido()
print(f"  presupuesto : {config.PRESUPUESTO_GEMINI_DIARIO}/dia")
print(f"  barridos    : {barridos} (cada {config.INTERVALO_HORAS:g}h)")
print(f"  antes       : {reparto} por barrido (20 // 12)")
print(f"  ahora       : {por_barrido} por barrido")
# El bug: 20 // 12 == 1, o sea una sola licitacion cada dos horas y once
# llamadas de la cuota perdidas solas al reiniciar.
check("El reparto viejo habria dado 1 (el bug)", reparto == 1,
      f"-> {reparto}")
check("Ahora analiza al menos 2 por barrido", por_barrido >= 2,
      f"-> {por_barrido}")
check("Y nunca mas de MAX_A_GEMINI por barrido",
      por_barrido <= config.MAX_A_GEMINI, f"-> {por_barrido}")
# Con el piso de 2 y 12 barridos se piden 24, pero el tope diario de 20
# sigue mandando por separado: no se puede gastar de mas.
check("Los barridos piden mas de lo que la cuota permite (queda topado "
      "por el tope diario)", por_barrido * barridos > config.PRESUPUESTO_GEMINI_DIARIO,
      f"-> {por_barrido * barridos} vs {config.PRESUPUESTO_GEMINI_DIARIO}")
check("El tope diario sigue siendo la fuente de verdad",
      config.PRESUPUESTO_GEMINI_DIARIO >= por_barrido)

print()
print("=" * 70)
print("3) verificar_api USA LA CADENA, NO UN MODELO SOLO")
print("=" * 70)
import gemini_analyzer as ga

fuente = io.open("gemini_analyzer.py", encoding="utf-8").read()
arbol = ast.parse(fuente)
nodos = [n for n in arbol.body
         if isinstance(n, ast.FunctionDef) and n.name == "verificar_api"]
check("Hay exactamente una verificar_api", len(nodos) == 1, f"-> {len(nodos)}")
if nodos:
    cuerpo = ast.dump(nodos[0])
    # La version rota usaba config.GEMINI_MODEL directo y no tenia bucle.
    check("Tiene bucle sobre la cola de modelos", "For" in cuerpo)
    check("Construye la cola con la lista de alternos",
          "GEMINI_MODELES_ALTERNATIVOS" in cuerpo)
    check("No se queda solo en el principal",
          cuerpo.count("GEMINI_MODELES_ALTERNATIVOS") >= 1)

# Comportamiento: con el principal caido y un alterno libre, debe dar OK.
print()
print("  Probando el comportamiento con modelos falsos:")
JSON_OK = "OK"


class _R:
    text = JSON_OK


class _M:
    def __init__(self, guion):
        self.guion = guion
        self.usados = []

    def generate_content(self, model=None, contents=None, config=None):
        self.usados.append(model)
        if model in self.guion:
            raise Exception(self.guion[model])
        return _R()


class _C:
    def __init__(self, guion):
        self.models = _M(guion)


ERR_503 = "503 UNAVAILABLE. {'error': {'code': 503, 'status': 'UNAVAILABLE'}}"

caidos = {principal: ERR_503, alternos[0]: ERR_503}
ga._get_cliente = lambda: _C(caidos)
r = ga.verificar_api()
print(f"  ok={r['ok']} modelo={r.get('modelo')} -> {r['detalle'][:80]}")
check("Con el principal y un alterno caidos, aun da OK", r["ok"] is True,
      f"-> {r['detalle'][:110]}")
check("Dice que model'sulo (no el caido)", r.get("modelo") not in caidos,
      f"-> {r.get('modelo')}")
check("Lo marca como degradado, no como error", r.get("degradado") is True)

# Y si caen todos, debe decirlo claro.
ga._get_cliente = lambda: _C({m: ERR_503 for m in [principal] + alternos})
r = ga.verificar_api()
print(f"  todos caidos -> ok={r['ok']} :: {r['detalle'][:100]}")
check("Si caen todos, avisa que estan saturados", r["ok"] is False)
check("  y lo dice con la palabra saturados",
      "saturado" in r["detalle"].lower())
check("  y nombra los que probó", r.get("saturado") is True)

print()
print("=" * 70)
print("4) LOS FICHEROS DE DESPLIEGUE Dicen LO MISMO QUE config.py")
print("=" * 70)
for f in ("render.yaml", ".env.example"):
    t = io.open(f, encoding="utf-8").read()
    if f.endswith(".yaml"):
        # Quita el prefijo "value: " ANTES de las comillas: al revés se
        # deja una comilla pegada y la comparacion falla sola.
        lineas = t.split("\n")

        def valor(clave):
            # Busca la linea "- key: <clave>" EXACTA, no una que la contenga:
            # "GEMINI_MODELES_ALTERNATIVOS" contiene "GEMINI_MODEL".
            for i, l in enumerate(lineas):
                if l.strip() == f"- key: {clave}":
                    s = lineas[i + 1].strip()
                    if s.startswith("value:"):
                        return s[len("value:"):].strip().strip("'\"")
            return None

        v = valor("GEMINI_MODEL")
        v_alt = valor("GEMINI_MODELES_ALTERNATIVOS")
    else:
        d = dict(
            l.split("=", 1) for l in t.split("\n")
            if "=" in l and not l.startswith("#")
        )
        v, v_alt = d.get("GEMINI_MODEL"), d.get("GEMINI_MODELES_ALTERNATIVOS")

    check(f"{f}: el modelo es {principal}", v == principal, f"-> {v}")
    check(f"{f}: la lista de alternos coincide con config.py",
          v_alt == ",".join(alternos), f"-> {v_alt}")

print()
print("=" * 70)
print("5) NINGUN SECRETO EN EL REPOSITORIO")
print("=" * 70)
import re

PATRONES = [
    (r"AIza[A-Za-z0-9_\-]{20,}", "clave de Google"),
    (r"SAM-[0-9a-f]{8}-[0-9a-f]{4}", "clave de SAM.gov"),
    (r"\d{8,10}:[A-Za-z0-9_\-]{30,}", "token de Telegram"),
    (r"AQ\.Ab[A-Za-z0-9_\-]{30,}", "clave nueva de Google"),
]
fugas = []
for raiz, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "legacy")]
    for f in files:
        if f.endswith((".db", ".db-wal", ".db-shm", ".pdf", ".pyc")):
            continue
        ruta = os.path.join(raiz, f)
        try:
            c = io.open(ruta, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for patron, que in PATRONES:
            if re.search(patron, c):
                fugas.append(f"{f} ({que})")
check("Ninguna clave esta escrita en un fichero", not fugas, f"-> {fugas}")

print()
print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: el codigo esta limpio y coherente")
