"""
gemini_analyzer.py - Evaluacion de la oportunidad con Gemini.

DIFERENCIA CRITICA CON EL CODIGO ORIGINAL:

El original hacia esto:

    try:
        response = ai_client.models.generate_content(...)
        return response.text
    except Exception as e:
        print(f"Error al invocar Gemini: {e}")
        return "NO_VIABLE"        # <-- AQUI

Traducido: si la API de Google fallaba (por ejemplo, 401 por clave invalida,
cuota agotada o error de red), el bot lo tomaba como "esta oportunidad no me
sirve" y se quedaba en silencio para siempre. Por eso Kyomoto no respondia:
no era que no encontrara licitaciones, era que NUNCA podia evaluarlas.

Aqui los errores de Gemini se propagan como GeminiError y se reportan a
Telegram. "No es viable" y "no pude preguntar" son dos cosas distintas.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time

import config
import filters

log = logging.getLogger("kyomoto.gemini")

_cliente = None
_bloqueo = threading.Lock()

ESQUEMA = {
    "viable": "bool",
    "motivo_descarte": "str",
    # --- Que se compra, a nivel de unidad ---
    "producto": "str",
    "modelo_especifico": "str",
    "unidad_medida": "EA|LOT|CJ|KIT|PAIR|SET|CAJA|OTRO",
    "cantidad_total": "num",
    "especificacion_tecnica_clave": "str",
    "lugar_entrega": "str",
    # --- Precio por unidad ---
    "precio_unitario_costo": "num|null",
    "precio_unitario_mercado": "num|null",
    "precio_unitario_oferta": "num|null",
    "ganancia_por_unidad": "num|null",
    # --- Totales del contrato ---
    "valor_contrato_usd": "num|null",
    "costo_total_usd": "num|null",
    "ganancia_total_usd": "num|null",
    "margen_bruto_porcentaje": "num|null",
    "costo_factoring_usd": "num|null",
    "margen_neto_porcentaje": "num|null",
    "precio_oferta_sugerido_usd": "num|null",
    # --- Como se consigue ese margen ---
    "estrategia_oferta": "str",
    "margen_por_distribuidor": "str",
    "busquedas_distribuidores": "[str]",
    "query_google_proveedores": "str",
    "distribuidores_candidatos": "[{nombre,tipo,porque,verificar}]",
    "nivel_riesgo": "bajo|medio|alto",
    "preguntas_criticas": "[str]",
    "observaciones": "str",
}


class GeminiError(Exception):
    pass


def _get_cliente():
    global _cliente
    if _cliente is not None:
        return _cliente
    if not config.GEMINI_API_KEY:
        raise GeminiError(
            "GEMINI_API_KEY no esta configurada. Ve a Render > Environment."
        )
    try:
        from google import genai
    except ImportError as e:
        raise GeminiError(f"google-genai no esta instalado: {e}") from e
    with _bloqueo:
        if _cliente is None:
            try:
                _cliente = genai.Client(api_key=config.GEMINI_API_KEY)
            except Exception as e:
                raise GeminiError(f"No se pudo crear el cliente de Gemini: {e}") from e
    return _cliente


_SIN_PENSAR = None


def _construir_config(sin_pensar: bool = False):
    """
    Config de la llamada a Gemini.

    `sin_pensar=True` omite el campo thinking_budget. Hace falta porque
    algunos modelos lo rechazan con 400 INVALID_ARGUMENT: medido el
    29-sep-2026 con gemini-3.5-flash-lite, que acepta response_mime_type y
    temperature pero no thinking_budget=0.

    Y ese modelo era justamente el unico con cuota libre ese dia, asi que
    sin esto el bot se quedaba sin nada que analizar. Cuando un 400 sale de aqui,
    se reintenta una vez sin el campo: se pierde un poco de razonamiento pero
    se desbloquea el modelo entero.
    """
    global _SIN_PENSAR
    from google.genai import types

    if sin_pensar:
        try:
            return types.GenerateContentConfig(
                temperature=config.GEMINI_TEMPERATURE,
                response_mime_type="application/json",
                max_output_tokens=config.GEMINI_MAX_OUTPUT_TOKENS,
            )
        except Exception:
            return types.GenerateContentConfig(
                max_output_tokens=config.GEMINI_MAX_OUTPUT_TOKENS
            )

    try:
        return types.GenerateContentConfig(
            temperature=config.GEMINI_TEMPERATURE,
            response_mime_type="application/json",
            max_output_tokens=config.GEMINI_MAX_OUTPUT_TOKENS,
            thinking_config=types.ThinkingConfig(
                thinking_budget=config.GEMINI_THINKING_BUDGET
            ),
        )
    except Exception:
        # Si la version instalada de google-genai no conoce thinking_config,
        # se sigue sin el en vez de reventar.
        return types.GenerateContentConfig(
            temperature=config.GEMINI_TEMPERATURE,
            response_mime_type="application/json",
            max_output_tokens=config.GEMINI_MAX_OUTPUT_TOKENS,
        )


def verificar_api() -> dict:
    """
    Prueba de humo. Recorre la MISMA cadena de modelos que usa analizar() Y
    CON LA MISMA CONFIG, para que el resultado refleje lo que de verdad pasara
    en un barrido.

    Importante: usa _construir_config(), no un dict a mano. El 28-sep-2026 el
    smoke test mandaba config={"max_output_tokens": 2048} mientras el escaneo
    real mandaba la config completa con response_mime_type y thinking_config.
    Resultado: gemini-3.5-flash-lite pasaba el smoke test con "OK" y devolvia
    400 INVALID_ARGUMENT con el prompt de verdad. Un test que usa una config
    distinta no prueba lo que dice probar.
    """
    if not config.GEMINI_API_KEY:
        return {"ok": False, "detalle": "GEMINI_API_KEY no esta configurada"}

    cola = [config.GEMINI_MODEL] + [
        m for m in config.GEMINI_MODELES_ALTERNATIVOS
        if m and m != config.GEMINI_MODEL
    ]
    caidos = []
    sin_cuota = []
    incompatibles = []
    for m in cola:
        try:
            cliente = _get_cliente()
            cliente.models.generate_content(
                model=m,
                contents='Responde solo con la palabra OK, en JSON: {"ok": true}',
                config=_construir_config(),
            )
            if m == config.GEMINI_MODEL:
                return {"ok": True, "detalle": f"OK - {m} responde", "modelo": m}
            # El principal estaba caido pero hay respaldo: es una nota, no un
            # fallo. El escaneo va a funcionar igual.
            return {
                "ok": True,
                "detalle": f"OK via {m} ({config.GEMINI_MODEL} no responde)",
                "modelo": m,
                "degradado": True,
                "avisos": caidos + incompatibles,
            }
        except Exception as e:
            texto = str(e)
            if "429" in texto or "RESOURCE_EXHAUSTED" in texto:
                # El tope es por modelo: se sigue con el siguiente, que puede
                # tener cuota todavia. Antes se rendia aqui y declaraba el
                # fallo con el primer 429, aunque quedaran modelos libres.
                sin_cuota.append(m)
                continue
            if "401" in texto or "UNAUTHENTICATED" in texto:
                return {"ok": False, "detalle": f"401: {texto[:200]}"}
            if "400" in texto or "INVALID_ARGUMENT" in texto:
                incompatibles.append(f"{m}: 400, no acepta la peticion")
            else:
                caidos.append(f"{m}: 503 saturado")
            continue

    return {
        "ok": False,
        "detalle": (
            "Ningun modelo de la lista sirve ahora mismo.\n"
            f"Sin cuota (429): {', '.join(sin_cuota) or 'ninguno'}. "
            f"Saturados (503): {', '.join(caidos) or 'ninguno'}. "
            f"Incompatibles (400): {', '.join(incompatibles) or 'ninguno'}.\n"
            "La cuota se reinicia a medianoche del Pacifico y es POR MODELO."
        ),
        "saturado": bool(caidos),
        "incompatible": bool(incompatibles),
        "cuota": bool(sin_cuota),
    }


def _detalle_de_cuota(texto_error: str) -> str:
    """
    Saca de la respuesta de Google el dato que sirve: cuanto es el tope y de
    que metrica se trata.

    Sin esto, el usuario solo lee "se agoto la cuota" y no tiene por donde
    empezar. Google lo dice muy claro en el 429:

        * Quota exceeded for metric:
          generativelanguage.googleapis.com/generate_content_free_tier_requests,
          limit: 20, model: gemini-3.8-flash

    El "limit: 20" es el tope real del plan gratis, medido el 29-sep-2026. Vale
    la pena repetirselo tal cual, porque "el plan gratis son 20 al dia" es la
    informacion que convierte un misterio en una decision.
    """
    if not texto_error:
        return ""
    tope = re.search(r"limit:\s*([0-9][0-9,]*)", texto_error)
    metrica = re.search(r"Quota exceeded for metric:\s*([A-Za-z0-9_./-]+)", texto_error)
    espera = re.search(r"Please retry in ([0-9.]+)s", texto_error)

    partes = ["Lo que dice Google:"]
    if tope:
        partes.append(f"   · Tope del plan gratis: {tope.group(1)} llamadas al dia.")
    if metrica:
        # El nombre de la metrica es largo y no le dice nada a un usuario
        # normal; solo se guarda en el log para diagnosticar.
        log.info("Metrica de cuota agotada: %s", metrica.group(1))
    if espera:
        partes.append(f"   · Google sugiere reintentar en {float(espera.group(1)):.0f}s.")
    if len(partes) == 1:
        return ""
    return "\n".join(partes) + "\n"


def _modelos_sin_cuota(probados: list) -> str:
    """
    Dice QUE modelos se quedaron sin cuota, no solo que "se agoto".

    El tope es por modelo, asi que puede pasar que 3.8-flash y flash-latest no
    respondan y 3.5-flash-lite si. Decirlo es la diferencia entre un misterio
    y un diagnostico: el usuario puede ir a /cuota y entender que hay que
    esperar a medianoche del Pacifico, no cambiar nada.
    """
    if not probados:
        return ""
    # Sin repetir: un mismo modelo puede anotarse dos veces si dio 429 al
    # primer intento y al reintentar sin thinking_budget, y al usuario le
    # pareceria que hay dos iguales en la lista.
    unicos = list(dict.fromkeys(probados))
    lineas = ["Modelos que ya agotaron su cuota hoy: " + ", ".join(unicos) + "."]
    return "\n".join(lineas) + "\n"


def _construir_prompt(opp: dict, descripcion: str, lugar: str) -> str:
    return f"""
