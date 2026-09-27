"""
test_e2e.py - Escaneo completo de punta a punta con Gemini SIMULADO.
Verifica que: se notifica, la ficha se renderiza, /etiqueta genera el PDF y
un fallo de Gemini SI se reporta (que era el bug original).
"""
import os
import sys
import time

os.environ["DRY_RUN"] = "0"
os.environ.setdefault("SAM_API_KEY", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")
os.environ["MAX_A_GEMINI"] = "8"
os.environ["MAX_NOTIFICACIONES"] = "3"
os.environ["MAX_DESCRIPCIONES"] = "12"
os.environ["PUNTAJE_MINIMO"] = "4"
os.environ["DIAS_DE_VENTANA"] = "3"
os.environ["DIAS_POR_CHUNK"] = "2"

import gemini_analyzer
import quota
import scanner
import store
import telegram_notify as tn

ENVIADOS = []


def enviar_falso(chat_id, texto, html_mode=True):
    ENVIADOS.append((chat_id, texto, html_mode))
    return True


tn.enviar = enviar_falso
scanner.telegram_notify.enviar = enviar_falso

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    print(f"  {'OK   ' if cond else 'FALLA'} {nombre} {extra}")
    if not cond:
        FALLA += 1


print("=" * 70)
print("1) ESCANEO CON GEMINI SIMULADO (debe notificar)")
print("=" * 70)

LLAMADAS = {"n": 0}

# Como decide la IA real: viable salvo que sea servicio, exija certificacion
# inaccesible o el destino sea el extranjero. Antes este mock solo aceptaba
# titulos con "PART" o "KIT", lo cual era una aproximacion tan pobre que el
# filtro mejorado de descalificadores empezo a promover oportunidades que el
# mock rechazaba.
DESCARTABLES = {
    "overhaul", "rebuild", "repair", "refurbish", "maintenance",
    "dd2345", "security clearance", "first article", "installation",
    "consulting", "training", "software",
}


def _viable(titulo: str, desc: str) -> tuple[bool, str]:
    texto = f"{titulo} {desc}".lower()
    for palabra in DESCARTABLES:
        if palabra in texto:
            return False, f"exige {palabra} o es servicio"
    return True, ""


def gemini_falso(opp, descripcion, lugar=""):
    LLAMADAS["n"] += 1
    titulo = opp.get("title", "")
    ok, motivo = _viable(titulo, descripcion)
    return {
        "notice_id": opp["noticeId"],
        "viable": ok,
        "motivo_descarte": motivo,
        "sin_descripcion": not descripcion.strip(),
        "producto": f"Producto de prueba para {titulo[:30]}",
        "cantidad_estimada": "500 EA",
        "lugar_entrega": lugar or "Portland / Oregon / UNITED STATES",
        "valor_contrato_usd": 120000.0,
        "costo_proveedor_usd": 78000.0,
        "precio_unitario_referencia_usd": 210.0,
        "precio_unitario_sugerido_usd": 268.0,
        "ganancia_neta_usd": 42000.0,
        "margen_porcentaje": 35.0,
        "precio_oferta_sugerido_usd": 110000.0,
        "estrategia_oferta": "Ofertar 8% bajo estimado con entrega en 45 dias.",
        "busquedas_distribuidores": ["wholesale parts usa", "industrial distributor"],
        "nivel_riesgo": "medio",
        "preguntas_criticas": ["¿Aceptan marcas equivalentes?"],
        "observaciones": "",
        "especificacion_tecnica_clave": "Norma ISO 9001, acero 316L",
        "naics": opp.get("naicsCode", ""),
        "psc": opp.get("classificationCode", ""),
        "solicitation": opp.get("solicitationNumber", "N/A"),
        "set_aside": "SBA",
        "limite": opp.get("responseDeadLine", "N/A"),
        "posted": opp.get("postedDate", "N/A"),
        "agencia": opp.get("fullParentPathName", "N/A"),
        "contacto": "Contacto Prueba | test@agency.gov",
        "ui_link": opp.get("uiLink", ""),
        "title": titulo,
    }


gemini_analyzer.analizar = gemini_falso
scanner.gemini_analyzer.analizar = gemini_falso

store.init_db()
store.limpiar()
# El presupuesto diario de Gemini vive en la misma base y persiste entre
# corridas. Sin esto, la segunda vez que se corre el test no analizaría nada.
quota.init()
quota.limpiar()
t0 = time.time()
r = scanner.escanear("123456", dias=3)
print(f"  Tiempo: {time.time() - t0:.1f}s")
for k in ("traidas", "candidatas", "puntuales", "analizadas", "viables", "notificadas"):
    print(f"  {k:12}: {r[k]}")
print(f"  errores     : {len(r['errores'])}")
print()

check("Trajo avisos de SAM.gov", r["traidas"] > 100, f"-> {r['traidas']}")
check("El filtro NAICS dejo candidatos", r["candidatas"] > 0, f"-> {r['candidatas']}")
check("Analizo con Gemini", r["analizadas"] > 0, f"-> {r['analizadas']}")
check("Respeta MAX_A_GEMINI", r["analizadas"] <= 8, f"-> {r['analizadas']}")
check("Notifico al menos una", r["notificadas"] >= 1, f"-> {r['notificadas']}")
check("Respeta MAX_NOTIFICACIONES (tope de 5-10 diarias)",
      r["notificadas"] <= 3, f"-> notificadas {r['notificadas']} de {r['viables']} viables")
check("Sin errores en el camino feliz", not r["errores"], f"-> {r['errores'][:1]}")
check("Guardo fichas para /etiqueta", len(r["analisis"]) == r["viables"])
print()

print("=" * 70)
print("2) CONTENIDO DE LA FICHA ENVIADA")
print("=" * 70)
if ENVIADOS:
    texto = ENVIADOS[0][1]
    for etiqueta_txt, esperado in (
        ("titulo", "Kyomoto"), ("valor", "$120,000.00"), ("costo", "$78,000.00"),
        ("ganancia", "$42,000.00"), ("margen", "35.0%"),
        ("precio unitario", "$268.00"), ("oferta", "$110,000.00"),
        ("destino", "Destino:"), ("contacto", "test@agency.gov"),
        ("enlace SAM", "https://sam.gov/"),
    ):
        check(f"Incluye {etiqueta_txt}", esperado in texto)
    check("No supera 4096 chars", len(texto) <= 4096, f"-> {len(texto)}")
    check("Usa modo HTML", ENVIADOS[0][2] is True)
    print(f"\n  --- Vista previa de la ficha ---\n")
    for linea in texto.split("\n")[:14]:
        print(f"  {linea}")
    print()
else:
    check("Se envio algun mensaje", False, "-> nada enviado")
print()

print("=" * 70)
print("3) /etiqueta GENERA EL PDF")
print("=" * 70)
import label
a = r["analisis"][0]
ruta = label.generar(a["notice_id"], a)
check("El PDF existe", bool(ruta) and os.path.exists(ruta), f"-> {ruta}")
if ruta and os.path.exists(ruta):
    check("Peso razonable", os.path.getsize(ruta) > 2048, f"-> {os.path.getsize(ruta)} bytes")
print()

print("=" * 70)
print("4) BUG ORIGINAL: UN FALLO DE GEMINI DEBE REPORTARSE")
print("=" * 70)
ENVIADOS.clear()
scanner.telegram_notify.enviar = enviar_falso


def gemini_roto(opp, descripcion, lugar=""):
    raise gemini_analyzer.GeminiError("401 UNAUTHENTICATED: la clave no es valida")


scanner.gemini_analyzer.analizar = gemini_roto
r2 = scanner.escanear("123456", dias=3)
check("El escaneo NO se cae", True)
check("Se acumulan los errores", len(r2["errores"]) > 0, f"-> {r2['errores']}")
check("El error menciona la clave", any("401" in e or "GEMINI" in e.upper() for e in r2["errores"]))
print(f"  errores detectados: {r2['errores'][:1]}")
scanner.redactar_fallo("123456", r2)
avisos = [t for _, t, _ in ENVIADOS if "problemas" in t or "GEMINI" in t.upper()]
check("Kyomoto AVISA el fallo en Telegram", bool(avisos))
if avisos:
    print(f"\n  --- Aviso de error ---\n")
    for linea in avisos[0].split("\n")[:10]:
        print(f"  {linea}")
print()

print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: pipeline completo verificado")
