# -*- coding: utf-8 -*-
"""
test_fase3.py - Fase 3: geo-matching, escenarios de margen y Award Tracker.

QUE SE COMPRUEBA

1) GEO-MATCHING (distribuidores.py): el Ship-To se lee en los DOS formatos
   que llegan al modulo ("/" del barrido, "," del aviso en vivo), se
   clasifica en la region del Censo y con el se ORDENA por cercania. Nunca
   filtra: con destino desconocido el orden de sitios y de candidatos es
   IDENTICO al de antes de la Fase 3.
2) ESCENARIOS DE MARGEN (sensibilidad.py): 100% / 97% / 95% del
   presupuesto, con el factoring dinamico descontado del PRECIO DE CADA
   ESCENARIO y el margen medido contra el precio ofertado (no contra el
   presupuesto, que infla el margen). El bloque sale en la ficha de
   Telegram, despues del resumen financiero.
3) AWARD TRACKER (kyomoto_main.py): solo se celebra un premio cuyo UEI es
   el de la entidad (o que no trae UEI y por tanto no se puede descartar),
   se enlaza el Notice of Award cuando el registro lo trae, y la alerta
   anade prioridad y origen del UEI SIN tocar el formato original.
4) Retrocompatibilidad: firmas, claves de retorno y el contrato visual de
   la ficha siguen intactos.

Este test NO toca la red: todo va con datos falsos y monkeypatch.
"""
import asyncio
import inspect
import os
import re
import sys
import tempfile

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print("  OK    %s" % nombre)
    else:
        FALLA += 1
        print("  FALLA %s %s" % (nombre, extra))


