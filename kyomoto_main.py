"""
kyomoto_main.py - Motor asincrono de Kyomoto (asyncio + aiohttp).

QUE ES ESTE FICHERO, Y QUE NO ES
================================
Es un modulo NUEVO y aditivo. No reemplaza a main.py, scanner.py, sam_api.py,
filters.py ni a ningun modulo que ya esta en produccion: ninguno de ellos se
toca, se borra ni se refactoriza aqui. Su proposito es el rastreo EN TIEMPO REAL
de dos cosas que el escaneo programado no cubre:

  1) ADJUDICACIONES: si el gobierno federal le adjudica un contrato a nuestra
     entidad (UEI NEXGNXGF5E38), llegar la alerta a Telegram el mismo dia.
  2) OPORTUNIDADES COTS: micro-compras (< USD 10.000) y licitaciones bajo el
     umbral SAT (< USD 250.000), con los filtros de notice type, set-aside y
     la practica COTS / drop-shipping (exencion del Non-Manufacturer Rule).

Se puede ejecutar solo (`python kyomoto_main.py`) o importarse desde otro
modulo. No arranca nada al importarse.

CREDENCIALES
============
SAM_API_KEY, TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID se leen EXCLUSIVAMENTE de
variables de entorno del sistema con os.getenv(). No hay ninguna clave escrita
en este fichero, ni en config.py, ni en ningun otro sitio del repositorio.

RESILIENCIA
===========
429 (cuota), 500, 502, 503 y 504 y errores de red se reintentan con backoff
exponencial (base * 2^n, tope configurable) y respetando el header Retry-After
cuando SAM.gov lo manda. Un fallo persistente NO detiene el bucle: se registra,
se degrada y se sigue.

DEGRADACION GRACIOSA
====================
Si el resumen con IA (o cualquier parser) falla, el aviso sale a Telegram con
los datos crudos esenciales en vez de lanzar una excepcion fatal. El objetivo
es que un problema de un modulo nunca cueste la deteccion de otro.

FORMATO DE FECHAS
=================
SAM.gov exige MM/DD/YYYY con diagonales (en locale es-419 strftime devuelve
09-23-2026 y la API responde 400). Se replica aqui el mismo criterio que
sam_api._fecha, y nunca se pagina con offset (offset esta acotado y devuelve 0
mas alla de cierto punto): se trocea la ventana por fecha.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

log = logging.getLogger("kyomoto.main")

# =====================================================================
# CREDENCIALES (solo os.getenv)
# =====================================================================
SAM_API_KEY = os.getenv("SAM_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# UEI de L.A.M.B. Logistics. Se puede sobreescribir por entorno en pruebas.
UEI = os.getenv("KYOMOTO_UEI", "NEXGNXGF5E38").strip()

# Endpoints. Los defaults son los oficiales; quedan configurables porque la
# ruta de Contract Awards varia por entorno y hay que poder apuntarla sin
# recompilar.
AWARDS_URL = os.getenv(
    "KYOMOTO_AWARDS_URL",
    "https://api.sam.gov/prod/awards/v1/api/search").strip()
OPPORTUNITIES_URL = os.getenv(
    "KYOMOTO_OPPORTUNITIES_URL",
    "https://api.sam.gov/prod/opportunities/v2/search").strip()
# Nombre del parametro con el que se filtra por UEI.
UEI_PARAM = os.getenv("KYOMOTO_UEI_PARAM", "uei").strip() or "uei"

# Estado de deduplicacion (evita reenviar la misma adjudicacion en cada ciclo).
STATE_FILE = Path(os.getenv("KYOMOTO_STATE_FILE", "kyomoto_state.json"))

# Backoff exponencial: 429/500/503 de SAM.gov o de Telegram.
BACKOFF_BASE = float(os.getenv("KYOMOTO_BACKOFF_BASE", "5"))
BACKOFF_MAX = float(os.getenv("KYOMOTO_BACKOFF_MAX", "120"))
BACKOFF_INTENTOS = int(os.getenv("KYOMOTO_BACKOFF_INTENTOS", "5"))

# Intervalos de los dos ciclos, en segundos.
INTERVALO_ADJ = int(os.getenv("KYOMOTO_INTERVALO_ADJ", "3600"))
INTERVALO_OPP = int(os.getenv("KYOMOTO_INTERVALO_OPP", "7200"))

# Umbrales del modelo economico (micro-compra y SAT).
UMBRAL_MICRO = float(os.getenv("KYOMOTO_UMBRAL_MICRO", "10000"))
UMBRAL_SAT = float(os.getenv("KYOMOTO_UMBRAL_SAT", "250000"))

# Dry-run: escribe en el log en vez de mandar a Telegram. Sirve para las
# pruebas de fuego en vivo de esta semana sin ensuciar el canal real.
# Se relee en cada envio para que --dry-run (que se define en main()) surta
# efecto aunque el modulo ya se haya importado.
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "si", "s")


def _es_dry_run() -> bool:
    """True si no hay que enviar a Telegram (dry-run activo)."""
    if DRY_RUN:
        return True
    return os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "si", "s")

# Familias PSC que el negocio trata como COTS industrial / ferreteria
# comercial (rodamientos, valvulas, empaquetaturas, mangueras, cables).
# Se toman de config cuando esta disponible para no duplicar la regla; si el
# modulo se ejecuta en solitario, se usa el mismo listado.
try:
    from config import PSC_COTS_INDUSTRIAL  # type: ignore
except Exception:  # pragma: no cover - entorno sin config.py
    PSC_COTS_INDUSTRIAL = ("43", "47", "48", "51", "53")

# Set-asides que este modulo acepta: sin set-aside (unrestricted) y los de
# small business. Es un filtro de pantalla, no un veto: lo que se descarta de
# verdad lo decide el escaneo programado con su propia logica.
SETASIDE_PERMITIDOS = {
    s.strip().upper()
    for s in os.getenv(
        "KYOMOTO_SETASIDE_PERMITIDOS",
        "NA,UNRESTRICTED,SMALL BUSINESS,TOTAL SMALL BUSINESS,SBA 8(A),"
        "8(A),SDVOSB,HUBZONE,WOSB,EDWOSB").split(",")
    if s.strip()
}

# Palabras que indican articulo COTS comercial en el titulo (drop-shipping
# viable: se compra a un distribuidor en USA y se envia, sin fabricar).
PALABRAS_COTS = (
    "bearing", "valve", "gasket", "hose", "cable", "seal", "fastener",
    "bolt", "tool", "fitting", "coupling", "filter", "pump", "motor",
    "switch", "connector", "hardware", "consumable", "supply",
)


class KyomotoError(Exception):
    """Fallo controlado de un modulo. Nunca debe tumbar el bucle."""


# =====================================================================
# UTILIDADES DE FORMATO (replican el criterio de sam_api.py)
# =====================================================================
def _fecha(dt: datetime) -> str:
    """SAM.gov exige MM/DD/YYYY con diagonales. Nunca to_native de Windows."""
    return f"{dt.month:02d}/{dt.day:02d}/{dt.year:04d}"


def _campo(datos: dict, *claves: str, por_defecto: str = "N/A") -> str:
    """
    Lee el primer campo presente entre varios nombres candidatos.

    Los esquemas de los endpoints de SAM.gov cambian entre versiones y entre
    entornos (contractId / contract_id / awardIdPIID, uiLink / ui_link / link).
    En vez de asumir uno solo y fallar en produccion, se prueban en orden y se
    devuelve "N/A" si ninguno viene: mejor un dato ausente visible que un
    aviso entero perdido.
    """
    if not isinstance(datos, dict):
        return por_defecto
    for clave in claves:
        valor = datos.get(clave)
        if valor is None:
            continue
        texto = str(valor).strip()
        if texto and texto.lower() not in ("none", "null", "n/a"):
            return texto
    return por_defecto


def _campo_num(datos: dict, *claves: str) -> float | None:
    """Devuelve el primer valor numerico legible, o None si no hay."""
    for clave in claves:
        valor = datos.get(clave) if isinstance(datos, dict) else None
        if valor is None:
            continue
        try:
            return float(str(valor).replace(",", "").replace("$", "").strip())
        except (ValueError, TypeError):
            continue
    return None


def _usd(valor) -> str:
    """Formatea un monto con miles. Si no se puede leer, lo deja como esta."""
    if valor is None:
        return "N/A"
    try:
        return f"{float(valor):,.2f}"
    except (ValueError, TypeError):
        texto = str(valor).strip()
        return texto or "N/A"


def _limpiar(texto: str, limite: int = 400) -> str:
    """Una sola linea, sin saltos ni caracteres que rompen el parseo."""
    plano = " ".join(str(texto or "").split())
    if len(plano) > limite:
        plano = plano[:limite].rstrip() + "..."
    return plano or "N/A"


def _escapar_md(texto: str) -> str:
    """
    Escapa los caracteres que el Markdown legado de Telegram interpreta.

    Los datos crudos de SAM.gov traen guiones bajos, asteriscos y corchetes en
    los identificadores y en los SOW. Sin escaparlos, Telegram rechaza el
    mensaje entero por un detalle de formato y la alerta se pierde.
    """
    salida = _limpiar(texto)
    # En el Markdown legado de Telegram SOLO son especiales '_', '*', '`' y '['.
    # Escapar '(' o ')' con barra invertida hace que el caracter salga literal
    # en el mensaje, asi que se dejan tal cual.
    for ch in ("_", "*", "`", "["):
        salida = salida.replace(ch, "\\" + ch)
    return salida


def _extraer_usd(texto: str) -> float | None:
    """
    Busca un monto en USD en un texto libre (titulo, descripcion).

    Es la misma idea que usa el escaneo programado para el tope de USD, pero
    aqui es solo una PANTALLA: si no se encuentra monto, el aviso no se
    descarta, se etiqueta como "SIN MONTO DECLARADO". Descartar por no saber el
    precio seria perder licitaciones que el escaneo normal si toma.
    """
    if not texto:
        return None
    patrones = (
        r"(?:usd|us\$|dollars?|\$)\s*([\d,]+(?:\.\d{1,2})?)",
        r"([\d,]+(?:\.\d{1,2})?)\s*(?:usd|us\$)",
        r"not\s+to\s+exceed\s+([\d,]+(?:\.\d{1,2})?)",
    )
    for patron in patrones:
        m = re.search(patron, str(texto), flags=re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except (ValueError, TypeError):
                continue
    return None


# =====================================================================
# BACKOFF EXPONENCIAL COMPARTIDO
# =====================================================================
async def _dormir_backoff(intento: int, respuesta=None) -> None:
    """
    Espera exponencial (base * 2^n, tope BACKOFF_MAX).

    Si SAM.gov manda Retry-After, se respeta aunque sea mayor: el servidor
    federal ya dijo cuanto hay que esperar, y desobedecerlo solo provoca otro
    429.
    """
    espera = min(BACKOFF_BASE * (2 ** intento), BACKOFF_MAX)
    if respuesta is not None:
        try:
            ra = respuesta.headers.get("Retry-After")
            if ra:
                espera = max(espera, float(ra))
        except (ValueError, TypeError, AttributeError):
            pass
    await asyncio.sleep(espera)


async def _get(session: aiohttp.ClientSession, url: str, params: dict,
               *, nombre: str = "SAM.gov") -> dict:
    """
    GET con backoff exponencial sobre 429/500/502/503/504 y errores de red.

    Devuelve el JSON ya decodificado. Lanza KyomotoError en fallos no
    transitorios (400/401/403) o cuando se agotan los reintentos: el llamante
    decide si degrada o se rinde.
    """
    ultimo = None
    for intento in range(max(1, BACKOFF_INTENTOS)):
        try:
            timeout = aiohttp.ClientTimeout(total=45)
            async with session.get(url, params=params, timeout=timeout) as r:
                if r.status == 200:
                    try:
                        return await r.json(content_type=None)
                    except Exception as e:  # noqa: BLE001 - se traduce al usuario
                        raise KyomotoError(
                            f"{nombre} devolvio algo que no es JSON: {e}")
                if r.status in (429, 500, 502, 503, 504):
                    ultimo = f"HTTP {r.status}"
                    log.warning("%s: HTTP %s (intento %d/%d), backoff",
                                nombre, r.status, intento + 1, BACKOFF_INTENTOS)
                    await _dormir_backoff(intento, r)
                    continue
                cuerpo = (await r.text())[:200]
                raise KyomotoError(f"{nombre}: HTTP {r.status}: {cuerpo}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            ultimo = str(e)
            log.warning("%s: error de red (%s), backoff", nombre, e)
            await _dormir_backoff(intento, None)
    raise KyomotoError(f"{nombre} fallo tras {BACKOFF_INTENTOS} intentos: {ultimo}")


# =====================================================================
# ESTADO DE DEDUPLICACION
# =====================================================================
def _cargar_estado() -> dict:
    """Lee el estado de avisos ya mandados. Si no existe, empieza vacio."""
    try:
        datos = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(datos, dict):
            return {"adjudicaciones": [], "oportunidades": []}
        datos.setdefault("adjudicaciones", [])
        datos.setdefault("oportunidades", [])
        return datos
    except (OSError, ValueError):
        return {"adjudicaciones": [], "oportunidades": []}


def _guardar_estado(estado: dict) -> None:
    """
    Guarda el estado de forma atomica.

    Se escribe a un fichero temporal y se renombra: si el proceso muere a
    mitad de escritura, el estado queda legible en vez de corrupto, y el
    proximo ciclo no vuelve a mandar avisos ya entregados.
    """
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        log.warning("No se pudo guardar el estado (%s); el proximo ciclo "
                    "podria repetir avisos ya mandados", e)


# =====================================================================
# TELEGRAM
# =====================================================================
async def enviar_telegram(session: aiohttp.ClientSession, texto: str,
                          *, parse_mode: str = "Markdown") -> bool:
    """
    Envia un mensaje al canal con backoff. En DRY_RUN solo lo registra.

    Nunca lanza: si Telegram falla, se avisa en el log y se sigue. Un aviso
    perdido por un 429 de Telegram no justifica frenar el rastreo.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("No hay TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID: no se envia.")
        return False
    if _es_dry_run():
        log.info("[DRY_RUN] Telegram no recibe el aviso:\n%s", texto)
        return True

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    for intento in range(max(1, BACKOFF_INTENTOS)):
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with session.post(url, json=payload, timeout=timeout) as r:
                if r.status == 200:
                    return True
                cuerpo = (await r.text())[:200]
                if r.status in (429, 500, 502, 503, 504):
                    log.warning("Telegram: HTTP %s (intento %d), backoff",
                                r.status, intento + 1)
                    await _dormir_backoff(intento, r)
                    continue
                log.error("Telegram rechazo el aviso: HTTP %s %s", r.status, cuerpo)
                return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Telegram: error de red (%s), backoff", e)
            await _dormir_backoff(intento, None)
    log.error("Telegram no respondio tras %d intentos; aviso no entregado.",
              BACKOFF_INTENTOS)
    return False