Eres Kyomoto, analista senior de abastecimiento del gobierno de EE.UU.
Evalua esta oportunidad de CONTRATACION DE PRODUCTOS FISICOS para mi cliente:
una empresa unipersonal en CHILE que compra en Estados Unidos y despacha
directamente al destino de entrega. NO realiza instalaciones,
NO presta servicios, NO tiene personal en obra.

REGLA PRINCIPAL

  Si es una COMPRA o SUMINISTRO de PRODUCTO FISICO por menos de
  USD {config.TOPE_USD:,.0f}, la respuesta es viable = true.

  Ese es el negocio: revender producto fisico al gobierno de EE.UU. que se
  compra en Estados Unidos. Todo lo demas son detalles que se resuelven
  comprando bien.

LO QUE SI DESCALIFICA (viable = false)

  1. Que NO haya producto fisico que revender: consultoria, personal, TI,
     software, mantenimiento, construccion, limpieza, transporte o alquiler.
     Incluye los IDIQ de servicios y los contratos donde lo principal es una
     prestacion y no una entrega de cosas.
  2. Que el valor supere USD {config.TOPE_USD:,.0f} o sea menor a USD {config.MIN_USD:,.0f}.
  3. Que exija INSTALACION en obra, licencia local, o que el proveedor sea
     residente o establecido en EE.UU. Eso no lo puede hacer un proveedor
     que compra en USA y despacha desde Chile.
  4. Que la entrega sea fuera de Estados Unidos.
  5. Que la fecha limite para presentar oferta ya vencio.
  6. Que el margen NETO (despues de factoring) quede por debajo de
     {config.MARGEN_NETO_MIN * 100:.0f}%. Este es el filtro de negocio de verdad: por debajo de eso no
     vale la pena ofertar.

