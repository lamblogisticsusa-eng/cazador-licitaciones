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
    "producto": "str",
    "especificacion_tecnica_clave": "str",
    "cantidad_estimada": "str",
    "lugar_entrega": "str",
    "valor_contrato_usd": "num|null",
    "costo_proveedor_usd": "num|null",
    "precio_unitario_referencia_usd": "num|null",
    "precio_unitario_sugerido_usd": "num|null",
    "ganancia_neta_usd": "num|null",
    "margen_porcentaje": "num|null",
    "precio_oferta_sugerido_usd": "num|null",
    "estrategia_oferta": "str",
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
  3. Si el valor es menor a USD {config.MIN_USD:,.0f} -> false (no justifica el
     costo de embarque, tramite de exportacion y envio internacional).
  4. Si exige presencia en obra, licencia local, certificacion de contratista
     local o que el proveedor sea residente en EE.UU. -> false.
  5. Si el lugar de entrega es un punto en el extranjero que no sea
     Estados Unidos o un almacen del gobierno -> false.
  6. Si la fecha limite para presentar oferta ya vencio -> false.

SI SE CUMPLE -> viable = false y llena SOLO motivo_descarte con una frase
corta explicando cual regla se rompio.

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

SI ES VIABLE, ENTREGA:
  producto                      : que se compra, en espanol claro
  especificacion_tecnica_clave  : 3-5 datos tecnicos que hay que cumplir
  cantidad_estimada            : ej. "1,200 EA", "40 LOTES", "1 servicio anual"
  valor_contrato_usd            : mejor estimacion. null si no se puede saber
  costo_proveedor_usd           : costo estimado de compra en USA + exportacion
  precio_unitario_referencia_usd: precio de mercado actual por unidad en USA
  precio_unitario_sugerido_usd  : tu precio por unidad para ofertar
  ganancia_neta_usd             : valor - costo proveedor
  margen_porcentaje             : ganancia / valor * 100
  precio_oferta_sugerido_usd    : total a ofertar para ganar con buen margen
  estrategia_oferta             : 2-3 frases de tactica de oferta
  busquedas_distribuidores      : 3-6 terminos EN INGLES para buscar
                                 distribuidores mayoristas en USA (Google,
                                 Thomasnet, Faire, Alibaba US, Global Sources)
  nivel_riesgo                  : bajo | medio | alto
  preguntas_criticas            : 3-5 preguntas que DEBO hacer antes de ofertar
  observaciones                 : 1-2 frases de advertencia