# =====================================================================
# 1) MODULO DE ADJUDICACIONES Y ENLACES (Award Tracking & Deep Links)
# =====================================================================
def _extraer_lista(datos) -> list[dict]:
    """
    Encuentra la lista de registros en la respuesta, sea cual sea el contenedor.

    Los endpoints de awards responden con contractAwardList, results, data o
    una lista a secas segun la version. Se prueban todos y, si ninguno
    coincide, se devuelve vacio en vez de reventar.
    """
    if isinstance(datos, list):
        return [x for x in datos if isinstance(x, dict)]
    if not isinstance(datos, dict):
        return []
    for clave in ("contractAwardList", "contractAwards", "awards",
                  "results", "data", "records", "opportunities", "notices"):
        valor = datos.get(clave)
        if isinstance(valor, list):
            return [x for x in valor if isinstance(x, dict)]
    return []


def normalizar_adjudicacion(crudo: dict) -> dict:
    """
    Normaliza un registro crudo a los cinco campos obligatorios.

    contract_id, agency_name, award_amount, description_summary y
    sam_gov_url. Si un campo opcional viene vacio, se usa "N/A".
    """
    contract_id = _campo(crudo, "contractId", "contract_id", "awardIdPIID",
                         "piid", "contractAwardUniqueKey", "id")
    agency_name = _campo(crudo, "agencyName", "agency_name",
                         "awardingAgencyName", "awardingAgency",
                         "awardingSubAgencyName")
    monto = _campo_num(crudo, "awardAmount", "award_amount",
                       "obligationAmount", "totalValue", "estimatedValue")
    description_summary = _limpiar(
        _campo(crudo, "descriptionSummary", "description_summary",
               "description", "sow", "objectOfContract", "awardDescription",
               "contractObject"), 500)
    sam_gov_url = _campo(crudo, "uiLink", "ui_link", "link", "url", "samGovUrl")
    if sam_gov_url == "N/A":
        # Respaldo honesto: la pagina de busqueda oficial de SAM.gov con el
        # numero de contrato. No es el registro directo, pero si es un enlace
        # real que lleva al usuario al expediente.
        sam_gov_url = f"https://sam.gov/search/?index=awards&q={contract_id}"
    return {
        "contract_id": contract_id,
        "agency_name": agency_name,
        "award_amount": _usd(monto) if monto is not None else "N/A",
        "description_summary": description_summary,
        "sam_gov_url": sam_gov_url,
        "uei": _campo(crudo, "uei", "awardeeUEI", "recipientUEI", por_defecto=UEI),
    }