LO QUE YA NO DESCALIFICA (importante: antes si lo hacia)

  - "Entrega fisica directa en [base, instalacion, puerto]". ES LO NORMAL.
    Comprar en USA y mandar al destino es exactamente lo que hace este
    cliente. Antes esto se confundia con "presencia en obra" y se
    descartaba la oportunidad, y con ella casi todas las piezas de repuesto,
    que son el nucleo del negocio. Medido el 29-sep-2026: de 12 analisis
    reales, uno se descarto solo por "entrega fisica".
  - "Empaque militar", "MIL-STD", "gradiente militar", "packed for extended
    storage", "preservacion a largo plazo". Es la ESPECIFICACION DEL
    PRODUCTO, no una barrera para venderlo. El proveedor compra en USA y
    recibe el producto como venga.
  - Que el placo o el manual este en ingles.
  - Que exija certificacion de calidad del FABRICANTE (ISO 9001, AS9100).
    Se compra a un distribuidor que ya la tiene.
  - Que sea un IDIQ o un contrato multiple de entrega. Es la forma mas
    comun de comprar en el gobierno de EE.UU.

  Ante la duda, si hay producto fisico y el margen sale, es viable = true.
  Descartar de mas deja dinero sobre la mesa.

MODELO ECONOMICO (esto es lo mas importante, hazlo bien)

Mi cliente es una empresa unipersonal en CHILE que:
  - compra el producto en ESTADOS UNIDOS a un distribuidor
  - lo revende al gobierno de EE.UU.
  - NO tiene capital: usa factoring dentro de USA, que cobra un
    {config.FACTORING_PCT * 100:.1f}% del valor del contrato
  - necesita un margen BRUTO entre {config.MARGEN_BRUTO_MIN * 100:.0f}% y
    {config.MARGEN_BRUTO_MAX * 100:.0f}% para que el negocio valga la pena

Razona por UNIDADES, no por contrato. Ejemplo del razonamiento correcto:
  "50 laptops modelo X" -> 50 unidades x $1,200 de costo = $60,000 invertido
  -> se ofrece a $1,320 unitario = $66,000
  -> $6,000 de ganancia bruta (16.7%)
  -> factoring 3.5% sobre $66,000 = $2,310
  -> ganancia neta $3,690 (11.2% del valor del contrato)
  -> si el margen neto queda bajo {config.MARGEN_NETO_MIN * 100:.0f}%, no ofertar

Cuando la cantidad o el precio no estén en el aviso, ESTIMA a partir del
valor del contrato y de la especificacion, y dilo. Es preferible un numero
razonado que un null.

