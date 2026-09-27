"""
test_real.py - Kyomoto contra SAM.gov y Gemini REALES. Sin simulaciones.
Es la prueba que decide si el bot sirve.

Las claves se leen del entorno, nunca de este archivo:
    $env:SAM_API_KEY="SAM-..."
    $env:GEMINI_API_KEY="AQ...."
    python test_real.py
"""
import os
import sys
import time

faltantes = [
    v for v in ("SAM_API_KEY", "GEMINI_API_KEY")
    if not os.environ.get(v)
]
if faltantes:
    print("Faltan variables de entorno: " + ", ".join(faltantes))
    print("Ejemplo:")
    print('  $env:SAM_API_KEY="SAM-..."')
    print('  $env:GEMINI_API_KEY="AQ...."')
    sys.exit(2)

os.environ["GEMINI_MODEL"] = "gemini-3.8-flash"
os.environ["DRY_RUN"] = "1"
os.environ["DIAS_DE_VENTANA"] = "2"
os.environ["DIAS_POR_CHUNK"] = "2"
os.environ["MAX_A_GEMINI"] = "4"
os.environ["MAX_DESCRIPCIONES"] = "10"
os.environ["PUNTAJE_MINIMO"] = "6"
os.environ["GEMINI_WORKERS"] = "1"
os.environ["GEMINI_PAUSA_SEG"] = "6"

import gemini_analyzer
import scanner
import store
import telegram_notify as tn

print("=" * 70)
print("1) AUTODIAGNOSTICO")
print("=" * 70)
print(f"  SAM.gov: {__import__('sam_api').verificar_api()}")
print(f"  Gemini : {gemini_analyzer.verificar_api()}")
print()

print("=" * 70)
print("2) ANALISIS REAL DE UNA OPORTUNIDAD DE SAM.GOV")
print("=" * 70)
candidatas, total, _ = scanner._traer_candidatos(2)
print(f"  Avisos en la API: {total} | candidatos a producto: {len(candidatas)}")
prov = []
for o in candidatas:
    p, _ = __import__("filters").puntuar(o, "")
    if p >= 6:
        prov.append((p, o))
prov.sort(key=lambda x: x[0], reverse=True)
objetivo = [o for _, o in prov[:4]]
desc = scanner._bajar_descripciones(objetivo)
print(f"  Descripciones con texto: {sum(1 for v in desc.values() if v.strip())}/{len(desc)}")
print()

import filters
for o in objetivo:
    d = desc.get(o["noticeId"], "")
    lugar = filters.lugar_de_entrega(o)
    t0 = time.time()
    try:
        a = gemini_analyzer.analizar(o, d, lugar)
    except Exception as e:
        print(f"  FALLO: {type(e).__name__}: {str(e)[:200]}")
        continue
    dt = time.time() - t0
    print(f"  {'-' * 66}")
    print(f"  [{'VIABLE' if a['viable'] else 'descartada'}] {a['title'][:62]}")
    print(f"    {dt:.1f}s | NAICS {a['naics']} | destino: {a['lugar_entrega'][:40]}")
    if a["viable"]:
        print(f"    producto : {a['producto'][:80]}")
        print(f"    cantidad : {a['cantidad_estimada']}")
        print(f"    valor    : ${a['valor_contrato_usd']}  |  costo: ${a['costo_proveedor_usd']}")
        print(f"    ganancia : ${a['ganancia_neta_usd']}  ({a['margen_porcentaje']}%)")
        print(f"    unitario : mercado ${a['precio_unitario_referencia_usd']} -> ofertar ${a['precio_unitario_sugerido_usd']}")
        print(f"    oferta   : ${a['precio_oferta_sugerido_usd']}  riesgo {a['nivel_riesgo']}")
        print(f"    buscar   : {a['busquedas_distribuidores'][:3]}")
        print(f"    sin desc : {a['sin_descripcion']}")
    else:
        print(f"    motivo   : {a['motivo_descarte'][:90]}")
print()

print("=" * 70)
print("3) ESCANEO COMPLETO (con DRY_RUN: no envia)")
print("=" * 70)
store.init_db()
store.limpiar()
t0 = time.time()
r = scanner.escanear("1", dias=2)
print(f"  Tiempo total: {time.time() - t0:.0f}s")
for k in ("traidas", "candidatas", "puntuales", "analizadas", "viables"):
    print(f"  {k:12}: {r[k]}")
print(f"  errores     : {r['errores'] or 'ninguno'}")
print()

print("=" * 70)
if r["analizadas"] and not r["errores"]:
    print(f"RESULTADO: {r['viables']} oportunidades viables de {r['analizadas']} analizadas")
    print("Kyomoto funciona con las dos APIs reales.")
    sys.exit(0)
print("RESULTADO: hay problemas")
sys.exit(1)