def texto_adjudicacion(a: dict) -> str:
    """
    Alerta estructurada. El formato es EXACTO el que se pidio, con "N/A" en
    los campos opcionales vacios.
    """
    return (
        "🏆 ¡CONTRATO ADJUDICADO A L.A.M.B LOGISTICS!\n"
        "------------------------------------------\n"
        f"• 📄 ID Contrato: `{_escapar_md(a['contract_id'])}`\n"
        f"• 🏛️ Agencia: {_escapar_md(a['agency_name'])}\n"
        f"• 💰 Monto Total: `${_escapar_md(a['award_amount'])} USD`\n"
        f"• 📦 Objeto / SOW: {_escapar_md(a['description_summary'])}\n"
        "\n"
        f"🔗 [Ver Contrato Oficial en SAM.gov]({a['sam_gov_url']})\n"
        "\n"
        "⚡ *Paso Siguiente:* Iniciar Orden de Compra y Factoring."
    )


async def consultar_adjudicaciones(session: aiohttp.ClientSession) -> list[dict]:
    """
    Consulta Contract Awards por UEI (filtro primario: uei = NEXGNXGF5E38).

    Devuelve adjudicaciones normalizadas, ya deduplicadas por contract_id.
    Un fallo de la API se registra y se devuelve lista vacia: el ciclo
    siguiente reintenta, sin detener el resto del rastreo.
    """
    params = {
        "api_key": SAM_API_KEY,
        UEI_PARAM: UEI,
        "limit": int(os.getenv("KYOMOTO_AWARDS_LIMIT", "25")),
        "offset": 0,
    }
    try:
        datos = await _get(session, AWARDS_URL, params, nombre="SAM.gov awards")
    except KyomotoError as e:
        log.error("consultar_adjudicaciones: %s", e)
        return []
    crudos = _extraer_lista(datos)
    return [normalizar_adjudicacion(c) for c in crudos]


