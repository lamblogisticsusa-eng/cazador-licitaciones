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


def _construir_config():
    """Config tolerant: si la version instalada de google-genai no conoce
    thinking_config, se sigue sin el en vez de reventar."""
    from google.genai import types
    base = {
        "temperature": config.GEMINI_TEMPERATURE,
        "response_mime_type": "application/json",
        "max_output_tokens": config.GEMINI_MAX_OUTPUT_TOKENS,
    }
    try:
        return types.GenerateContentConfig(
            **base,
            thinking_config=types.ThinkingConfig(
                thinking_budget=config.GEMINI_THINKING_BUDGET
            ),
        )
    except Exception:
        return types.GenerateContentConfig(**base)


def verificar_api() -> dict:
    """
    Prueba de humo. Recorre la MISMA cadena de modelos que usa analizar(),
    asi que el resultado refleja lo que de verdad pasara en un barrido.
    """
    if not config.GEMINI_API_KEY:
        return {"ok": False, "detalle": "GEMINI_API_KEY no esta configurada"}

    cola = [config.GEMINI_MODEL] + [
        m for m in config.GEMINI_MODELES_ALTERNATIVOS
        if m and m != config.GEMINI_MODEL
    ]
    problemas = []
    for m in cola:
        try:
            cliente = _get_cliente()
            cliente.models.generate_content(
                model=m, contents="Responde solo: OK",
                config={"max_output_tokens": 2048},
            )
            if m == config.GEMINI_MODEL:
                return {"ok": True, "detalle": f"OK - {m} responde", "modelo": m}
            # El principal estaba saturado pero hay respaldo: esto es una
            # nota, no un fallo. El escaneo va a funcionar igual.
            problemas.append(f"{config.GEMINI_MODEL}: 503 saturado")
            return {
                "ok": True,
                "detalle": f"OK via {m} ({config.GEMINI_MODEL} esta saturado)",
                "modelo": m,
                "degradado": True,
                "avisos": problemas,
            }
        except Exception as e:
            texto = str(e)
            if "429" in texto or "RESOURCE_EXHAUSTED" in texto:
                return {
                    "ok": False,
                    "detalle": "Cuota de Google agotada (429). Se reinicia a "
                               "medianoche del Pacifico. La clave SI funciona.",
                    "cuota": True,
                }
            if "401" in texto or "UNAUTHENTICATED" in texto:
                return {"ok": False, "detalle": f"401: {texto[:200]}"}
            problemas.append(f"{m}: 503 saturado")
            continue

    return {
        "ok": False,
        "detalle": "Todos los modelos estan saturados (503): "
                   + ", ".join(problemas),
        "saturado": True,
    }


def _construir_config():
    """Config tolerant: si la version instalada de google-genai no conoce
    thinking_config, se sigue sin el en vez de reventar."""
    from google.genai import types
    base = {
        "temperature": config.GEMINI_TEMPERATURE,
        "response_mime_type": "application/json",
        "max_output_tokens": config.GEMINI_MAX_OUTPUT_TOKENS,
    }
    try:
        return types.GenerateContentConfig(
            **base,
            thinking_config=types.ThinkingConfig(
                thinking_budget=config.GEMINI_THINKING_BUDGET
            ),
        )
    except Exception:
        return types.GenerateContentConfig(**base)


def verificar_api() -> dict:
    """Prueba de humo. Se usa en /selftest."""
    if not config.GEMINI_API_KEY:
        return {"ok": False, "detalle": "GEMINI_API_KEY no configurada"}
    try:
        cliente = _get_cliente()
        cliente.models.generate_content(
            model=config.GEMINI_MODEL,
            contents="Responde solo con la palabra OK",
            config={"max_output_tokens": 2048},
        )
        return {"ok": True, "detalle": f"OK - modelo {config.GEMINI_MODEL} responde"}
    except Exception as e:
        return {"ok": False, "detalle": f"{type(e).__name__}: {str(e)[:300]}"}


