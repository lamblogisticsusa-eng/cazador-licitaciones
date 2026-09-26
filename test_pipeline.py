"""
test_pipeline.py - Prueba el pipeline real contra SAM.gov sin llamar a Gemini.
Sirve para verificar que la busqueda y el filtro funcionan antes de desplegar.
"""
import logging
import os
import sys
import time

os.environ.setdefault("DRY_RUN", "1")
# La clave se lee del entorno. Este archivo jamas lleva credenciales dentro.
if not os.environ.get("SAM_API_KEY"):
    print("Falta la variable de entorno SAM_API_KEY. No se ejecuta la prueba.")
    sys.exit(2)

logging.basicConfig(level=logging.INFO, format="%(message)s")

import config
import filters
import gemini_analyzer
import sam_api
import scanner
import store

DIAS = int(sys.argv[1]) if len(sys.argv) > 1 else 7

print("=" * 70)
print("1) AUTODIAGNOSTICO")
print("=" * 70)
print(f"  SAM.gov : {sam_api.verificar_api()}")
g = gemini_analyzer.verificar_api()
print(f"  Gemini  : {g['ok']} -> {g['detalle'][:150]}")
print()

print("=" * 70)
print(f"2) BUSQUEDA EN SAM.GOV ({DIAS} dias)")
print("=" * 70)
t0 = time.time()
vistos = store.ids_procesados()
crudos, total, ya = scanner._traer_candidatos(DIAS)
print(f"  Tiempo            : {time.time() - t0:.1f}s")
print(f"  Avisos en la API  : {total}")
print(f"  Ya notificados    : {ya}")
print(f"  Candidato a bien  : {len(crudos)}  (el NAICS veto descarto el resto)")
print()

print("=" * 70)
print(f"3) PRE-PUNTAJE POR TITULO (gratis) y BAJADA DE DESCRIPCIONES")
print("=" * 70)
provisionales = []
for opp in crudos:
    p, _ = filters.puntuar(opp, "")
    if p >= config.PUNTAJE_MINIMO:
        provisionales.append((p, opp))
provisionales.sort(key=lambda x: x[0], reverse=True)
objetivo = [o for _, o in provisionales[: config.MAX_DESCRIPCIONES]]
print(f"  Sobre el piso por titulo: {len(provisionales)}")
print(f"  Descripciones a bajar   : {len(objetivo)} (de {len(crudos)} candidatos)")
t0 = time.time()
desc = scanner._bajar_descripciones(objetivo)
ok = sum(1 for v in desc.values() if v.strip())
print(f"  Tiempo                  : {time.time() - t0:.1f}s")
print(f"  Con texto real          : {ok}/{len(desc)}")
print("  (el codigo anterior nunca hacia esta llamada: recibia la URL)")
print()

print("=" * 70)
print("4) PUNTAJE FINAL + DEDUPLICACION POR TITULO")
print("=" * 70)
puntuadas = []
for opp in objetivo:
    d = desc.get(opp["noticeId"], "")
    p, m = filters.puntuar(opp, d)
    if p >= config.PUNTAJE_MINIMO:
        puntuadas.append((p, opp, d, m))
puntuadas.sort(key=lambda x: x[0], reverse=True)

vistos_titulo, sin_repetir, repetidas = set(), [], 0
for item in puntuadas:
    clave = filters.clave_titulo(item[1].get("title", ""))
    if clave and clave in vistos_titulo:
        repetidas += 1
        continue
    vistos_titulo.add(clave)
    sin_repetir.append(item)
print(f"  Sobre el piso            : {len(puntuadas)}")
print(f"  Duplicados por titulo    : {repetidas}")
print(f"  Únicos                   : {len(sin_repetir)}")
print()
for p, o, d, m in sin_repetir[:10]:
    print(f"  [{p:>3}] {o.get('title', '')[:74]}")
    print(f"        NAICS {o.get('naicsCode')} | PSC {o.get('classificationCode')} "
          f"| limite en {sam_api.dias_restantes(o)}d | desc: {'si' if d.strip() else 'NO'}")
    print(f"        -> {'; '.join(m[:4])}")
    print()

print("=" * 70)
print("5) RESUMEN")
print("=" * 70)
print(f"  Total avisos en {DIAS} dias        : {total}")
print(f"  Candidatos tras veto NAICS         : {len(crudos)}")
print(f"  Con descripcion descargada         : {ok}")
print(f"  Únicos sobre el piso               : {len(sin_repetir)}")
print(f"  Llegan a Gemini por barrido        : {min(len(sin_repetir), config.MAX_A_GEMINI)}")
print()
print("  Ajusta MAX_A_GEMINI y PUNTAJE_MINIMO en las variables de Render.")