# =====================================================================
# 2) MODULO DE OPORTUNIDADES COTS (Opportunity Screening)
# =====================================================================
def es_cots(opp: dict) -> bool:
    """
    True si el aviso parece articulo COTS comercial (drop-shipping viable).

    Dos senales combinadas: el PSC pertenece a una familia industrial/comercial
    (43xx/47xx/48xx/51xx/53xx: rodamientos, valvulas, empaquetaturas,
    mangueras, cables, ferreteria) o el titulo trae palabras de producto
    comercial. No es un veto: sirve para priorizar y para etiquetar el aviso.
    """
    psc = str(opp.get("classificationCode") or opp.get("psc") or "").strip()
    if len(psc) == 4 and psc.isdigit() and psc[:2] in PSC_COTS_INDUSTRIAL:
        return True
    titulo = str(opp.get("title") or "").lower()
    return any(p in titulo for p in PALABRAS_COTS)


def set_aside_permitido(opp: dict) -> bool:
    """
    True si el set-aside esta entre los permitidos (unrestricted o small
    business). Un set-aside distinto (por ejemplo, una reserva para una
    categoria a la que no aplicamos) se descarta aqui.
    """
    valor = str(opp.get("typeOfSetAside") or opp.get("setAside") or "").strip()
    if not valor:
        return True  # sin set-aside = unrestricted
    return valor.upper() in SETASIDE_PERMITIDOS