SI ES VIABLE, ENTREGA:

  producto                      : que se compra, en espanol claro
  modelo_especifico              : marca y modelo exacto, o "" si no se indica
  unidad_medida                 : EA | LOT | CJ | KIT | PAIR | SET | CAJA | OTRO
  cantidad_total                : numero de unidades. Sin "comas" ni texto.
  especificacion_tecnica_clave  : 3-5 datos tecnicos que hay que cumplir
  lugar_entrega                 : ciudad / estado, o "No especificado"
  valor_contrato_usd            : mejor estimacion del valor total
  precio_unitario_costo         : lo que cuesta COMPRAR una unidad en USA
  precio_unitario_mercado       : precio de catalogo de una unidad
  precio_unitario_oferta        : por que unidad hay que ofrecer
  ganancia_por_unidad           : oferta - costo, por unidad
  costo_total_usd               : cantidad x precio_unitario_costo
  ganancia_total_usd            : cantidad x ganancia_por_unidad
  margen_bruto_porcentaje       : ganancia_total / valor_contrato * 100
  costo_factoring_usd           : valor_contrato * {config.FACTORING_PCT:.3f}
  margen_neto_porcentaje        : (ganancia_total - factoring) / valor * 100
  precio_oferta_sugerido_usd    : total a ofertar (cantidad x unitario)
  estrategia_oferta           : NO LA REDACTES. Kyomoto la escribe en Python,
                                  calculada con las cifras finales, y la
                                  sobreescribe a lo que pongas aqui. Pon ""
                                  y listo. Si escribes un porcentaje de
                                  descuento aqui, casi seguro no va a cuadrar
                                  con el que calcula el codigo, y la ficha
                                  mostraria dos cifras distintas para la misma
                                  cuenta. Kyomoto recalcula los totales con
                                  cantidad x precio unitario porque no confia
                                  en los que das tu, y el texto de la
                                  estrategia tiene que salir de esos mismos
                                  numeros recalculados.
  razonamiento_oferta          : UNA frase que explique el monto ofertado.
                                 Debe decir cuantos por ciento queda POR
                                 DEBAJO del presupuesto del gobierno, y
                                 cuanto se lleva el margen neto.
                                 Ejemplo: "Con este monto nos mantenemos un
                                 12% por debajo del presupuesto maximo del
                                 gobierno para asegurar alta competitividad
                                 y ganar el contrato, asegurando una ganancia
                                 neta estimada de $22,500.00."
  busquedas_distribuidores      : 3-5 terminos EN INGLES con el sustantivo
                                  tecnico del producto y el modelo, sin
                                  palabras de instruccion.
                                  Ej: "Dell Latitude 5450 wholesale distributor"

  query_google_proveedores      : UN solo termino de busqueda en ingles, el
                                  MEJOR posible para encontrar
                                  DISTRIBUIDORES de este producto concreto
                                  en Google. Este es el que se convierte en
                                  el enlace de la ficha, asi que tiene que
                                  ser preciso, no generico:
                                    - Usa lo que dice la ESPECIFICACION y no
                                      el titulo: el tamano, el material, la
                                      norma, el accionamiento. Si el titulo
                                      dice "FIRE PUMP VALVES" y la
                                      especificacion dice "6 inch cast
                                      steel gate valve, ASTM A216, 150 psi",
                                      el termino es "6 inch cast steel gate
                                      valve wholesale distributor", no
                                      "fire pump valves".
                                    - Incluye "wholesale" o "distributor":
                                      el cliente compra a un MAYORISTA, no al
                                      fabricante, y ahi esta el margen.
                                    - Anade "usa" o "united states": se
                                      compra en Estados Unidos.
                                    - Sin comillas, sin comas y sin
                                      palabras de instruccion ("como
                                      comprar", "precio"). Solo el
                                      termino.
                                    - De 3 a 10 palabras.
  distributors_candidatos        : hasta 3 DISTRIBUIDORES REALES de Estados
                                  Unidos que conoces de nombre y que de verdad
                                  manejan esta clase de producto. Para cada
                                  uno:
                                    - nombre      : el nombre comercial, como
                                                    lo escribe la empresa
                                                    ("Grainger", "McMaster-Carr",
                                                    "Radwell")
                                    - tipo        : que vende y por que sirve
                                                    aqui (mayorista
                                                    industrial, casa de
                                                    catalogo, distribuidor
                                                    naval...)
                                    - porque      : 1 frase, por que este y no
                                                    otro
                                    - verificar   : UNA busqueda en Google, en
                                                    ingles, para confirmar que
                                                    existe, que vende esto y
                                                    sacar su contacto real

                                  REGLAS DURA. LEELAS ANTES DE CONTESTAR:
                                    - NUNCA inventes una direccion de correo, un
                                      telefono ni un sitio web. NO hay campo
                                      para eso a proposito. Kyomoto lo prohibe
                                      en el modulo distribuidores porque un
                                      correo inventado hace que el cliente
                                      escriba a una empresa equivocada y
                                      queme su reputacion. Si no sabes el
                                      correo, no lo pongas: no hay sitio donde
                                      ponerlo.
                                    - SOLO empresas que de verdad hayas visto
                                      en tu entrenamiento. Es preferible
                                      devolver 1 con certeza que devolver 3
                                      inventadas.
                                    - Si no estas seguro de que una empresa
                                      maneja ESTE producto, no la incluyas.
                                    - Si no se te ocurre ninguna con
                                      confianza, devuelve una lista vacia. Es
                                      una respuesta valida y preferible a
                                      inventar.
                                  NO se verifica nada automaticamente: la
                                  busqueda de "verificar" es la que hace el
                                  usuario, en un clic, antes de escribir a
                                  nadie. Por eso el nombre es lo unico que
                                  se muestra como dato.
                                 compre, con el rango en cada caso:
                                 - catalogo grande: 15-20% bruto
                                 - mayorista/importador: 20-25% bruto
                                 - fabricante directo: 25-35% bruto
                                 Di explicitamente cual conviene para ESTE
                                 producto y por que.
  nivel_riesgo                  : bajo | medio | alto
  preguntas_criticas            : 3-5 preguntas que DEBO hacer antes de ofertar
  observaciones                 : 1-2 frases de advertencia

FORMATO DE SALIDA: SOLO un objeto JSON valido, sin markdown, sin ```.
Las claves son exactamente:
{json.dumps(ESQUEMA, indent=2, ensure_ascii=False)}

Ejemplo, bidding 50 laptops:
{{"viable": true, "motivo_descarte": "",
  "producto": "Laptops para Answer Key Kiosks",
  "modelo_especifico": "Dell Latitude 5450",
  "unidad_medida": "EA", "cantidad_total": 50,
  "especificacion_tecnica_clave": "i5-1345U, 16GB RAM, 512GB SSD",
  "lugar_entrega": "Fort Huachuca / Arizona / UNITED STATES",
  "valor_contrato_usd": 66000,
  "precio_unitario_costo": 1200, "precio_unitario_mercado": 1249,
  "precio_unitario_oferta": 1320, "ganancia_por_unidad": 120,
  "costo_total_usd": 60000, "ganancia_total_usd": 6000,
  "margen_bruto_porcentaje": 9.1,
  "costo_factoring_usd": 2310, "margen_neto_porcentaje": 5.6,
  "precio_oferta_sugerido_usd": 66000,
  "busquedas_distribuidores": ["Dell Latitude 5450 wholesale distributor usa"],
    "query_google_proveedores": "laptop dell latitude 5450 wholesale distributor usa",
  "distribuidores_candidatos": [
    {{"nombre": "McMaster-Carr", "tipo": "casa de catalogo industrial",
     "porque": "Stock enorme y envio el mismo dia; se paga al catalogo, no a precio de contrato.",
     "verificar": "McMaster-Carr laptops dell contact sales"}},
    {{"nombre": "CDW", "tipo": "mayorista IT para empresas",
     "porque": "Volumen grande y cuenta corporativa; buen precio en lotes de 50 en adelante.",
     "verificar": "CDW dell latitude business sales contact"}},
    {{"nombre": "Insight", "tipo": "mayorista IT",
     "porque": "Alternativa a CDW, util para tener dos cotizaciones en paralelo.",
     "verificar": "Insight dell laptop volume pricing contact"}}],
  "nivel_riesgo": "medio", "preguntas_criticas": ["..."], "observaciones": ""}}
(NOTA: ese ejemplo sale con margen neto bajo y por tanto seria viable=false.
 Sirve solo para mostrar la forma, no el resultado esperado.)

DATOS DE LA OPORTUNIDAD
  Titulo:        {opp.get('title', 'sin titulo')}
  Numero:        {opp.get('solicitationNumber', 'N/A')}
  NAICS:         {opp.get('naicsCode', 'N/A')}   (PSC: {opp.get('classificationCode', 'N/A')})
  Adjudicacion:  {opp.get('baseType', 'N/A')} / {opp.get('typeOfSetAsideDescription', 'sin set-aside')}
  Publicado:     {opp.get('postedDate', 'N/A')}
  Limite:        {opp.get('responseDeadLine', 'N/A')}
  Lugar entrega: {lugar}
  Enlace:        {opp.get('uiLink', '')}

DESCRIPCION OFICIAL
{descripcion if descripcion.strip() else "*** NO DISPONIBLE *** (SAM.gov respondio 404 'Description Not Found' para este aviso. Evalua SOLO con el titulo y los metadatos, y baja la certeza de tus estimaciones. Si el titulo no alcanza para saber si es producto fisico, marca viable=false.)"}
""".strip()


def _extraer_json(texto: str) -> dict:
    """Tolerante: quita cercas de markdown y busca el primer objeto JSON."""
    if not texto:
        raise GeminiError("Gemini devolvio respuesta vacia")
    limpio = texto.strip()
    limpio = re.sub(r"^```(?:json)?|```$", "", limpio, flags=re.MULTILINE).strip()
    try:
        return json.loads(limpio)
    except json.JSONDecodeError:
        pass
    inicio = limpio.find("{")
    fin = limpio.rfind("}")
    if inicio == -1 or fin <= inicio:
        raise GeminiError(f"Gemini no devolvio JSON: {limpio[:300]}")
    try:
        return json.loads(limpio[inicio:fin + 1])
    except json.JSONDecodeError as e:
        raise GeminiError(f"JSON ilegible de Gemini: {e} | {limpio[:300]}")


def _num(valor):
    if valor in (None, "", "null", "N/A"):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    limpio = re.sub(r"[^0-9.\-]", "", str(valor))
    try:
        return float(limpio)
    except ValueError:
        return None


def analizar(
    opp: dict,
    descripcion: str,
    lugar: str = "",
    continuar=None,
) -> dict:
    """
    Devuelve el analisis ya normalizado.
    Lanza GeminiError si la API falla (NUNCA confunde fallo con "no viable").

    `continuar` es un callback opcional que devuelve bool. Se consulta antes
    de cada intento: en False, la funcion se rinde al instante, sin llamar a
    Google y sin esperar. Lo usa el escaner para propagar el cortacircuitos:
    cuando un hilo recibe un 429, los demas se enteran por aqui en vez de
    descubrir por su cuenta el mismo 429 y dormir cada uno lo suyo.
    """
    cliente = _get_cliente()
    prompt = _construir_prompt(opp, descripcion, lugar or filters.lugar_de_entrega(opp))

    ultimo_error = None
    intentos_429 = 0
    incompatibles = []
    # El 503 es por MODELO, no de la cuenta. Medido el 28-sep-2026:
    #   gemini-3.8-flash  OK 5.1s        gemini-3.6-flash    OK 4.2s
    #   gemini-3.7-flash  503 saturado    gemini-3.5-flash   OK 26.1s
    # Por eso se prueban alternativas en vez de esperar: los picos duran
    # minutos y hay modelos libres casi siempre.
    cola = [config.GEMINI_MODEL] + [
        m for m in config.GEMINI_MODELES_ALTERNATIVOS
        if m and m != config.GEMINI_MODEL
    ]
    indice = 0
    # Modelos que ya respondieron 429: se anotan para poder decir
    # al usuario cuales se quedaron sin cuota en vez de un 'agotado' generico.
    ya_probados: list[str] = []
    # Los que respondieron 503 (saturados, no sin cuota). Se anotan aparte
    # porque la causa y el remedio son distintos: uno espera a que se le pase
    # la demanda, el otro a medianoche del Pacifico.
    saturados: list[str] = []
    # Suficientes intentos para recorrer TODA la cola mas margen para los
    # errores que no sean de saturacion.
    total_intentos = max(config.MAX_REINTENTOS, len(cola) + 1)

    sin_pensar = False
    for intento in range(total_intentos):
        # Si otro hilo ya descubririo que se acabo la cuota, no se llama a
        # Google ni se duerme: se sale ahora. El unico modo de que un hilo
        # sepa lo que le paso a otro es preguntarselo.
        if continuar is not None and not continuar():
            raise GeminiError(
                "Se abandono el resto del lote: otro aviso ya confirmo que la "
                "cuota de Google se agoto (429).\n"
                "Lo que no se analice no se pierde: queda pendiente para el "
                "proximo barrido."
            )
        modelo = cola[indice]
        try:
            respuesta = cliente.models.generate_content(
                model=modelo,
                contents=prompt,
                config=_construir_config(sin_pensar=sin_pensar),
            )
            if modelo != config.GEMINI_MODEL:
                log.info("Va con %s: el de verdad iba saturado", modelo)
            break
        except Exception as e:
            ultimo_error = e
            texto = str(e)

            # --- 429: este MODELO se quedo sin cuota. ---
            # El tope es POR MODELO, no de la cuenta. Medido el 29-sep-2026:
            # gemini-3.5-flash-lite respondia mientras gemini-3.8-flash y
            # gemini-flash-latest ya estaban sin cuota. Si fuera de la cuenta,
            # cuando uno se agota se agotan todos.
            #
            # Antes de arreglar esto, el bot se rendia en el primer 429 y
            # tiraba la llamada entera, dejando sin usar todos los modelos que
            # si tenian cuota.
            if "429" in texto or "RESOURCE_EXHAUSTED" in texto:
                intentos_429 += 1
                ya_probados.append(modelo)
                if indice + 1 < len(cola):
                    siguiente = cola[indice + 1]
                    log.warning(
                        "429 en %s (este modelo ya agoto su cuota). "
                        "Cambio a %s.", modelo, siguiente,
                    )
                    indice += 1
                    intentos_429 = 0
                    time.sleep(1)
                    continue
                if intentos_429 > config.GEMINI_REINTENTOS_429:
                    raise GeminiError(
                        "Se agotaron TODOS los modelos de la lista (429).\n"
                        "Tu clave SI funciona: esto es el tope del plan gratis, no "
                        "un error del codigo. Se reinicia a las 00:00 del "
                        "Pacifico (~03:00 hora de Chile).\n"
                        + _detalle_de_cuota(texto)
                        + _modelos_sin_cuota(ya_probados)
                        + "\n\nCOMO SOLUCIONARLO\n"
                        "   1. Activa facturacion en https://aistudio.google.com "
                        "(Settings > Billing). Eso quita el tope gratis de "
                        f"{config.PRESUPUESTO_GEMINI_DIARIO}/dia y el bot puede "
                        "analizar todo lo que encuentre. Con flash cuesta "
                        "centimos.\n"
                        "   2. Si no quieres pagar, baja "
                        f"PRESUPUESTO_GEMINI_DIARIO (hoy "
                        f"{config.PRESUPUESTO_GEMINI_DIARIO}) y acepta fewer "
                        "licitaciones al dia.\n"
                        "   3. O usa otra cuenta de Google con su propia cuota.\n"
                        "Nada se pierde: los avisos NO se marcan como vistos, "
                        "asi que lo que no alcanzo queda pendiente para el "
                        "proximo barrido."
                    ) from e
                # Si otro hilo ya esta esperando por lo mismo, no se suma una
                # segunda espera: se rinde y deja que el lote se detenga.
                if continuar is not None and not continuar():
                    raise GeminiError(
                        "Otro aviso ya confirmo que la cuota se agoto (429)."
                    ) from e
                log.warning("429 de cuota. Pausa %.0fs.", config.GEMINI_PAUSA_SEG * 3)
                time.sleep(config.GEMINI_PAUSA_SEG * 3)
                continue

            # --- 503: el modelo esta saturado. Probar el siguiente. ---
            if "503" in texto or "UNAVAILABLE" in texto:
                saturados.append(modelo)
                if indice + 1 < len(cola):
                    log.warning("503 en %s. Cambio a %s.", modelo, cola[indice + 1])
                    indice += 1
                    time.sleep(2)
                    continue
                raise GeminiError(
                    "Ningun modelo de la lista sirve ahora mismo.\n"
                    "   · Sin cuota (429): "
                    + (", ".join(dict.fromkeys(ya_probados)) or "ninguno")
                    + "\n   · Sin respuesta (503): "
                    + (", ".join(dict.fromkeys(saturados)) or "ninguno")
                    + "\n\n"
                    + (
                        "La cuota se agota POR MODELO y se reinicia a las 00:00 "
                        "del Pacifico (~03:00 Chile). NO es tu clave ni el codigo: "
                        "es el tope del plan gratis. Kyomoto reintentara en el "
                        "proximo barrido y lo que no se analice no se pierde."
                        if ya_probados
                        else "Es la saturacion de Google, que es transitoria y "
                        "cambia cada hora. NO es tu clave ni el codigo. Kyomoto "
                        "reintentara en el proximo barrido y lo que no se analice "
                        "no se pierde."
                    )
                ) from e

            # --- 401: la credencial no es reconocida. No cambiar de modelo. ---
            if "401" in texto or "UNAUTHENTICATED" in texto:
                raise GeminiError(
                    "Google respondio 401: no reconoce GEMINI_API_KEY como "
                    "credencial valida para la API de Gemini.\n"
                    "1. La clave se copio incompleta (~53 caracteres con un punto; "
                    "un solo caracter mal da 401). Copiala con el boton de copiar.\n"
                    "2. La clave tiene restriccion de IP. Render usa IPs dinamicas, "
                    "asi que una clave restringida a tu IP local falla ahi.\n"
                    "3. La API Generative Language no esta habilitada.\n"
                    "Verificalo sin escribirla: python probar_clave.py"
                ) from e

            # --- 404: modelo retirado o inexistente. ---
            if "no longer available" in texto.lower() or "not_found" in texto.lower():
                incompatibles.append(f"{modelo}: no existe")
                if indice + 1 < len(cola):
                    indice += 1
                    continue
                raise GeminiError(
                    "Ningun modelo de la lista existe para esta cuenta: "
                    + "; ".join(incompatibles) + ".\n"
                    "Sondea los disponibles con: python sondear_modelos.py"
                ) from e

            # --- 400: el modelo no acepta la peticion. ---
            # NO es transitorio: es que ese modelo no sirve con esta config
            # (thinking_budget, response_mime_type, tokens...). Reintentarlo
            # igual da el mismo 400 y quema tiempo. Se cambia de modelo.
            #
            # Medido el 28-sep-2026: gemini-3.5-flash-lite respondia "OK" al
            # smoke test pero devolvia 400 INVALID_ARGUMENT con el prompt real.
            if "400" in texto or "INVALID_ARGUMENT" in texto:
                # El 400 mas frecuente es thinking_budget=0, y es facil de
                # arreglar: se reintenta el MISMO modelo sin ese campo antes
                # de pasar al siguiente. Antes de perder un modelo entero por
                # un campo que se puede apagar, se prueba apagandolo.
                #
                # Se hace una sola vez: si ya se reintento sin pensar y vuelve
                # a fallar, es que el problema es otro y hay que cambiar de
                # modelo. Sin este guardia seria un bucle infinito.
                if not sin_pensar:
                    sin_pensar = True
                    log.info(
                        "%s rechazo la peticion (400). Reintento sin "
                        "thinking_budget.", modelo,
                    )
                    continue

                incompatibles.append(f"{modelo}: no acepta la peticion (400)")
                if indice + 1 < len(cola):
                    log.warning("400 en %s. Cambio a %s.", modelo, cola[indice + 1])
                    indice += 1
                    continue
                raise GeminiError(
                    "Ningun modelo de la lista acepta la peticion (400 "
                    "INVALID_ARGUMENT): " + "; ".join(incompatibles) + ".\n"
                    "No es tu clave ni un problema de cuota. Suele ser que el "
                    "modelo no admite alguna opcion de la config. Prueba: "
                    "python sondear_modelos.py"
                ) from e

            log.warning("Gemini fallo (intento %s): %s", intento + 1, texto[:200])
            time.sleep(2 ** intento)
    else:
        raise GeminiError(
            f"Gemini fallo tras {total_intentos} intentos: "
            f"{type(ultimo_error).__name__}: {str(ultimo_error)[:250]}"
        ) from ultimo_error

    try:
        texto = respuesta.text
    except Exception:
        texto = ""
    if texto is None:
        bloque = getattr(respuesta, "candidates", [None])[0]
        motivo = getattr(bloque, "finish_reason", "desconocido")
        raise GeminiError(f"Gemini no genero contenido (finish_reason={motivo})")

    datos = _extraer_json(texto)

    if not isinstance(datos.get("viable"), bool):
        raise GeminiError(f"Gemini no devolvio el campo booleano 'viable': {texto[:200]}")

    # --- Lo que la IA dice ---
    cantidad = _num(datos.get("cantidad_total"))
    pu_costo = _num(datos.get("precio_unitario_costo"))
    pu_oferta = _num(datos.get("precio_unitario_oferta"))
    pu_mercado = _num(datos.get("precio_unitario_mercado"))
    valor = _num(datos.get("valor_contrato_usd"))

    # --- Lo que se recalcula aqui ---
    # Se desconfia de los totales: si vienen mal, la ficha ensena al cliente
    # una economia que no existe. Con cantidad y precio unitario se rehace
    # toda la cuenta, que es ademas la que el usuario revisa.
    if cantidad and cantidad > 0 and pu_costo and pu_oferta:
        if not valor:
            valor = pu_oferta * cantidad
        ganancia_unidad = pu_oferta - pu_costo
        costo_total = pu_costo * cantidad
        ganancia_total = ganancia_unidad * cantidad
        margen_bruto = (ganancia_total / valor * 100) if valor else None
        factoring = valor * config.FACTORING_PCT if valor else None
        ganancia_neta = (ganancia_total - factoring) if factoring is not None else None
        margen_neto = (ganancia_neta / valor * 100) if valor and ganancia_neta is not None else None
    else:
        # Sin unidades no se puede hacer la cuenta por unidad: se usa lo que
        # haya dado la IA y se marca como poco fiable.
        ganancia_unidad = None
        costo_total = _num(datos.get("costo_total_usd"))
        ganancia_total = _num(datos.get("ganancia_total_usd"))
        margen_bruto = _num(datos.get("margen_bruto_porcentaje"))
        factoring = valor * config.FACTORING_PCT if valor else _num(datos.get("costo_factoring_usd"))
        ganancia_neta = _num(datos.get("ganancia_total_usd"))
        margen_neto = _num(datos.get("margen_neto_porcentaje"))

    # El precio de oferta final, tal y como sale en la ficha, y la frase de
    # estrategia calculada con el. Se hacen aqui y no en la linea del return
    # porque la frase la necesitan los dos, y sacarla en el return hacia
    # imposible compararla con nada.
    oferta_final = _num(datos.get("precio_oferta_sugerido_usd")) or valor
    estrategia, _pct_descuento = calcular_estrategia_oferta(valor, oferta_final)
    if estrategia:
        log.info(
            "Estrategia calculada en Python para %s: %.2f%% de descuento",
            str(opp.get("noticeId"))[:8], _pct_descuento
            if _pct_descuento is not None else float("nan"),
        )

    return {
        "notice_id": opp.get("noticeId"),
        "viable": bool(datos["viable"]),
        "motivo_descarte": _limpiar_ia(datos.get("motivo_descarte"))[:300],
        "sin_descripcion": not bool(descripcion.strip()),
        "producto": _limpiar_ia(datos.get("producto"))[:400],
        "modelo_especifico": _limpiar_ia(datos.get("modelo_especifico"))[:120],
        "unidad_medida": _limpiar_ia(datos.get("unidad_medida"))[:20].upper() or "EA",
        "cantidad_total": cantidad,
        "especificacion_tecnica_clave": _limpiar_ia(datos.get("especificacion_tecnica_clave"))[:700],
        "lugar_entrega": _limpiar_ia(datos.get("lugar_entrega")) or lugar or "No especificado",
        # Unidad
        "precio_unitario_costo": pu_costo,
        "precio_unitario_mercado": pu_mercado,
        "precio_unitario_oferta": pu_oferta,
        "ganancia_por_unidad": ganancia_unidad,
        # Totales
        "valor_contrato_usd": valor,
        "costo_total_usd": costo_total,
        "ganancia_total_usd": ganancia_total,
        "margen_bruto_porcentaje": margen_bruto,
        "costo_factoring_usd": factoring,
        "ganancia_neta_usd": ganancia_neta,
        "margen_neto_porcentaje": margen_neto,
        "precio_oferta_sugerido_usd": oferta_final,
        # Los dos campos de estrategia se SOBRESCRIBEN con la frase calculada
        # en Python. No es que se ignoren lo que dice el modelo: es que lo que
        # dice no puede ser correcto, porque sus cifras se corrigen mas arriba
        # (L792-815). La frase se arma con oferta_final y valor, que son
        # exactamente los numeros que salen en la ficha.
        "estrategia_oferta": estrategia,
        "razonamiento_oferta": estrategia,
        "busquedas_distribuidores": _lista(datos.get("busquedas_distribuidores")),
    # El termino optimizado para el enlace de la ficha. Se limpian comillas y
    # comas antes de guardarlo: si se cuela una comilla en el termino, el
    # enlace de Google se rompe, y con ella la unica ayuda que tiene el
    # usuario para localizar un proveedor.
    "query_google_proveedores": _limpiar_ia(
        str(datos.get("query_google_proveedores") or "").replace('"', "").replace(",", " ")
    )[:90].strip(),
        "distribuidores_candidatos": _candidatos(datos.get("distribuidores_candidatos")),
        "margen_por_distribuidor": _limpiar_ia(datos.get("margen_por_distribuidor"))[:600],
        "nivel_riesgo": _limpiar_ia(datos.get("nivel_riesgo") or "medio").lower()[:10],
        "preguntas_criticas": _lista(datos.get("preguntas_criticas")),
        "observaciones": _limpiar_ia(datos.get("observaciones"))[:400],
        "naics": opp.get("naicsCode", ""),
        "psc": opp.get("classificationCode", ""),
        "solicitation": opp.get("solicitationNumber", "N/A"),
        "set_aside": opp.get("typeOfSetAsideDescription") or opp.get("typeOfSetAside") or "sin set-aside",
        "limite": opp.get("responseDeadLine", "N/A"),
        "posted": opp.get("postedDate", "N/A"),
        "agencia": opp.get("fullParentPathName", "N/A"),
        "contacto": _contacto(opp),
        "ui_link": opp.get("uiLink", ""),
        "title": opp.get("title", ""),
    }


def calcular_estrategia_oferta(presupuesto, precio_ofertar) -> tuple[str, float | None]:
    """
    La frase de estrategia de oferta, calculada aqui y no por el modelo.

    Devuelve (texto, porcentaje). El porcentaje es None cuando no hay con que
    comparar, y en ese caso el texto es el del tope, que no inventa cifras.

    Se calcula DESPUES de la respuesta del modelo, a proposito, con los mismos
    numeros que se imprimen en la ficha. La razon esta en el docstring del
    modulo: antes de la llamada el precio de oferta no existe, y calcularlo
    antes haria que el texto y la tabla contasen cosas distintas.

    El formato del porcentaje es el de la ficha (coma de miles, punto decimal,
    como "$74,500.00"), no el del castellano, porque en esta ficha todos los
    importes van asi y mezclar los dos formatos en la misma pantalla es peor
    que la falta de tilde en "por debajo".
    """
    # Sin numero legible no hay nada que comparar. Se cae al texto del tope en
    # vez de devolver "":
    #
    # - devolver "" dejaba la ficha con la linea de estrategia pelada, sin el
    #   porque entre parentesis, que es justo la parte util
    # - tratar la oferta ausente como 0.0 producia "Ofertar a 0.00 USD...
    #   100% por debajo", que es un absurdo: 100% de descuento significa
    #   regalar el contrato, no que falte el dato
    #
    # Un modelo puede devolver null, "", "N/A" o una lista donde se espera un
    # numero. Nada de eso es un cero.
    try:
        if presupuesto is None or precio_ofertar is None:
            raise TypeError("falta un numero")
        tope = float(presupuesto)
        oferta = float(precio_ofertar)
    except (TypeError, ValueError):
        return (
            "Ofertar al tope del presupuesto estimado del gobierno para "
            "maximizar el margen: el aviso no publica las dos cifras con las "
            "que comparar el descuento.",
            None,
        )

    if tope <= 0:
        # Sin presupuesto no hay descuento que calcular. Se dice el tope, que
        # es lo unico cierto, en vez de inventar un porcentaje.
        return (
            "Ofertar al tope del presupuesto estimado del gobierno para "
            "maximizar el margen, ya que el aviso no publica un presupuesto "
            "con el cual comparar el descuento.",
            None,
        )

    descuento_absoluto = tope - oferta
    porcentaje = round((descuento_absoluto / tope) * 100, 2)

    if porcentaje == 0:
        return (
            "Ofertar al tope del presupuesto estimado del gobierno para "
            "maximizar el margen, ya que este monto iguala el presupuesto "
            "publicado y no deja descuento alguno.",
            porcentaje,
        )

    if porcentaje < 0:
        # Ofertar por encima del techo. La formula daria "-8.5% por debajo",
        # que es un absurdo en la ficha. Se dice lo que pasa y cuanto.
        return (
            f"Ofertar a {oferta:,.2f} USD. ATENCION: este monto supera el "
            f"presupuesto estimado del gobierno ({tope:,.2f} USD) en un "
            f"{abs(porcentaje):.2f}%. Una oferta por encima del techo no "
            f"tiene sentido: revisar el precio antes de presentarla.",
            porcentaje,
        )

    return (
        f"Ofertar a {oferta:,.2f} USD. Con este monto nos mantenemos un "
        f"{porcentaje:.2f}% por debajo del presupuesto estimado del gobierno "
        f"para asegurar alta competitividad y ganar el contrato.",
        porcentaje,
    )


def _candidatos(valor) -> list[dict]:
    """
    Los 3 distribuidores candidatos que pide Gemini, limpios y con tope.

    Se descartan los que llegan sin nombre, porque un candidato sin nombre no
    es un candidato: no hay nada que verificar ni que buscar. El campo
    "verificar" se reconstruye SI FALTA a partir del nombre, con la misma
    logica que usa distribuidores.py. Asi una ficha con el campo vacio
    degrada a "buscar el nombre", que es lo unico que se puede hacer, en vez
    de quedarse sin nada.

    NUNCA se acepta aqui un correo ni un telefono. Aunque el modelo los
    mande, se descartan en silencio: Kyomoto no muestra contactos que no
    puede verificar, y un correo inventado hace que el cliente escriba a
    una empresa equivocada.
    """
    if not isinstance(valor, list):
        return []
    salida: list[dict] = []
    vistos: set[str] = set()
    # Se recorre la lista ENTERA y el tope se aplica al final, no antes.
    # Con `valor[:3]` primero, un modelo que mandara tres entradas malas
    # (una sin nombre, otra repetida, otra con basura) mas una buena
    # quarta se quedaba con cero candidatos, y la ficha caia a la busqueda
    # general habiendole dado una buena. El limite es de lo que SALE bien,
    # no de lo que llega.
    for crudo in valor:
        if len(salida) >= 3:
            break
        if not isinstance(crudo, dict):
            continue
        nombre = _limpiar_ia(str(crudo.get("nombre") or ""))[:80].strip()
        if len(nombre) < 2:
            continue
        clave = nombre.lower()
        if clave in vistos:
            continue
        vistos.add(clave)
        verificar = str(crudo.get("verificar") or "").replace('"', "").replace(",", " ")
        verificar = re.sub(r"\s+", " ", verificar).strip()[:90]
        if not verificar:
            verificar = f"{nombre} {str(crudo.get('tipo') or '').strip()} contact usa".strip()
        salida.append({
            "nombre": nombre,
            "tipo": _limpiar_ia(str(crudo.get("tipo") or ""))[:80].strip(),
            "porque": _limpiar_ia(str(crudo.get("porque") or ""))[:200].strip(),
            "verificar": verificar,
        })
    return salida


def _lista(valor) -> list[str]:
    if valor is None:
        return []
    if isinstance(valor, str):
        partes = [p.strip(" -*\t") for p in re.split(r"[\n;]+", valor)]
        return [_limpiar_ia(p) for p in partes if p][:8]
    if isinstance(valor, (list, tuple)):
        return [_limpiar_ia(v) for v in valor if str(v).strip()][:8]
    return []


def _limpiar_ia(valor) -> str:
    """
    Gemini escribe markdown (*negrita*, _cursiva_, `codigo`) y aunque en modo
    HTML de Telegram no rompe el envio, se ve feo: el usuario lee asteriscos
    sueltos en la pantalla. Aqui se quitan las marcas y se deja el texto plano.
    """
    texto = str(valor or "")
    texto = re.sub(r"\*\*(.+?)\*\*", r"\1", texto)
    texto = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", texto)
    texto = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", texto)
    texto = re.sub(r"`{1,3}([^`\n]+)`{1,3}", r"\1", texto)
    texto = texto.replace("`", "")
    return texto.strip()


def _contacto(opp: dict) -> str:
    poc = opp.get("pointOfContact") or []
    if isinstance(poc, dict):
        poc = [poc]
    for p in poc:
        if isinstance(p, dict) and p.get("fullName"):
            partes = [p["fullName"]]
            if p.get("email"):
                partes.append(p["email"])
            if p.get("phone"):
                partes.append(p["phone"])
            return " | ".join(str(x) for x in partes if x)
    return "N/A"
