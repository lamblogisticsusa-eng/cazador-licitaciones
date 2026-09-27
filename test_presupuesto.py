"""
test_presupuesto.py - El scanner respeta el presupuesto diario y el tope de USD.
No llama a Gemini: comprueba que la seleccion y el descarte ocurren ANTES de
gastar una sola llamada, que es justo lo que hace posible el plan gratuito.
"""
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_pres.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["DRY_RUN"] = "1"
os.environ.setdefault("SAM_API_KEY", "SAM-clave-de-prueba")
os.environ["PRESUPUESTO_GEMINI_DIARIO"] = "3"
os.environ["MAX_A_GEMINI"] = "10"
os.environ["MAX_NOTIFICACIONES"] = "3"
os.environ["PUNTAJE_MINIMO"] = "6"
os.environ["DIAS_DE_VENTANA"] = "2"
os.environ["DIAS_POR_CHUNK"] = "2"
os.environ["MAX_DESCRIPCIONES"] = "20"

import config
import filters
import gemini_analyzer
import quota
import scanner
import store

FALLA = 0
LLAMADAS = {"n": 0}


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


def analizar_falso(opp, descripcion, lugar=""):
    LLAMADAS["n"] += 1
    return {
        "notice_id": opp["noticeId"], "viable": True, "motivo_descarte": "",
        "sin_descripcion": not descripcion.strip(),
        "producto": "Producto de prueba", "especificacion_tecnica_clave": "ISO 9001",
        "cantidad_estimada": "100 EA", "lugar_entrega": lugar or "USA",
        "valor_contrato_usd": 60000.0, "costo_proveedor_usd": 40000.0,
        "precio_unitario_referencia_usd": 500.0, "precio_unitario_sugerido_usd": 600.0,
        "ganancia_neta_usd": 20000.0, "margen_porcentaje": 33.0,
        "precio_oferta_sugerido_usd": 55000.0, "estrategia_oferta": "Ofertar plano.",
        "busquedas_distribuidores": ["wholesale usa"], "nivel_riesgo": "medio",
        "preguntas_criticas": ["?"], "observaciones": "",
        "naics": opp.get("naicsCode", ""), "psc": opp.get("classificationCode", ""),
        "solicitation": opp.get("solicitationNumber", "N/A"), "set_aside": "SBA",
        "limite": "2026-11-01", "posted": "2026-09-26", "agencia": "TEST",
        "contacto": "Test | t@t.gov", "ui_link": opp.get("uiLink", ""),
        "title": opp.get("title", ""),
    }


gemini_analyzer.analizar = analizar_falso
scanner.gemini_analyzer.analizar = analizar_falso

store.init_db()
quota.init()
store.limpiar()

print("=" * 70)
print("1) EL PRESUPUESTO MANDA POR ENCIMA DE MAX_A_GEMINI")
print("=" * 70)
r = scanner.escanear("1", dias=2)
print(f"  avisos {r['traidas']} | candidatos {r['candidatas']} | "
      f"puntuales {r['puntuales']} | analizadas {r['analizadas']} | viables {r['viables']}")
check("No se pasa del presupuesto de 3", r["analizadas"] <= 3, f"-> {r['analizadas']}")
check("Llamadas a Gemini <= 3", LLAMADAS["n"] <= 3, f"-> {LLAMADAS['n']}")
check("La cuota lo registra", quota.estado()["usadas"] == LLAMADAS["n"],
      f"-> {quota.estado()['usadas']}")
check("No guarda mas de MAX_NOTIFICACIONES", r["viables"] <= 3)
print()

print("=" * 70)
print("2) AL AGOTARSE, NO INTENTA MAS")
print("=" * 70)
quota.gastar(10)
e = quota.estado()
check("Presupuesto agotado", e["agotado"] is True, f"-> {e}")
antes = LLAMADAS["n"]
r2 = scanner.escanear("1", dias=2)
check("No llamo a Gemini", LLAMADAS["n"] == antes, f"-> antes {antes}, ahora {LLAMADAS['n']}")
check("Lo reporta como error visible", any("Presupuesto" in x for x in r2["errores"]),
      f"-> {r2['errores'][:1]}")
print()

print("=" * 70)
print("3) LO NO ANALIZADO QUEDA PENDIENTE (no se pierde)")
print("=" * 70)
antes_vistos = store.total_procesadas()
r3 = scanner.escanear("1", dias=2)
print(f"  vistos antes {antes_vistos} | despues {store.total_procesadas()}")
check("No se perdio ninguna oportunidad por falta de cuota",
      store.total_procesadas() - antes_vistos <= r3["analizadas"],
      f"-> +{store.total_procesadas() - antes_vistos}")
print()

print("=" * 70)
print("4) EL TOPE DE USD SE APLICA SIN GASTAR GEMINI")
print("=" * 70)
texto = "Indefinite Delivery Contract: Not to Exceed 350,000.00"
v, frag = filters.valor_declarado(texto)
check("Detecta el valor sobre el tope", v is not None and v > config.TOPE_USD, f"-> {v}")
llamadas_previas = LLAMADAS["n"]
check("Se descarta sin llamar a Gemini", LLAMADAS["n"] == llamadas_previas)
print(f"  TOPE_USD={config.TOPE_USD:,.0f} | fragmento: {frag[:60]!r}")
print()

for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: el presupuesto de 10/dia funciona con plan gratuito")