def clasificar_monto(monto: float | None) -> str:
    """Etiqueta MICRO (<10k) / SAT (<250k) / SIN MONTO DECLARADO."""
    if monto is None:
        return "SIN MONTO DECLARADO"
    if monto < UMBRAL_MICRO:
        return f"MICRO-COMPRA (< USD {UMBRAL_MICRO:,.0f})"
    if monto < UMBRAL_SAT:
        return f"BAJO SAT (< USD {UMBRAL_SAT:,.0f})"
    return "FUERA DE UMBRAL"


def normalizar_oportunidad(crudo: dict) -> dict:
    """Normaliza un aviso a los campos que la alerta necesita."""
    notice_id = _campo(crudo, "noticeId", "notice_id", "id",
                       "solicitationNumber")
    titulo = _limpiar(_campo(crudo, "title", "noticeTitle"), 220)
    agencia = _campo(crudo, "agencyName", "agency", "awardingAgencyName")
    url = _campo(crudo, "uiLink", "ui_link", "link", "url")
    if url == "N/A":
        url = f"https://sam.gov/search/?index=opps&q={notice_id}"
    descripcion = _limpiar(_campo(crudo, "description", "solicitationText",
                                  "noticeText"), 400)
    monto = _campo_num(crudo, "estimatedValue", "awardAmount",
                       "estimatedAwardAmount", "obligationAmount")
    if monto is None:
        monto = _extraer_usd(f"{titulo} {descripcion}")
    return {
        "notice_id": notice_id,
        "title": titulo,
        "agency": agencia,
        "set_aside": _campo(crudo, "typeOfSetAside", "setAside",
                            por_defecto="Unrestricted"),
        "monto": monto,
        "categoria": clasificar_monto(monto),
        "cots": es_cots(crudo),
        "url": url,
        "descripcion": descripcion,
    }