FORMATO DE SALIDA: SOLO un objeto JSON valido, sin markdown, sin ```.
Las claves son exactamente:
{json.dumps(ESQUEMA, indent=2, ensure_ascii=False)}

Ejemplo minimo de forma:
{{"viable": true, "motivo_descarte": "", "producto": "...", "valor_contrato_usd": 45000,
  "costo_proveedor_usd": 31000, "precio_unitario_sugerido_usd": 34.5,
  "ganancia_neta_usd": 14000, "margen_porcentaje": 31.1,
  "precio_oferta_sugerido_usd": 41000, "busquedas_distribuidores": ["wholesale forklift usa"],
  "nivel_riesgo": "medio", "preguntas_criticas": ["..."], "observaciones": ""}}
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
    for intento in range(config.MAX_REINTENTOS):
        try:
            respuesta = cliente.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config=_construir_config(),
            )
            break
        except Exception as e:
            ultimo_error = e
            texto = str(e)

            # --- 429: la credencial es valida, se acabo la cuota. ---
            if "429" in texto or "RESOURCE_EXHAUSTED" in texto:
                intentos_429 += 1
                if intentos_429 > config.GEMINI_REINTENTOS_429:
                    raise GeminiError(
                        f"Se agoto la cuota de Gemini (429 RESOURCE_EXHAUSTED).\n"
                        f"La clave SI funciona: esto es un limite del plan, no del "
                        f"codigo. Opciones:\n"
                        f"  - Bajar MAX_A_GEMINI (ahora {config.MAX_A_GEMINI}) y/o subir "
                        f"GEMINI_PAUSA_SEG (ahora {config.GEMINI_PAUSA_SEG:g}s).\n"
                        f"  - Activar facturacion en https://aistudio.google.com para "
                        f"levantar el limite.\n"
                        f"Este aviso NO se marco como visto: se reintentara en el "
                        f"proximo barrido."
                    ) from e
                log.warning("429 de cuota. Pausa %.0fs.", config.GEMINI_PAUSA_SEG * 3)
                time.sleep(config.GEMINI_PAUSA_SEG * 3)
                continue

            # --- 503: el modelo esta saturado. ---
            if "503" in texto or "UNAVAILABLE" in texto:
                log.warning("503 saturacion del modelo. Pausa %.0fs.", config.GEMINI_PAUSA_SEG * 2)
                time.sleep(config.GEMINI_PAUSA_SEG * 2)
                if intento + 1 >= config.MAX_REINTENTOS:
                    raise GeminiError(
                        f"El modelo {config.GEMINI_MODEL} esta saturado (503). "
                        "Es transitorio: se reintentara en el proximo barrido."
                    ) from e
                continue

            # --- 401: la credencial no es reconocida. ---
            if "401" in texto or "UNAUTHENTICATED" in texto:
                raise GeminiError(
                    "Google respondio 401: no reconoce GEMINI_API_KEY como "
                    "credencial valida para la API de Gemini.\n"
                    "Causas probables, en orden:\n"
                    "1. La clave se copio incompleta (~53 caracteres con un punto; "
                    "un solo caracter mal da 401). Copiala con el boton de copiar.\n"
                    "2. La clave tiene restriccion de IP. Render usa IPs dinamicas, "
                    "asi que una clave restringida a tu IP local falla ahi.\n"
                    "3. La API Generative Language no esta habilitada en el proyecto.\n"
                    "Verificalo sin escribir la clave: python probar_clave.py"
                ) from e

            # --- 404 / modelo retirado. ---
            if "no longer available" in texto.lower() or "not_found" in texto.lower():
                raise GeminiError(
                    f"El modelo '{config.GEMINI_MODEL}' no le sirve a esta cuenta. "
                    f"Usa 'gemini-3.8-flash'. Detalle: {texto[:200]}"
                ) from e

            log.warning("Gemini fallo (intento %s): %s", intento + 1, texto[:200])
            time.sleep(2 ** intento)
    else:
        raise GeminiError(
            f"Gemini fallo tras {config.MAX_REINTENTOS} intentos: "
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

    return {
        "notice_id": opp.get("noticeId"),
        "viable": bool(datos["viable"]),
        "motivo_descarte": _limpiar_ia(datos.get("motivo_descarte"))[:300],
        "sin_descripcion": not bool(descripcion.strip()),
        "producto": _limpiar_ia(datos.get("producto"))[:400],
        "especificacion_tecnica_clave": _limpiar_ia(datos.get("especificacion_tecnica_clave"))[:700],
        "cantidad_estimada": _limpiar_ia(datos.get("cantidad_estimada"))[:80],
        "lugar_entrega": str(datos.get("lugar_entrega") or lugar or "No especificado")[:150],
        "valor_contrato_usd": _num(datos.get("valor_contrato_usd")),
        "costo_proveedor_usd": _num(datos.get("costo_proveedor_usd")),
        "precio_unitario_referencia_usd": _num(datos.get("precio_unitario_referencia_usd")),
        "precio_unitario_sugerido_usd": _num(datos.get("precio_unitario_sugerido_usd")),
        "ganancia_neta_usd": _num(datos.get("ganancia_neta_usd")),
        "margen_porcentaje": _num(datos.get("margen_porcentaje")),
        "precio_oferta_sugerido_usd": _num(datos.get("precio_oferta_sugerido_usd")),
        "estrategia_oferta": _limpiar_ia(datos.get("estrategia_oferta"))[:700],
        "busquedas_distribuidores": _lista(datos.get("busquedas_distribuidores")),
        "nivel_riesgo": str(datos.get("nivel_riesgo") or "medio").lower()[:10],
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