def _construir_prompt(opp: dict, descripcion: str, lugar: str) -> str:
    return f"""
Eres Kyomoto, analista senior de abastecimiento del gobierno de EE.UU.
Evalua esta oportunidad de CONTRATACION DE PRODUCTOS FISICOS para mi cliente:
una empresa unipersonal en CHILE que compra en Estados Unidos y despacha
directamente al destino de entrega. NO realiza instalaciones,
NO presta servicios, NO tiene personal en obra.

REGLAS DE ELIMINACION (si se cumple cualquiera, viable = false)
  1. Si es un servicio intangible, consultoria, personal, TI, software,
     mantenimiento, construccion, limpieza, transporte o alquiler -> false.
  2. Si el valor del contrato supera USD {config.TOPE_USD:,.0f} -> false.
  3. Si el valor es menor a USD {config.MIN_USD:,.0f} -> false.
  4. Si exige presencia en obra, licencia local, certificacion de contratista
     local o que el proveedor sea residente en EE.UU. -> false.
  5. Si el lugar de entrega esta fuera de Estados Unidos -> false.
  6. Si la fecha limite para presentar oferta ya vencio -> false.
  7. Si el margen NETO (despues de factoring) queda por debajo de
     {config.MARGEN_NETO_MIN * 100:.0f}% -> false. Es el margen que de
     verdad se lleva el cliente, y por debajo de eso no vale ofertar.

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
  estrategia_oferta             : 2-3 frases de tactica
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
  margen_por_distribuidor       : como cambia la ganancia segun donde se
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


def analizar(opp: dict, descripcion: str, lugar: str = "") -> dict:
    """
    Devuelve el analisis ya normalizado.
    Lanza GeminiError si la API falla (NUNCA confunde fallo con "no viable").
    """
    cliente = _get_cliente()
    prompt = _construir_prompt(opp, descripcion, lugar or filters.lugar_de_entrega(opp))

    ultimo_error = None
    intentos_429 = 0
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
    # Suficientes intentos para recorrer TODA la cola mas margen para los
    # errores que no sean de saturacion.
    total_intentos = max(config.MAX_REINTENTOS, len(cola) + 1)

    for intento in range(total_intentos):
        modelo = cola[indice]
        try:
            respuesta = cliente.models.generate_content(
                model=modelo,
                contents=prompt,
                config=_construir_config(),
            )
            if modelo != config.GEMINI_MODEL:
                log.info("Va con %s: el de verdad iba saturado", modelo)
            break
        except Exception as e:
            ultimo_error = e
            texto = str(e)

            # --- 429: la credencial es valida, se acabo la cuota. ---
            # La cuota es de la CUENTA, no del modelo: cambiar no ayuda.
            if "429" in texto or "RESOURCE_EXHAUSTED" in texto:
                intentos_429 += 1
                if intentos_429 > config.GEMINI_REINTENTOS_429:
                    raise GeminiError(
                        "Se agoto la cuota de Gemini (429 RESOURCE_EXHAUSTED).\n"
                        "La clave SI funciona: esto es un limite del plan, no del "
                        "codigo.\n"
                        f"  - Bajar MAX_A_GEMINI (ahora {config.MAX_A_GEMINI}) y/o "
                        f"subir GEMINI_PAUSA_SEG (ahora {config.GEMINI_PAUSA_SEG:g}s).\n"
                        "  - Activar facturacion en https://aistudio.google.com para "
                        "levantar el limite.\n"
                        "Los avisos NO se marcan como vistos: se reintentan en el "
                        "proximo barrido."
                    ) from e
                log.warning("429 de cuota. Pausa %.0fs.", config.GEMINI_PAUSA_SEG * 3)
                time.sleep(config.GEMINI_PAUSA_SEG * 3)
                continue

            # --- 503: el modelo esta saturado. Probar el siguiente. ---
            if "503" in texto or "UNAVAILABLE" in texto:
                if indice + 1 < len(cola):
                    log.warning("503 en %s. Cambio a %s.", modelo, cola[indice + 1])
                    indice += 1
                    time.sleep(2)
                    continue
                raise GeminiError(
                    "Todos los modelos de Gemini estan saturados (503): "
                    + ", ".join(cola) + ".\n"
                    "Es temporal y NO es tu clave ni tu codigo: Google tiene "
                    "mucha demanda ahora mismo. Kyomoto reintentara en el proximo "
                    "barrido, y lo que no se analice no se pierde."
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
                if indice + 1 < len(cola):
                    indice += 1
                    continue
                raise GeminiError(
                    f"El modelo '{modelo}' no existe o no esta disponible para esta "
                    f"cuenta. Prueba: python sondear_modelos.py"
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
        "precio_oferta_sugerido_usd": _num(datos.get("precio_oferta_sugerido_usd")) or valor,
        "estrategia_oferta": _limpiar_ia(datos.get("estrategia_oferta"))[:700],
        "razonamiento_oferta": _limpiar_ia(datos.get("razonamiento_oferta"))[:500],
        "busquedas_distribuidores": _lista(datos.get("busquedas_distribuidores")),
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