def texto_oportunidad(o: dict) -> str:
    """Alerta estructurada de una oportunidad COTS."""
    categoria = o["categoria"]
    etiqueta_cots = " · COTS / drop-shipping" if o["cots"] else ""
    monto = (_usd(o["monto"]) + " USD" if o["monto"] is not None
             else "SIN MONTO DECLARADO")
    return (
        "🔔 NUEVA OPORTUNIDAD COTS EN SAM.GOV\n"
        "------------------------------------------\n"
        f"• 📄 Notice ID: `{_escapar_md(o['notice_id'])}`\n"
        f"• 📝 Título: {_escapar_md(o['title'])}\n"
        f"• 🏛️ Agencia: {_escapar_md(o['agency'])}\n"
        f"• 💰 Monto: `{_escapar_md(monto)}`\n"
        f"• 📦 Set-Aside: {_escapar_md(o['set_aside'])}\n"
        f"• 🏷️ Clasificación: {_escapar_md(categoria)}{etiqueta_cots}\n"
        "\n"
        f"🔗 [Ver Aviso Oficial en SAM.gov]({o['url']})\n"
        "\n"
        "⚡ *Siguiente paso:* Cotizar con distribuidor y evaluar margen neto."
    )


async def consultar_oportunidades(session: aiohttp.ClientSession) -> list[dict]:
    """
    Consulta Opportunities (v2/search) con los filtros de pantalla.

    Filtros aplicados:
      * notice type: Combined Synopsis/Solicitation (ptype=o, que es como el
        escaneo programado ya pide los avisos de oportunidades);
      * set-aside: small business o unrestricted;
      * COTS: se prioriza y etiqueta el articulo comercial (exencion del
        Non-Manufacturer Rule); si no es COTS se mantiene en la lista pero sin
        la etiqueta, porque el filtro de verdad esta en el escaneo programado.

    El tope de USD se aplica solo cuando el monto es conocido; un aviso sin
    monto declarado NO se descarta (se etiqueta SIN MONTO DECLARADO).
    """
    hoy = datetime.now(timezone.utc)
    params = {
        "api_key": SAM_API_KEY,
        "postedFrom": _fecha(hoy - timedelta(days=int(
            os.getenv("KYOMOTO_DIAS_VENTANA", "14")))),
        "postedTo": _fecha(hoy),
        "limit": int(os.getenv("KYOMOTO_OPP_LIMIT", "1000")),
        "offset": 0,
        "ptype": "o",
    }
    try:
        datos = await _get(session, OPPORTUNITIES_URL, params,
                           nombre="SAM.gov opportunities")
    except KyomotoError as e:
        log.error("consultar_oportunidades: %s", e)
        return []
    candidatas = []
    for crudo in _extraer_lista(datos):
        if not set_aside_permitido(crudo):
            continue
        oportunidad = normalizar_oportunidad(crudo)
        if (oportunidad["monto"] is not None
                and oportunidad["monto"] > UMBRAL_SAT):
            continue  # por encima del umbral SAT: fuera de este modulo
        candidatas.append(oportunidad)
    candidatas.sort(key=lambda x: (not x["cots"], -(x["monto"] or 0)))
    return candidatas


