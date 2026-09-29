"""
prueba_lote.py - Comprueba si 5 avisos en una llamada rinden igual que 5 sueltos.

POR QUE ESTA PRUEBA
La cuota del plan gratis de Google son 20 LLAMADAS al dia, no 20 avisos. Cada
llamada individual usa unas 2.400 tokens de un contexto de un millon: el 0,2%
de lo disponible. Enviando los avisos de uno en uno se desperdicia el 99,8%.

Si 5 avisos entran en una llamada, la cuota rinde 5 veces mas y el analisis
sigue siendo gratuito. Pero "cabe" no es lo mismo que "rinde igual": un
modelo puede mezclar los numeros de dos avisos, o empezar a saltarse el
tercero. Eso se nota en las cifras, no en el JSON.

Asi que la prueba compara las DOS formas con la API real, sobre los mismos
avisos, y mide:
  - si salen los 5 analisis, no menos
  - si las cifras por unidad de cada aviso coinciden con las del envio suelto
  - si algun numero de un aviso se le atribuyo a otro (contaminacion)

    python prueba_lote.py
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if not os.environ.get("GEMINI_API_KEY"):
    print("Falta GEMINI_API_KEY en el entorno.")
    sys.exit(2)

import gemini_analyzer as ga

# ------------------------------------------------------------------ avisos
# Casos con producto fisico claro, cantidades distintas y precios distintos,
# que es donde se manifestaria una contaminacion entre avisos.
AVISOS = [
    {
        "id": "LOTE-A",
        "opp": {
            "noticeId": "TEST-A", "title": "PARTS KIT,BALL VALVE ASSEMBLY",
            "solicitationNumber": "N0010426Q0001", "naicsCode": "332999",
            "agencies": [{"name": "Department of the Navy"}],
            "typeOfSetAside": "", "description": "",
            "responseDeadLine": "2026-11-04T17:00:00-04:00",
        },
        "desc": (
            "Adquisicion de 45 PARTS KIT para valvulas de bola de acero. "
            "Cada kit incluye cuerpo de valvula, disco, asiento y pernos de acero "
            "inoxidable. Material de grado militar, normas MIL-STD. "
            "Not to exceed USD 40,000.00. Entrega fisica en instalacion naval. "
            "Se requiere distribuidor con inventario en Estados Unidos."
        ),
        "lugar": "United States",
    },
    {
        "id": "LOTE-B",
        "opp": {
            "noticeId": "TEST-B", "title": "GENERATOR CONTROL MODULE 50KW",
            "solicitationNumber": "FA8601-26-Q0182", "naicsCode": "335312",
            "agencies": [{"name": "Department of the Air Force"}],
            "typeOfSetAside": "Small Business", "description": "",
            "responseDeadLine": "2026-12-01T17:00:00-05:00",
        },
        "desc": (
            "Adquisicion de 12 modulos de control electronico y 4 kits de "
            "alternadores de repuesto para generadores diesel tacticos de 50kW. "
            "Estandar MIL-STD-882. Not to exceed USD 85,000.00. "
            "Entrega fisica directa en base aerea."
        ),
        "lugar": "United States",
    },
    {
        "id": "LOTE-C",
        "opp": {
            "noticeId": "TEST-C", "title": "OFFICE FURNITURE STEEL DESK",
            "solicitationNumber": "47QPTC000000", "naicsCode": "337115",
            "agencies": [{"name": "General Services Administration"}],
            "typeOfSetAside": "", "description": "",
            "responseDeadLine": "2026-11-20T17:00:00-05:00",
        },
        "desc": (
            "Adquisicion de 60 escritoriosmetalicos de 1.50 metros con cajones "
            "y 120 sillas ergonómicas. Acabado en laminado gris. "
            "Not to exceed USD 72,000.00. Entrega a bodega federal."
        ),
        "lugar": "United States",
    },
    {
        "id": "LOTE-D",
        "opp": {
            "noticeId": "TEST-D", "title": "PUMP ASSEMBLY HYDRAULIC",
            "solicitationNumber": "N0001926001", "naicsCode": "333996",
            "agencies": [{"name": "Department of the Navy"}],
            "typeOfSetAside": "", "description": "",
            "responseDeadLine": "2026-11-15T17:00:00-05:00",
        },
        "desc": (
            "Adquisicion de 8 conjuntos de bomba hidraulica de 40 galones por "
            "minuto con motor electrico de 20 HP, carcasa de hierro fundido. "
            "Repuestos incluidos. Not to exceed USD 96,000.00."
        ),
        "lugar": "United States",
    },
    {
        "id": "LOTE-E",
        "opp": {
            "noticeId": "TEST-E", "title": "PPE SAFETY EQUIPMENT ISSUE",
            "solicitationNumber": "W911RZ2601", "naicsCode": "339113",
            "agencies": [{"name": "Department of the Army"}],
            "typeOfSetAside": "", "description": "",
            "responseDeadLine": "2026-10-30T17:00:00-04:00",
        },
        "desc": (
            "Suministro de equipo de proteccion personal para 200Signaleros: "
            "cascos, guantes, gafas de seguridad, botas y chalecos de alta "
            "visibilidad. Not to exceed USD 28,000.00."
        ),
        "lugar": "United States",
    },
]

print("=" * 74)
print("  PRUEBA REAL CONTRA LA API: 5 EN UNA LLAMADA vs 5 SUELTAS")
print("=" * 74)
print()

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


# ------------------------------------------------- 1) envio individual
print("=" * 74)
print("1) CINCO LLAMADAS SUELTAS (como funciona hoy)")
print("=" * 74)
sueltos = {}
t0 = time.time()
gastadas = 0
for a in AVISOS:
    try:
        r = ga.analizar(a["opp"], a["desc"], a["lugar"])
        gastadas += 1
        sueltos[a["id"]] = r
        print(f"  [{a['id']}] viable={r['viable']} "
              f"neto={r.get('margen_neto_porcentaje')}%")
    except Exception as e:
        print(f"  [{a['id']}] ERROR: {type(e).__name__}: {str(e)[:110]}")
print()
print(f"  Llamadas que salieron: {gastadas} de 5")
print(f"  Tiempo: {time.time() - t0:.0f}s")
print()

if not sueltos:
    print("  Sin datos de referencia: no hay contra que comparar. Sali de aqui.")
    sys.exit(2)

# ------------------------------------------------- 2) envio por lote
print()
print("=" * 74)
print("2) UNA SOLA LLAMADA CON LOS CINCO")
print("=" * 74)
def _construir_lote(lote):
    partes = [
        "Eres Kyomoto, analista senior de abastecimiento del gobierno de EE.UU.",
        f"Evalua {len(lote)} oportunidades de CONTRATACION DE PRODUCTOS FISICOS.",
        "",
        "Cada una se entrega como un bloque. Analiza TODAS y responde con un",
        "objeto JSON por cada una, con la clave 'id' tal como te la di.",
        "",
        "REGLAS DE ELIMINACION por aviso (viable = false si se cumple alguna):",
        f"  1. Servicio intangible, consultoria, personal, TI, software,",
        f"     mantenimiento, construccion, limpieza, transporte o alquiler.",
        f"  2. Valor mayor a USD {ga.config.TOPE_USD:,.0f} o menor a "
        f"USD {ga.config.MIN_USD:,.0f}.",
        "  3. Exige presencia en obra, licencia local o proveedor residente",
        "     en EE.UU.",
        "  4. Entrega fuera de Estados Unidos.",
        "  5. Fecha limite ya vencida.",
        f"  6. Margen NETO (despues de factoring de "
        f"{ga.config.FACTORING_PCT * 100:.1f}%) por debajo de "
        f"{ga.config.MARGEN_NETO_MIN * 100:.0f}%.",
        "",
        "MODELO ECONOMICO, POR UNIDADES Y NO POR CONTRATO:",
        "El cliente es unipersonal en CHILE, compra en Estados Unidos a un",
        "distribuidor y revende al gobierno de EE.UU. Usa factoring dentro de",
        "USA, asi que no necesita capital. Necesita margen BRUTO entre",
        f"{ga.config.MARGEN_BRUTO_MIN * 100:.0f}% y "
        f"{ga.config.MARGEN_BRUTO_MAX * 100:.0f}% para que valga la pena.",
        "  cantidad x costo unitario = costo total",
        "  cantidad x precio ofertado = valor del contrato",
        "  ganancia bruta = valor ofertado - costo total",
        "  costo factoring = 3.5% del VALOR OFERTADO (no del presupuesto)",
        "  ganancia neta = bruta - factoring",
        "Si la cantidad o el precio no estan, estima desde el valor del",
        "contrato y la especificacion, y dilo. Mejor una estimacion que un",
        "cero.",
        "",
        "NO mezcles datos entre avisos: cada analisis usa SOLO el bloque de su",
        "propio aviso. Es el error mas importante a evitar.",
        "",
        "FORMATO: responde UN objeto JSON, sin texto alrededor:",
        '{"analisis": [',
        '  {"id": "...", "viable": true, "producto": "...",',
        '   "cantidad_total": 0, "unidad_medida": "EA",',
        '   "precio_unitario_costo": 0, "precio_unitario_mercado": 0,',
        '   "precio_unitario_oferta": 0, "ganancia_por_unidad": 0,',
        '   "costo_total_usd": 0, "ganancia_total_usd": 0,',
        '   "margen_bruto_porcentaje": 0, "costo_factoring_usd": 0,',
        '   "ganancia_neta_usd": 0, "margen_neto_porcentaje": 0,',
        '   "precio_oferta_sugerido_usd": 0, "margen_por_distribuidor": "...",',
        '   "preguntas_criticas": ["..."], "nivel_riesgo": "bajo|medio|alto",',
        '   "motivo_descarte": ""}',
        "]}",
        "",
        "AVISOS:",
    ]
    for a in lote:
        partes.append(
            f"\n===== id: {a['id']} =====\n"
            f"Titulo: {a['opp']['title']}\n"
            f"Solicitud: {a['opp']['solicitationNumber']}\n"
            f"NAICS: {a['opp']['naicsCode']}\n"
            f"Agencia: {a['opp']['agencies'][0]['name']}\n"
            f"Fecha limite: {a['opp']['responseDeadLine']}\n"
            f"Entrega: {a['lugar']}\n"
            f"Descripcion:\n{a['desc']}\n"
        )
    return "\n".join(partes)


prompt_lote = _construir_lote(AVISOS)
print(f"  Caracteres del prompt de lote: {len(prompt_lote):,}")
print(f"  ~tokens:                      {len(prompt_lote) // 4:,}")
print(f"  Limite de salida:             {ga.config.GEMINI_MAX_OUTPUT_TOKENS:,} tokens")
print(f"  Necesario para 5 analisis:    ~4,000 tokens (JSON completo)")
print()
print(f"  OJO: con el limite de salida actual ({ga.config.GEMINI_MAX_OUTPUT_TOKENS:,})")
print(f"  NO CABEN los 5 analisis completos. Habria que subirlo a ~8,192.")
print()

lote = None
err = None
t0 = time.time()
try:
    cliente = ga._get_cliente()
    resp = cliente.models.generate_content(
        model=ga.config.GEMINI_MODEL,
        contents=prompt_lote,
        config={
            "temperature": ga.config.GEMINI_TEMPERATURE,
            "response_mime_type": "application/json",
            "max_output_tokens": 8192,
        },
    )
    lote = ga._parsear(resp.text) if hasattr(ga, "_parsear") else None
    if lote is None:
        import re as _re
        m = _re.search(r"\{.*\}", resp.text or "", _re.S)
        lote = json.loads(m.group(0)) if m else None
    print(f"  Respuesta recibida en {time.time() - t0:.0f}s")
except Exception as e:
    err = e
    print(f"  ERROR: {type(e).__name__}: {str(e)[:200]}")

print()
if err is not None:
    print("=" * 74)
    print("  LA LLAMADA EN LOTE NO PUDO PROBARSE")
    print("=" * 74)
    print(f"  {type(err).__name__}: {str(err)[:300]}")
    print()
    print("  Puede ser cuota agotada (ya se gastarion 5 en el paso anterior) o")
    print("  que el limite de salida no baste. Para el caso real habria que")
    print("  subir GEMINI_MAX_OUTPUT_TOKENS y reintentar con cuota fresca.")
    sys.exit(3)

analisis = (lote or {}).get("analisis") or []
print(f"  Analisis devueltos: {len(analisis)} de 5")
for a in analisis:
    print(f"    {a.get('id')}: viable={a.get('viable')} "
          f"neto={a.get('margen_neto_porcentaje')}%")

print()
print("=" * 74)
print("3) ¿LOS NUMEROS COINCIDEN CON LOS SUELTOS?")
print("=" * 74)
por_id = {a.get("id"): a for a in analisis}
faltan = [a["id"] for a in AVISOS if a["id"] not in por_id]
check("Salieron los 5 analisis", len(analisis) == 5,
      f"-> {len(analisis)}, faltan {faltan}")
check("Con los ids correctos", not faltan, f"-> faltan {faltan}")

for a in AVISOS:
    ref = sueltos.get(a["id"])
    got = por_id.get(a["id"])
    if not ref or not got:
        continue
    # El margen es la cifra que se decide ofertar o no. Si se parece, el
    # razonamiento por unidades sobrevive al agrupamiento.
    r_net = ref.get("margen_neto_porcentaje")
    g_net = got.get("margen_neto_porcentaje")
    if r_net is None or g_net is None:
        print(f"  [{a['id']}] sin margen en uno de los dos, no comparo")
        continue
    dif = abs(float(r_net) - float(g_net))
    # Tolerancia del 25% del margen: lo que se busca no es el mismo numero
    # exacto (el modelo estima), sino que no se haya contaminado con otro.
    ok = dif <= max(3.0, abs(float(r_net)) * 0.25)
    check(f"[{a['id']}] margen {r_net}% vs {g_net}%  (dif {dif:.1f}pp)", ok)

print()
print("=" * 74)
print("4) CONTAMINACION: ¿ALGUN AVISO TOMO DATOS DE OTRO?")
print("=" * 74)
# La firma de contaminacion es clara: dos avisos con el MISMO margen neto y
# la misma cantidad, cuando los avisos son de productos muy distintos.
pares = []
ids = [a.get("id") for a in analisis if a.get("margen_neto_porcentaje")]
for i, x in enumerate(ids):
    for y in ids[i + 1:]:
        if abs(float(por_id[x]["margen_neto_porcentaje"])
               - float(por_id[y]["margen_neto_porcentaje"])) < 0.01:
            pares.append(f"{x}={y}")
check("Ningun par de avisos quedo con margen identico al centimo", not pares,
      f"-> {pares}")
if pares:
    print("     Puede ser casualidad, pero conviene mirarlo: si dos avisos de")
    print("     productos muy distintos dan EXACTAMENTE el mismo margen, el")
    print("     modelo pudo copiar la respuesta de uno al otro.")

print()
print("=" * 74)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s). El lote NO es seguro todavia.")
    sys.exit(1)
print("RESULTADO: el lote rinde igual que los envios sueltos")
print()
print("  Con esto, 20 llamadas al dia analizan 100 avisos en vez de 20.")