DB = os.path.join(tempfile.gettempdir(), "kyo_test_fase3.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "x"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"

import config
import distribuidores
import sensibilidad
import telegram_notify as tn
import kyomoto_main as km


# =====================================================================
# 1) GEO: EL SHIP-TO SE LEE EN LOS DOS FORMATOS, SIN INVENTAR
# =====================================================================
print("=" * 76)
print("1) GEO: EXTRAER EL DESTINO DEL SHIP-TO")
print("=" * 76)

print("  Los dos formatos que llegan al modulo:")
for etiqueta, lugar, ciudad, estado, region in (
        ("con '/' del barrido", "Philadelphia / Pennsylvania / UNITED STATES",
         "Philadelphia", "Pennsylvania", "Northeast"),
        ("con ',' del aviso vivo", "Philadelphia, Pennsylvania, UNITED STATES",
         "Philadelphia", "Pennsylvania", "Northeast"),
        ("estado solo, por codigo", "Norfolk / VA / USA",
         "Norfolk", "VA", "South"),
        ("ZIP pegado al estado", "Rock Island, Illinois 61299, UNITED STATES",
         "Rock Island", "Illinois", "Midwest"),
        ("instalacion delante", "Naval Station Norfolk, Virginia, UNITED STATES",
         "Naval Station Norfolk", "Virginia", "South"),
        ("solo el estado", "California", "", "California", "West"),
        ("ciudad sin estado", "Norfolk, UNITED STATES",
         "Norfolk", "", ""),
        ("solo el pais", "UNITED STATES", "", "", ""),
        ("marcador de sin dato", "No especificado", "", "", ""),
        ("vacio", "", "", "", "")):
    got = distribuidores.destino_de(lugar)
    check("%-24s -> %s, %s, %s" % (etiqueta, ciudad or "-", estado or "-",
                                   region or "-"),
          (got["ciudad"], got["estado"], got["region"])
          == (ciudad, estado, region), "-> %r" % got)

print()
print("  Un dict de direccion (la forma de pdf_generator):")
got = distribuidores.destino_de({"ciudad": "Groton", "estado": "Connecticut"})
check("Lee ciudad y estado del dict",
      (got["ciudad"], got["estado"], got["region"])
      == ("Groton", "Connecticut", "Northeast"), "-> %r" % got)

print()
print("  Y lo que NUNCA hace:")
got = distribuidores.destino_de("{'city': 'Norfolk'}")
check("Un repr de dict no se cuela", got == {"ciudad": "", "estado": "",
                                             "region": ""}, "-> %r" % got)
got = distribuidores.destino_de("Atlantis, Wonderland, UNITED STATES")
check("Un estado que no existe no se fuerza", got["estado"] == ""
      and got["region"] == "", "-> %r" % got)

print()
print("  Regiones del Censo:")
for estado, region in (("Illinois", "Midwest"), ("PA", "Northeast"),
                       ("Georgia", "South"), ("California", "West"),
                       ("Texas", "South"), ("NY", "Northeast")):
    check("  %-12s -> %-9s" % (estado, region),
          distribuidores.region_de_estado(estado) == region,
          "-> %r" % distribuidores.region_de_estado(estado))
check("Un estado inventado no tiene region",
      distribuidores.region_de_estado("Narnia") == "")
check("Vacio no tiene region", distribuidores.region_de_estado("") == "")


# =====================================================================
# 2) GEO: EL ORDEN LO PONE LA REGION DEL DESTINO
# =====================================================================
print()
print("=" * 76)
print("2) GEO: PRIORIZACION POR PROXIMIDAD AL SHIP-TO")
print("=" * 76)

# Cuatro candidatos, con uno SIN base conocida ("Acme Local"): es el que
# comprueba que el geo-matching no hunde a los desconocidos hasta el fondo.
# Se mandan cuatro para que, al recortar a tres, el que sobrevive dependa
# del destino y se pueda ver el orden cambiar de verdad.
CAND = [{"nombre": "Grainger", "tipo": "mayorista industrial",
         "porque": "Alternativa.", "verificar": "Grainger bearings"},
        {"nombre": "Acme Local", "tipo": "distribuidor local",
         "porque": "Alternativa.", "verificar": "Acme bearings"},
        {"nombre": "Radwell", "tipo": "mayorista MRO",
         "porque": "Alternativa.", "verificar": "Radwell bearings"},
        {"nombre": "Platt", "tipo": "mayorista industrial",
         "porque": "Alternativa.", "verificar": "Platt bearings"}]

ORIGEN_SITIOS = [n for n, _, _ in distribuidores.DIRECTORIOS[:5]]

print("  Con destino DESCONOCIDO el orden es el de siempre:")
sin = distribuidores.para_oportunidad(["bearing"], "S-1", "No especificado",
                                      "", CAND)
check("sitios: el orden original, intacto",
      [s[0] for s in sin["sitios"]] == ORIGEN_SITIOS,
      "-> %s" % [s[0] for s in sin["sitios"]])
check("candidatos: el orden de Gemini, intacto (los 3 primeros)",
      [c["nombre"] for c in sin["candidatos"]]
      == ["Grainger", "Acme Local", "Radwell"],
      "-> %s" % [c["nombre"] for c in sin["candidatos"]])
check("La bandera dice que NO se ordeno",
      sin["ordenado_por_cercania"] is False, "-> %r" % sin)
check("El destino sale con las tres claves vacias",
      sin["destino"] == {"ciudad": "", "estado": "", "region": ""},
      "-> %r" % sin["destino"])

print()
print("  Con destino en el MIDWEST (Illinois):")
mw = distribuidores.para_oportunidad(["bearing"], "S-1",
                                     "Chicago / Illinois / UNITED STATES",
                                     "", CAND)
sitios_mw = [s[0] for s in mw["sitios"]]
cand_mw = [(c["nombre"], c["region_base"]) for c in mw["candidatos"]]
print("     sitios    :", sitios_mw)
print("     candidatos:", cand_mw)
check("El destino se analiza entero",
      mw["destino"] == {"ciudad": "Chicago", "estado": "Illinois",
                        "region": "Midwest"}, "-> %r" % mw["destino"])
check("La bandera dice que si se ordeno",
      mw["ordenado_por_cercania"] is True)
check("Zoro (base Midwest) entra en los cinco",
      "Zoro" in sitios_mw, "-> %s" % sitios_mw)
check("Grainger (base Midwest) sube el primero",
      cand_mw[0] == ("Grainger", "Midwest"), "-> %s" % cand_mw)
check("Radwell (base Northeast) baja de Grainger",
      cand_mw.index(("Radwell", "Northeast"))
      > cand_mw.index(("Grainger", "Midwest")), "-> %s" % cand_mw)
check("El desconocido NO se hunde: conserva el medio",
      cand_mw[1][0] == "Acme Local", "-> %s" % cand_mw)

print()
print("  Con destino en el OESTE (California):")
we = distribuidores.para_oportunidad(["bearing"], "S-1",
                                     "Los Angeles / California / USA",
                                     "", CAND)
sitios_we = [s[0] for s in we["sitios"]]
print("     sitios    :", sitios_we)
check("Platt (base West) entra en los cinco",
      "Platt" in sitios_we, "-> %s" % sitios_we)
check("Platt es el primero de la lista",
      sitios_we[0] == "Platt", "-> %s" % sitios_we)
check("Platt sube el primero entre los candidatos",
      we["candidatos"][0]["nombre"] == "Platt",
      "-> %s" % [c["nombre"] for c in we["candidatos"]])

print()
print("  Y nadie se DESCARTA por region (el geo-matching ordena, no filtra):")
todos = distribuidores.para_oportunidad(
    ["bearing"], "S-1", "Atlanta / Georgia / USA", "", CAND)
check("Los sitios siguen siendo 5", len(todos["sitios"]) == 5,
      "-> %d" % len(todos["sitios"]))
check("Los candidatos siguen siendo 3", len(todos["candidatos"]) == 3,
      "-> %d" % len(todos["candidatos"]))
check("El nombre de la region de cada candidato queda marcado",
      all("region_base" in c for c in todos["candidatos"]))

print()
print("  La tercera consulta de Google lleva ciudad Y estado:")
r = distribuidores.para_oportunidad(["bearing"], "S-1",
                                    "Chicago / Illinois / UNITED STATES",
                                    "", [])
check("Aparece la consulta localizada",
      any("Chicago, Illinois" in c for c in r["consultas"]),
      "-> %s" % r["consultas"])


# =====================================================================
# 3) GEO: LA FICHA DICE DE DONDE SALE EL ORDEN
# =====================================================================
print()
print("=" * 76)
print("3) GEO: EL SHIP-TO SE VE EN LA FICHA DE TELEGRAM")
print("=" * 76)

FICHA = {
    "notice_id": "f3-1234567890",
    "solicitation": "W9127N26QA145",
    "title": "Kits de sellos mecanicos para bomba",
    "agencia": "Department of the Air Force",
    "set_aside": "Total Small Business",
    "limite": "2026-12-15T17:00:00-05:00",
    "producto": "Kits de sellos mecanicos para bomba",
    "modelo_especifico": "NSN 5330-01-586-4931",
    "unidad_medida": "KIT",
    "cantidad_total": 50,
    "especificacion_tecnica_clave": "EPDM 70 Shore A, ASTM D2000",
    "lugar_entrega": "Chicago / Illinois / UNITED STATES",
    "valor_contrato_usd": 85000,
    "costo_total_usd": 52000,
    # Mismos numeros que test_formato: presupuesto 85.000, costo 52.000 y
    # oferta 74.500 -> bruta 22.500, factoring 2.607,50 y neta 19.892,50
    # (26,7%). La ficha entera cuadra consigo misma y con el resto de la
    # suite, en vez de mezclar dos ejemplos distintos.
    "ganancia_neta_usd": 19892.50,
    "margen_neto_porcentaje": 26.7,
    "precio_oferta_sugerido_usd": 74500,
    "razonamiento_oferta": "Nos quedamos 12% bajo el tope del gobierno",
    "query_google_proveedores": "EPDM seal kit wholesale distributor usa",
    "busquedas_distribuidores": ["EPDM seal kit wholesale distributor"],
    "distribuidores_candidatos": CAND,
    "sin_descripcion": False,
    "ui_link": "https://sam.gov/opp/f3abcdef/view",
}

h = tn.formatear_analisis(FICHA)
check("La linea del Ship-To aparece",
      "📍 Ship-To: Chicago, Illinois" in h, "-> no esta")
check("Y la region con la que se ordena",
      "Región: Midwest" in h, "-> no esta")
check("Explica por que ese orden",
      "cercanía a la región del destino" in h, "-> no esta")

h_sin = tn.formatear_analisis(dict(FICHA, lugar_entrega="No especificado"))
check("Sin destino NO se pinta la linea", "📍 Ship-To:" not in h_sin)


# =====================================================================
# 4) ESCENARIOS: LA CUENTA DE LOS TRES RENGLONES
# =====================================================================
print()
print("=" * 76)
print("4) ESCENARIOS DE OFERTA: 100% / 97% / 95% DEL PRESUPUESTO")
print("=" * 76)

PRESUP = 85000.0
COSTO = 52000.0
esc = sensibilidad.escenarios(PRESUP, COSTO)
print("     %-8s %12s %12s %9s" % ("pct", "precio", "ganancia", "margen"))
for f in esc:
    print("     %-8.1f %12.2f %12.2f %8.2f%%"
          % (f["pct"], f["precio"], f["ganancia_neta"], f["margen"]))

check("Salen los tres escenarios", len(esc) == 3, "-> %d" % len(esc))
check("Los porcentajes son 100 / 97 / 95",
      [f["pct"] for f in esc] == [100.0, 97.0, 95.0],
      "-> %s" % [f["pct"] for f in esc])

for f in esc:
    pct = f["pct"]
    check("Precio %.0f%% = presupuesto x pct" % pct,
          abs(f["precio"] - PRESUP * pct / 100) < 0.01,
          "-> %s" % f["precio"])
    check("Factoring = 3.5%% del PRECIO de ese escenario (%.0f%%)" % pct,
          abs(f["factoring_usd"] - f["precio"] * 0.035) < 0.01,
          "-> %s" % f["factoring_usd"])
    check("Ganancia = precio - costo - factoring (%.0f%%)" % pct,
          abs(f["ganancia_neta"]
              - (f["precio"] - COSTO - f["factoring_usd"])) < 0.01,
          "-> %s" % f["ganancia_neta"])
    check("Margen contra el PRECIO OFERTADO (%.0f%%)" % pct,
          abs(f["margen"] - f["ganancia_neta"] / f["precio"] * 100) < 0.001,
          "-> %s" % f["margen"])
    # En el techo precio == presupuesto, asi que las dos bases dan lo mismo
    # y el check anterior se cumpliria por casualidad. En los otros dos no:
    # ahi es donde se ve de verdad que la division no es contra el
    # presupuesto (que infla el margen y lleva a ofertar barato).
    if pct < 100:
        check("...y NO contra el presupuesto (%.0f%%)" % pct,
              abs(f["margen"] - f["ganancia_neta"] / PRESUP * 100) > 0.01,
              "-> las dos bases dan lo mismo")

print()
print("  El factoring es DINAMICO, no un 3.5% escrito a mano:")
check("Por defecto sale de config.FACTORING_PCT",
      abs(esc[0]["factoring_usd"] - PRESUP * config.FACTORING_PCT) < 0.01)
_viejo = config.FACTORING_PCT
try:
    config.FACTORING_PCT = 0.05
    f5 = sensibilidad.escenarios(PRESUP, COSTO)
    check("Se relee en el MOMENTO (si cambia el tipo, cambia la tabla)",
          abs(f5[0]["factoring_usd"] - PRESUP * 0.05) < 0.01,
          "-> %s" % f5[0]["factoring_usd"])
finally:
    config.FACTORING_PCT = _viejo
check("Y el tipo de config sigue en su sitio tras la prueba",
      config.FACTORING_PCT == _viejo)

check("Se puede variar explicitamente",
      abs(sensibilidad.escenarios(1000, 100, factoring_pct=0.05)[0]
          ["factoring_usd"] - 50.0) < 0.01)

print()
print("  Degradacion: sin datos no hay tabla (nunca una con huecos):")
check("Sin presupuesto -> []", sensibilidad.escenarios(None, COSTO) == [])
check("Sin costo -> []", sensibilidad.escenarios(PRESUP, None) == [])
check("Presupuesto cero -> []", sensibilidad.escenarios(0, COSTO) == [])
check("Texto -> []", sensibilidad.escenarios("ochenta mil", COSTO) == [])
check("Costo negativo -> []", sensibilidad.escenarios(PRESUP, -1) == [])

print()
print("  Un escenario rojo NO se esconde (si pierde, se muestra que pierde):")
rojo = sensibilidad.escenarios(1000, 990)
check("La ganancia sale negativa", rojo[0]["ganancia_neta"] < 0,
      "-> %s" % rojo[0]["ganancia_neta"])
check("Y el margen tambien", rojo[0]["margen"] < 0,
      "-> %s" % rojo[0]["margen"])


# =====================================================================
# 5) ESCENARIOS: EL BLOQUE EN LA FICHA
# =====================================================================
print()
print("=" * 76)
print("5) ESCENARIOS: LA TABLA SALE EN LA FICHA")
print("=" * 76)

check("La ficha trae el bloque",
      "📊 <b>Escenarios de oferta (sensibilidad)</b>" in h, "-> no esta")
bloques = re.findall(r"<pre>(.*?)</pre>", h, re.S)
check("Hay un SEGUNDO <pre>, con la tabla", len(bloques) >= 2,
      "-> %d" % len(bloques))
tabla = bloques[1] if len(bloques) >= 2 else ""
check("Trae las tres filas",
      all(x in tabla for x in ("100%", "97%", "95%")), "-> %r" % tabla)
check("Trae las tres columnas pedidas",
      all(x in tabla for x in ("Precio", "Ganancia", "Margen")),
      "-> %r" % tabla)
check("Dice el factoring aplicado",
      "Factoring dinámico %g%%" % (config.FACTORING_PCT * 100) in h,
      "-> no esta")
check("Dice sobre que se mide el margen",
      "sobre el precio ofertado" in h, "-> no esta")

print()
print("  El PRIMER <pre> sigue siendo el resumen financiero (lo mide "
      "test_formato):")
primero = re.search(r"<pre>(.*?)</pre>", h, re.S)
check("El primero contiene el resumen",
      primero and "Presupuesto govt." in primero.group(1))
anchos = {len(l) for l in (primero.group(1).split("\n") if primero else [])
          if l.strip() and not l.startswith("─")}
check("Y sus columnas siguen todas iguales",
      len(anchos) == 1, "-> anchos %s" % sorted(anchos))

print()
print("  Sin datos, no se pinta la tabla:")
h_vacio = tn.formatear_analisis(dict(FICHA, valor_contrato_usd=None,
                                      costo_total_usd=None))
check("No aparece el bloque", "Escenarios de oferta" not in h_vacio)
check("Pero el resumen de arriba si",
      "💰 <b>Resumen financiero</b>" in h_vacio)

print()
print("  Si el analisis ya trae la tabla calculada, se usa la suya:")
precalculada = [{"pct": 100.0, "precio": 1.0, "factoring_usd": 0.0,
                 "ganancia_neta": 0.5, "margen": 50.0}]
h_pre = tn.formatear_analisis(dict(FICHA, escenarios_oferta=precalculada))
check("Se respelda el dato del analisis",
      "100%" in re.findall(r"<pre>(.*?)</pre>", h_pre, re.S)[1]
      and "$0.50" in re.findall(r"<pre>(.*?)</pre>", h_pre, re.S)[1])

print()
print("  La ficha sigue cabiendo en un mensaje y sin arrobas:")
check("Cabe en un solo mensaje", len(h) < 4096, "-> %d" % len(h))
check("No se colo ningun @", "@" not in h, "-> hay un @")


# =====================================================================
# 6) AWARD TRACKER: SOLO LO NUESTRO SE CELEBRA
# =====================================================================
print()
print("=" * 76)
print("6) AWARD TRACKER: UEI, NOTICE Y PRIORIDAD")
print("=" * 76)

NUESTRA = km.normalizar_adjudicacion({
    "contractId": "W9127N26QA145",
    "agencyName": "Army Corps of Engineers",
    "awardAmount": 150000,
    "description": "Bearing kits, COTS",
    "uiLink": "https://sam.gov/opp/abc/view",
    "uei": "NEXGNXGF5E38",
    "noticeId": "W912DY-26-R-0042",
})
t = km.texto_adjudicacion(NUESTRA)
print(t)
print()

check("El UEI del registro se reconoce como nuestro",
      km.es_nuestra_adjudicacion(NUESTRA) is True)
check("Un UEI ajeno NO es nuestro",
      km.es_nuestra_adjudicacion(dict(NUESTRA, uei="OTROUEI999")) is False)
check("Un registro sin UEI no se puede descartar",
      km.es_nuestra_adjudicacion({"uei": ""}) is True)
check("Y una cosa que no es un dict, no",
      km.es_nuestra_adjudicacion(None) is False)

check("El registro marca que el UEI vino verificado",
      NUESTRA["uei_en_registro"] is True)
check("Sin UEI en el registro, queda como asumido",
      km.normalizar_adjudicacion({"contractId": "X1"})["uei_en_registro"]
      is False)

print("  El formato original sigue EXACTO:")
check("El encabezado, letra por letra",
      t.startswith("🏆 ¡CONTRATO ADJUDICADO A L.A.M.B LOGISTICS!"))
check("El separacion esta", "------------------------------------------" in t)
check("El ID", "• 📄 ID Contrato: `W9127N26QA145`" in t)
check("La agencia", "• 🏛️ Agencia: Army Corps of Engineers" in t)
check("El monto", "• 💰 Monto Total: `$150,000.00 USD`" in t)
check("El SOW", "• 📦 Objeto / SOW: Bearing kits" in t)
check("El enlace", "🔗 [Ver Contrato Oficial en SAM.gov]"
      "(https://sam.gov/opp/abc/view)" in t)
check("El paso siguiente",
      "⚡ *Paso Siguiente:* Iniciar Orden de Compra y Factoring." in t)

print()
print("  Lo que se anadio en la Fase 3:")
check("Prioridad ALTA visible",
      "• 🚨 Prioridad: ALTA (adjudicación ganada)" in t)
check("La UEI con su origen",
      "• 🔑 UEI: `NEXGNXGF5E38` · verificada en el registro" in t)
check("El Notice of Award enlazado",
      "• 📋 Notice ID: `W912DY-26-R-0042`" in t)

sin_notice = km.texto_adjudicacion(
    km.normalizar_adjudicacion({"contractId": "X1"}))
check("Sin Notice ID la linea NO se pinta (no se inventa)",
      "Notice ID" not in sin_notice)
check("Y el formato vacio sigue siendo el de siempre",
      sin_notice.startswith("🏆 ¡CONTRATO ADJUDICADO A L.A.M.B LOGISTICS!")
      and "Agencia: N/A" in sin_notice and "$N/A USD" in sin_notice)

print()
print("  El ciclo no celebra premios ajenos (monkeypatch, sin red):")
_enviados = []
_orig = (km._get, km.enviar_telegram, km._guardar_estado)


async def _fake_get(session, url, params, **kw):
    return {"contractAwardList": [
        {"contractId": "NUESTRO-1", "uei": "NEXGNXGF5E38",
         "agencyName": "Army", "awardAmount": 1000},
        {"contractId": "AJENO-2", "uei": "OTROUEI999",
         "agencyName": "Otro", "awardAmount": 2000},
        {"contractId": "NUESTRO-3", "uei": km.UEI,
         "agencyName": "Navy", "awardAmount": 3000},
    ]}


async def _fake_enviar(session, texto):
    _enviados.append(texto)
    return True


try:
    km._get = _fake_get
    km.enviar_telegram = _fake_enviar
    km._guardar_estado = lambda estado: None
    enviadas = asyncio.run(km.ciclo_adjudicaciones(None, {}))
finally:
    km._get, km.enviar_telegram, km._guardar_estado = _orig

todo = "\n".join(_enviados)
check("Se mandan SOLO las de nuestra UEI", enviadas == 2,
      "-> %d" % enviadas)
check("El primero sale", "NUESTRO-1" in todo)
check("El tercero sale", "NUESTRO-3" in todo)
check("Y el AJENO no se envia", "AJENO-2" not in todo)


# =====================================================================
# 7) RETROCOMPATIBILIDAD
# =====================================================================
print()
print("=" * 76)
print("7) RETROCOMPATIBILIDAD: NADA DE LO DE ANTES SE MOVIO")
print("=" * 76)

check("para_oportunidad conserva su firma",
      list(inspect.signature(distribuidores.para_oportunidad).parameters)
      == ["terminos", "producto", "pais_destino",
          "query_google_proveedores", "candidatos"],
      "-> %s" % list(
          inspect.signature(distribuidores.para_oportunidad).parameters))
d_viejo = distribuidores.para_oportunidad(["bearing"], "S-1", "", "", [])
for clave in ("principal", "candidatos", "sitios", "fabricantes",
              "terminos", "consultas", "consulta_gemini"):
    check("Devuelve la clave vieja %r" % clave, clave in d_viejo)
check("Y solo ANADE las de la Fase 3",
      set(d_viejo) - {"principal", "candidatos", "sitios", "fabricantes",
                      "terminos", "consultas", "consulta_gemini"}
      == {"destino", "ordenado_por_cercania"},
      "-> %s" % sorted(set(d_viejo)))

check("formatear_analisis conserva su firma",
      list(inspect.signature(tn.formatear_analisis).parameters) == ["a"])
check("normalizar_adjudicacion conserva su firma",
      list(inspect.signature(km.normalizar_adjudicacion).parameters)
      == ["crudo"])
check("texto_adjudicacion conserva su firma",
      list(inspect.signature(km.texto_adjudicacion).parameters) == ["a"])

print()
print("  El contrato visual de la ficha, tal y como estaba:")
for etiqueta, esperado in (
        ("Saludo", "¡Amo, oportunidad nueva y bien armada!"),
        ("Separadores", "━━━━━━━━"),
        ("Resumen financiero", "💰 <b>Resumen financiero</b>"),
        ("Presupuesto", "Presupuesto govt."),
        ("Costo", "Costo proveedor"),
        ("Precio a ofertar", "Precio a ofertar"),
        ("Ganancia neta", "GANANCIA NETA"),
        ("Margen neto", "Margen neto"),
        ("Estrategia", "🎯 <b>Estrategia de oferta</b>"),
        ("Distribuidores", "🏭 <b>Distribuidores a verificar (USA)</b>"),
        ("Enlace", "Ver ficha en SAM.gov"),
):
    check("%s sigue ahi" % etiqueta, esperado in h)

check("La fecha sigue ANTES del bloque financiero",
      h.index("Vence en") < h.index("Presupuesto govt."))
check("Los4 numeros del resumen salen igual",
      all(x in h for x in ("$85,000.00", "$52,000.00", "$74,500.00")))

print()
print("  Los modulos nuevos no traen secretos ni tocan los viejos:")
fuente_sens = inspect.getsource(sensibilidad)
check("sensibilidad no lleva claves", "AIza" not in fuente_sens
      and "SAM-" not in fuente_sens)
check("sensibilidad solo lee config (no red, no envia nada)",
      "requests" not in fuente_sens and "urllib" not in fuente_sens
      and "os.getenv" not in fuente_sens)
fuente_dist = inspect.getsource(distribuidores)
check("distribuidores no ha crecido en dependencias",
      "requests" not in fuente_dist and "import pdf_generator"
      not in fuente_dist)
check("Tampoco se le ha colado ninguna clave",
      "AIza" not in fuente_dist and "SAM-" not in fuente_dist)

print()
print("=" * 76)
if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: geo-matching, escenarios y award tracker en verde")