# =====================================================================
# 3) CICLOS Y BUCLE ASINCRONO
# =====================================================================
async def ciclo_adjudicaciones(session: aiohttp.ClientSession,
                               estado: dict) -> int:
    """Un ciclo de rastreo de adjudicaciones. Devuelve cuantas se mandaron."""
    adjudicaciones = await consultar_adjudicaciones(session)
    vistas = set(estado.get("adjudicaciones", []))
    enviadas = 0
    for a in adjudicaciones:
        clave = a["contract_id"]
        if clave in vistas or clave == "N/A":
            continue
        texto = texto_adjudicacion(a)
        if await enviar_telegram(session, texto):
            vistas.add(clave)
            estado["adjudicaciones"] = sorted(vistas)
            _guardar_estado(estado)
            enviadas += 1
    return enviadas


async def ciclo_oportunidades(session: aiohttp.ClientSession,
                              estado: dict) -> int:
    """Un ciclo de rastreo de oportunidades COTS. Devuelve cuantas se mandaron."""
    oportunidades = await consultar_oportunidades(session)
    vistas = set(estado.get("oportunidades", []))
    enviadas = 0
    for o in oportunidades:
        clave = o["notice_id"]
        if clave in vistas or clave == "N/A":
            continue
        texto = texto_oportunidad(o)
        if await enviar_telegram(session, texto):
            vistas.add(clave)
            estado["oportunidades"] = sorted(vistas)
            _guardar_estado(estado)
            enviadas += 1
    return enviadas


async def _bucle(nombre: str, corredor, intervalo: int,
                 session: aiohttp.ClientSession, estado: dict) -> None:
    """
    Bucle de un modulo: corre el primer ciclo ya, y luego cada `intervalo`
    segundos. Un fallo no lo detiene: se registra y se reintenta en el
    proximo tick.
    """
    while True:
        try:
            n = await corredor(session, estado)
            log.info("%s: %d aviso(s) nuevos", nombre, n)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - degradacion graciosa
            log.error("%s: ciclo fallo (%s); se reintenta en %ds",
                      nombre, e, intervalo)
        await asyncio.sleep(intervalo)


async def bucle_principal() -> None:
    """
    Bucle principal: los dos rastreos corren CONCURRENTEMENTE, cada uno con su
    propio intervalo, compartiendo el mismo cliente HTTP y el mismo estado.
    """
    if not SAM_API_KEY:
        log.error("Falta SAM_API_KEY en el entorno: no se puede rastrear.")
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Faltan TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID: los avisos "
                    "quedaran solo en el log (no se envia nada a Telegram).")
    estado = _cargar_estado()
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        log.info("Kyomoto asincrono arrancado (UEI %s, dry_run=%s).",
                 UEI, DRY_RUN)
        tareas = [
            asyncio.create_task(_bucle("adjudicaciones", ciclo_adjudicaciones,
                                       INTERVALO_ADJ, session, estado)),
            asyncio.create_task(_bucle("oportunidades", ciclo_oportunidades,
                                       INTERVALO_OPP, session, estado)),
        ]
        try:
            await asyncio.gather(*tareas)
        except asyncio.CancelledError:
            pass
        finally:
            for t in tareas:
                t.cancel()


def _falta_config() -> bool:
    """True si falta alguna credencial obligatoria."""
    faltan = [n for n, v in (("SAM_API_KEY", SAM_API_KEY),
                             ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
                             ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)) if not v]
    if faltan:
        log.error("Faltan variables de entorno: %s. Configuralas en el sistema "
                  "(Render > Environment) o en un .env local.", ", ".join(faltan))
    return bool(faltan)


def main(argv: list[str] | None = None) -> int:
    """Entrada de linea de comandos. --once hace UN ciclo de cada modulo."""
    parser = argparse.ArgumentParser(
        description="Kyomoto asincrono: adjudicaciones por UEI y "
                    "oportunidades COTS en SAM.gov.")
    parser.add_argument("--once", action="store_true",
                        help="hace UN ciclo de cada modulo y termina")
    parser.add_argument("--dry-run", action="store_true",
                        help="no envia a Telegram, solo registra en el log")
    args = parser.parse_args(argv)
    if args.dry_run:
        os.environ["DRY_RUN"] = "1"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout)
    if _falta_config():
        return 1
    if args.once:
        async def _un_paso():
            estado = _cargar_estado()
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                n1 = await ciclo_adjudicaciones(session, estado)
                n2 = await ciclo_oportunidades(session, estado)
            return n1, n2
        n_adj, n_opp = asyncio.run(_un_paso())
        log.info("Ciclo unico terminado: %d adjudicacion(es), %d oportunidad(es).",
                 n_adj, n_opp)
        return 0
    try:
        asyncio.run(bucle_principal())
    except KeyboardInterrupt:
        log.info("Interrumpido por el usuario.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
