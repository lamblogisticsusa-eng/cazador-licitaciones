import io
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask
from google import genai
from google.genai import types
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# ---------------------------------------------------------
# CONFIGURACIÓN DE LOGGING
# ---------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("LAMB_Logistics_Bot")

# ---------------------------------------------------------
# SERVIDOR FLASK (Keep-Alive para Render / Cloud Services)
# ---------------------------------------------------------
flask_app = Flask(__name__)


@flask_app.route("/")
def health_check():
    return (
        "<h3>Bot Cazador de Licitaciones L.A.M.B. Logistics LLC</h3>"
        "<p>Estado: Activo y Monitoreando SAM.gov 24/7</p>",
        200,
    )


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)


# ---------------------------------------------------------
# VARIABLES DE ENTORNO Y ESTADO GLOBAL
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SAM_API_KEY = os.getenv("SAM_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Control de duplicados y persistencia en memoria
LICITACIONES_NOTIFICADAS = set()
LICITACIONES_POSTULADAS = set()
MEMORIA_LICITACIONES = {}

# Inicialización del cliente de IA (Google Gemini API)
ai_client = None
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("Cliente de Gemini API inicializado correctamente.")
    except Exception as e:
        logger.error(f"Error crítico al inicializar Gemini API: {e}")
else:
    logger.warning("GEMINI_API_KEY no detectada. El análisis con IA estará desactivado.")

# ---------------------------------------------------------
# GENERADOR DE DOCUMENTOS PDF (ReportLab)
# ---------------------------------------------------------


def generar_pdf_po(lic_data):
    """Genera una Purchase Order (PO) en PDF lista para enviar al proveedor."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # Encabezado Empresa
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(colors.HexColor("#1A365D"))  # Azul corporativo
    c.drawString(50, height - 50, "L.A.M.B. LOGISTICS LLC")

    c.setFont("Helvetica", 9)
    c.setFillColor(colors.black)
    c.drawString(50, height - 65, "Government Contracting & Supply Chain Division")
    c.drawString(50, height - 77, "Email: logistics@lamblogistics.com")

    # Datos de la Orden de Compra
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(colors.HexColor("#2B6CB0"))
    c.drawString(380, height - 50, "PURCHASE ORDER")

    c.setFont("Helvetica", 10)
    c.setFillColor(colors.black)
    c.drawString(380, height - 67, f"PO Date: {datetime.now().strftime('%Y-%m-%d')}")
    c.drawString(
        380,
        height - 80,
        f"Solicitation Ref: {lic_data.get('solicitation_number', 'N/A')}",
    )

    # Línea Divisoria
    c.setStrokeColor(colors.HexColor("#CBD5E0"))
    c.setLineWidth(1)
    c.line(50, height - 95, width - 50, height - 95)

    # Contenido del Producto
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 120, "1. Specification & Quantities:")

    c.setFont("Helvetica", 10)
    y_pos = height - 140
    c.drawString(65, y_pos, f"• Item Description: {lic_data.get('producto', 'N/A')}")
    y_pos -= 18
    c.drawString(
        65,
        y_pos,
        f"• Total Quantity: {lic_data.get('cantidad', 1):,} {lic_data.get('unidad', 'Units')}",
    )
    y_pos -= 18
    c.drawString(
        65,
        y_pos,
        f"• Target Unit Cost: ${lic_data.get('target_cost', 0):,.2f} USD",
    )
    y_pos -= 18
    c.drawString(
        65,
        y_pos,
        f"• Delivery ZIP Code: {lic_data.get('zip_code', 'Destination Point')}",
    )

    # Instrucciones de Entrega / Drop-Shipping
    y_pos -= 35
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y_pos, "2. Logistics & Delivery Instructions:")

    c.setFont("Helvetica", 9)
    y_pos -= 20
    instructions = [
        "a. Direct drop-shipment required to final Government delivery point specified in RFQ.",
        "b. Vendor must include L.A.M.B. Logistics Packing Slip with shipment (No pricing disclosure).",
        "c. Outer packaging must clearly reference the Solicitation Number listed above.",
        "d. Notify tracking details immediately upon dispatch to logistics@lamblogistics.com.",
    ]
    for line in instructions:
        c.drawString(65, y_pos, line)
        y_pos -= 15

    # Pie de página
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.gray)
    c.drawString(
        50, 40, "L.A.M.B. Logistics LLC — Proprietary & Confidential Document"
    )

    c.showPage()
    c.save()
    buffer.seek(0)
    buffer.name = f"Vendor_PO_{lic_data.get('solicitation_number', 'Doc')}.pdf"
    return buffer


def generar_pdf_packing_list(lic_data):
    """Genera una Packing List neutra para enviar al proveedor para el Drop-Shipping."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(colors.HexColor("#1A365D"))
    c.drawString(50, height - 50, "PACKING LIST / SLIP")

    c.setFont("Helvetica", 10)
    c.setFillColor(colors.black)
    c.drawString(50, height - 67, "Prime Contractor: L.A.M.B. LOGISTICS LLC")
    c.drawString(50, height - 80, f"Date: {datetime.now().strftime('%Y-%m-%d')}")

    c.setStrokeColor(colors.HexColor("#CBD5E0"))
    c.setLineWidth(1)
    c.line(50, height - 95, width - 50, height - 95)

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 120, "Shipment Details:")

    c.setFont("Helvetica", 10)
    y_pos = height - 140
    c.drawString(
        65,
        y_pos,
        f"Solicitation Ref: {lic_data.get('solicitation_number', 'N/A')}",
    )
    y_pos -= 18
    c.drawString(65, y_pos, f"Item Description: {lic_data.get('producto', 'N/A')}")
    y_pos -= 18
    c.drawString(
        65,
        y_pos,
        f"Total Quantity: {lic_data.get('cantidad', 1):,} {lic_data.get('unidad', 'Units')}",
    )
    y_pos -= 18
    c.drawString(
        65,
        y_pos,
        f"Destination Zip Code: {lic_data.get('zip_code', 'N/A')}",
    )

    y_pos -= 35
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y_pos, "Receiving Inspection:")
    c.setFont("Helvetica-Oblique", 9)
    y_pos -= 18
    c.drawString(
        65,
        y_pos,
        "Inspection & Final Acceptance: Destination by Authorized Government Personnel.",
    )

    c.showPage()
    c.save()
    buffer.seek(0)
    buffer.name = f"Packing_List_{lic_data.get('solicitation_number', 'Doc')}.pdf"
    return buffer


# ---------------------------------------------------------
# INTELIGENCIA ARTIFICIAL Y ANÁLISIS COTS (GEMINI)
# ---------------------------------------------------------


def limpiar_json_respuesta(texto_raw):
    """Limpia la respuesta de la IA para obtener un JSON válido."""
    texto_limpio = re.sub(r"```json\s*", "", texto_raw, flags=re.IGNORECASE)
    texto_limpio = re.sub(r"```\s*", "", texto_limpio)
    return texto_limpio.strip()


def analizar_licitacion_con_ia(titulo, descripcion):
    """Extrae especificaciones clave y cantidades usando Gemini 2.5 Flash."""
    if not ai_client:
        return {
            "producto": titulo,
            "cantidad": 1,
            "unidad": "Unidades",
            "detalles": "Análisis de IA no disponible.",
        }

    prompt = f"""
    Analiza esta oportunidad de contratación del gobierno federal de EE. UU. orientada a compras COTS (Commercial Off-The-Shelf).
    Título: {titulo}
    Descripción: {descripcion}

    Extrae en formato JSON estricto únicamente:
    1. "producto": Nombre claro y conciso del producto o material en español.
    2. "cantidad": Cantidad numérica total requerida (si es ambigua o no se especifica, pon 1).
    3. "unidad": Unidad de medida (Cajas, Unidades, Paquetes, Kits, Rollos).
    4. "detalles": Un resumen de 1 oración con especificaciones clave o número de parte si aplica.
    """

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(limpiar_json_respuesta(response.text))
        try:
            cant = int(data.get("cantidad", 1))
            data["cantidad"] = cant if cant > 0 else 1
        except (ValueError, TypeError):
            data["cantidad"] = 1
        return data
    except Exception as e:
        logger.warning(f"Error procesando análisis de IA: {e}")
        return {
            "producto": titulo,
            "cantidad": 1,
            "unidad": "Unidades",
            "detalles": "No fue posible extraer detalles automáticos.",
        }


def buscar_distribuidores_locales(producto, zip_code):
    """Usa IA para sugerir proveedores/distribuidores COTS reales en EE. UU."""
    if not ai_client:
        return [
            {
                "nombre": "Grainger Industrial Supply",
                "tel": "(800) 472-4643",
                "web": "grainger.com",
            },
            {
                "nombre": "Fastenal Company",
                "tel": "(800) 416-9400",
                "web": "fastenal.com",
            },
        ]

    prompt = f"""
    Sugiéreme 2 distribuidores o mayoristas comerciales principales en EE. UU. que distribuyan el producto '{producto}' y hagan envíos a la zona del código postal '{zip_code}'.
    Responde ÚNICAMENTE en formato JSON con una lista de objetos conteniendo las llaves: "nombre", "tel", "web".
    """

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(limpiar_json_respuesta(response.text))
        if isinstance(data, list):
            return data[:2]
        return data.get("distribuidores", [])[:2]
    except Exception as e:
        logger.warning(f"Error buscando distribuidores con IA: {e}")
        return [
            {
                "nombre": "Grainger Industrial Supply",
                "tel": "Ver Sitio Web",
                "web": "grainger.com",
            },
            {
                "nombre": "MSC Industrial Supply",
                "tel": "Ver Sitio Web",
                "web": "mscdirect.com",
            },
        ]


# ---------------------------------------------------------
# CONEXIÓN Y ESCANEO EN SAM.GOV
# ---------------------------------------------------------


def obtener_mejores_licitaciones_sam():
    """Consulta la API de SAM.gov filtrando estrictamente contratos de suministros COTS < $250k."""
    if not SAM_API_KEY:
        logger.error("SAM_API_KEY no está configurada.")
        return []

    url = "https://api.sam.gov/prod/opportunities/v2/search"
    hoy = datetime.now(timezone.utc)
    fecha_desde = (hoy - timedelta(days=7)).strftime("%m/%d/%Y")
    fecha_hasta = hoy.strftime("%m/%d/%Y")

    params = {
        "api_key": SAM_API_KEY,
        "postedFrom": fecha_desde,
        "postedTo": fecha_hasta,
        "limit": 100,
        "ptype": "k,o,p",  # Solicitations, Combined Synopsis/Solicitation, Presolicitations
        "is_active": "true",
    }

    logger.info(
        f"Iniciando búsqueda en SAM.gov (Rango: {fecha_desde} - {fecha_hasta})..."
    )

    try:
        response = requests.get(url, params=params, timeout=25)
        if response.status_code != 200:
            logger.error(f"Error API SAM.gov: Código {response.status_code}")
            return []

        data = response.json()
        opps = data.get("opportunitiesData", [])
        candidatas = []

        # Palabras clave a descartar (Servicios complejos u obras civiles)
        excluir_keywords = [
            "construction",
            "installation required",
            "on-site service",
            "janitorial",
            "repair service",
            "maintenance contract",
            "paving",
            "demolition",
        ]

        for opp in opps:
            opp_id = opp.get("noticeId")
            if not opp_id or opp_id in LICITACIONES_NOTIFICADAS:
                continue

            titulo = opp.get("title", "")
            descripcion = opp.get("description", "")
            texto_completo = f"{titulo} {descripcion}".lower()

            # Descartar servicios
            if any(kw in texto_completo for kw in excluir_keywords):
                continue

            # Calcular plazo restante
            response_date_str = opp.get("responseDeadLine")
            dias_restantes = 15
            cierre_str = "A la brevedad"

            if response_date_str:
                try:
                    fecha_cierre = datetime.fromisoformat(
                        response_date_str.replace("Z", "+00:00")
                    )
                    dias_restantes = (fecha_cierre - hoy).days
                    cierre_str = fecha_cierre.strftime("%d/%m/%Y")
                except Exception:
                    pass

            # Filtrar por tiempo límite viable (entre 1 y 60 días)
            if dias_restantes < 1 or dias_restantes > 60:
                continue

            # Presupuesto estimado / Límite SAP ($250k)
            raw_amount = opp.get("award", {}).get("amount")
            if raw_amount:
                try:
                    monto_estimado = float(raw_amount)
                except ValueError:
                    monto_estimado = 35000.0
            else:
                monto_estimado = 35000.0  # Estimado por defecto para SAP COTS

            if monto_estimado > 250000.0:
                continue

            zip_code = opp.get("placeOfPerformance", {}).get("zip", "EE. UU.")

            candidatas.append(
                {
                    "id": opp_id,
                    "titulo": titulo or "Sin título disponible",
                    "descripcion": descripcion or "Sin descripción proporcionada",
                    "solicitation_number": opp.get("solicitationNumber", "N/A"),
                    "agencia": opp.get("department", "Agencia Federal"),
                    "cierre_str": cierre_str,
                    "dias_restantes": dias_restantes,
                    "monto_est": monto_estimado,
                    "zip_code": zip_code,
                    "link": opp.get(
                        "uiLink", f"https://sam.gov/opp/{opp_id}/view"
                    ),
                }
            )

        candidatas.sort(key=lambda x: x["dias_restantes"])
        logger.info(f"Escaneadas exitosamente: {len(candidatas)} licitaciones COTS aptas.")

        return candidatas[:5]

    except Exception as e:
        logger.error(f"Excepción durante la conexión a SAM.gov: {e}")
        return []


def verificar_adjudicaciones_sam():
    """Consulta la API de SAM.gov para verificar si se adjudicó alguna licitación bajo monitoreo."""
    if not SAM_API_KEY or not LICITACIONES_POSTULADAS:
        return []

    url = "https://api.sam.gov/prod/opportunities/v2/search"
    hoy = datetime.now(timezone.utc)
    fecha_desde = (hoy - timedelta(days=14)).strftime("%m/%d/%Y")

    params = {
        "api_key": SAM_API_KEY,
        "postedFrom": fecha_desde,
        "postedTo": hoy.strftime("%m/%d/%Y"),
        "limit": 50,
        "ptype": "a",  # Award Notices
    }

    adjudicadas = []
    try:
        response = requests.get(url, params=params, timeout=20)
        if response.status_code != 200:
            return []

        data = response.json()

        for opp in data.get("opportunitiesData", []):
            sol_num = opp.get("solicitationNumber", "")
            if sol_num in LICITACIONES_POSTULADAS:
                award_data = opp.get("award", {})
                adjudicadas.append(
                    {
                        "solicitation_number": sol_num,
                        "titulo": opp.get("title", "Sin título"),
                        "monto_adjudicado": award_data.get("amount", "N/A"),
                        "adjudicatario": award_data.get("awardee", {}).get(
                            "name", "Contratista Ganador"
                        ),
                        "link": opp.get(
                            "uiLink", f"https://sam.gov/opp/{opp.get('noticeId')}/view"
                        ),
                    }
                )
        return adjudicadas
    except Exception as e:
        logger.warning(f"Error al verificar adjudicaciones: {e}")
        return []


# ---------------------------------------------------------
# LÓGICA PRINCIPAL DE NOTIFICACIÓN
# ---------------------------------------------------------


async def buscar_y_notificar(context: ContextTypes.DEFAULT_TYPE, target_chat_id=None):
    """Ejecuta la búsqueda, analiza oportunidades y envía las alertas estructuradas a Telegram."""
    chat_id = target_chat_id
    if not chat_id and context.job:
        chat_id = context.job.chat_id
    if not chat_id:
        chat_id = TELEGRAM_CHAT_ID

    if not chat_id:
        logger.warning("TELEGRAM_CHAT_ID no configurado. Cancelando envío.")
        return 0

    # 1. Verificar Adjudicaciones en seguimiento
    adjudicaciones = verificar_adjudicaciones_sam()
    for adj in adjudicaciones:
        sol_num = adj["solicitation_number"]
        mensaje_adj = (
            "🏆 **RESULTADO DE ADJUDICACIÓN PUBLICADO** 🏆\n\n"
            f"📋 **Solicitation #:** `{sol_num}`\n"
            f"📌 **Título:** {adj['titulo']}\n"
            f"💰 **Monto Adjudicado:** ${adj['monto_adjudicado']}\n"
            f"🏢 **Ganador:** {adj['adjudicatario']}\n\n"
            f"🔗 [Ver Registro Oficial en SAM.gov]({adj['link']})"
        )
        await context.bot.send_message(
            chat_id=chat_id, text=mensaje_adj, parse_mode="Markdown"
        )
        LICITACIONES_POSTULADAS.discard(sol_num)

    # 2. Buscar Oportunidades Nuevas
    licitaciones = obtener_mejores_licitaciones_sam()

    for lic in licitaciones:
        LICITACIONES_NOTIFICADAS.add(lic["id"])

        ia_data = analizar_licitacion_con_ia(lic["titulo"], lic["descripcion"])
        cantidad = max(1, int(ia_data.get("cantidad", 1)))
        producto = ia_data.get("producto", lic["titulo"])
        unidad = ia_data.get("unidad", "Unidades")

        distribuidores = buscar_distribuidores_locales(producto, lic["zip_code"])

        # Análisis Financiero Automatizado
        monto_total = lic["monto_est"]
        precio_unitario_bid = monto_total / cantidad
        target_cost_unitario = (monto_total * 0.70) / cantidad

        costo_proveedor = monto_total * 0.70
        flete = 400.00
        factoring = monto_total * 0.03
        ganancia_neta = monto_total - (costo_proveedor + flete + factoring)

        # Almacenar en memoria local para generación de PDFs instantánea
        MEMORIA_LICITACIONES[lic["id"]] = {
            "solicitation_number": lic["solicitation_number"],
            "producto": producto,
            "cantidad": cantidad,
            "unidad": unidad,
            "zip_code": lic["zip_code"],
            "target_cost": target_cost_unitario,
        }

        distrib_str = ""
        for idx, dist in enumerate(distribuidores, 1):
            distrib_str += f"{idx}. *{dist.get('nombre')}* | Tel: {dist.get('tel')} | Web: {dist.get('web')}\n"

        mensaje = (
            "🚨 **NUEVA LICITACIÓN DE PRODUCTOS (<$250k)** 🚨\n\n"
            f"📦 **Producto:** {producto}\n"
            f"🔢 **Cantidad:** {cantidad:,} {unidad}\n"
            f"🏛 **Agencia:** {lic['agencia']}\n"
            f"📍 **Entrega (ZIP):** {lic['zip_code']}\n"
            f"📋 **Solicitation #:** `{lic['solicitation_number']}`\n"
            f"⏳ **Cierre:** {lic['cierre_str']} (En {lic['dias_restantes']} días)\n\n"
            "💵 **ANÁLISIS FINANCIERO & TARGET BID**\n"
            f"• Presupuesto Est.: ${monto_total:,.2f} USD\n"
            f"• Precio Bid Unitario Sugerido: **${precio_unitario_bid:,.2f} / unid.**\n"
            f"• Costo Máx. Compra Objetivo: **<=${target_cost_unitario:,.2f} / unid.**\n"
            f"📈 **Ganancia Neta Est.:** `${ganancia_neta:,.2f} USD`\n\n"
            f"🏭 **DISTRIBUIDORES SUGERIDOS:**\n"
            f"{distrib_str}"
        )

        keyboard = [
            [
                InlineKeyboardButton("🔗 Ver en SAM.gov", url=lic["link"]),
                InlineKeyboardButton(
                    "📄 Packing List (PDF)", callback_data=f"pkg_{lic['id']}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "📝 Generar Vendor PO (PDF)", callback_data=f"po_{lic['id']}"
                )
            ],
        ]

        await context.bot.send_message(
            chat_id=chat_id,
            text=mensaje,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        logger.info(f"Notificación enviada correctamente: {lic['id']}")

    return len(licitaciones)


# ---------------------------------------------------------
# COMANDOS Y CALLBACKS DE TELEGRAM
# ---------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensaje de bienvenida y comandos disponibles."""
    await update.message.reply_text(
        "🦅 **Cazador de Licitaciones L.A.M.B. Logistics LLC**\n\n"
        "Sistema automático de monitoreo de oportunidades COTS en SAM.gov.\n\n"
        "**Comandos Disponibles:**\n"
        "• `/scan` - Ejecutar escaneo manual inmediato en SAM.gov\n"
        "• `/on` - Activar el monitoreo automático cada hora\n"
        "• `/off` - Pausar el monitoreo automático\n"
        "• `/postulado <solicitation_num>` - Registrar seguimiento de una oferta presentada\n"
        "• `/rfq <solicitation_num>` - Generar borrador de correo RFQ para distribuidores\n"
        "• `/mis_postulaciones` - Ver la lista de licitaciones bajo seguimiento",
        parse_mode="Markdown",
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ejecuta una búsqueda manual inmediata."""
    await update.message.reply_text("🔎 **Iniciando escaneo manual en SAM.gov...**\nPor favor espera unos segundos.", parse_mode="Markdown")
    
    total = await buscar_y_notificar(context, target_chat_id=update.effective_chat.id)
    
    if total == 0:
        await update.message.reply_text(
            "ℹ️ **Sin novedades:** No se encontraron nuevas licitaciones COTS que cumplan los criterios en este momento.",
            parse_mode="Markdown",
        )


async def cmd_rfq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Genera una plantilla de correo profesional en inglés para solicitar cotización a un proveedor."""
    if not context.args:
        await update.message.reply_text(
            "⚠️ **Uso correcto:** `/rfq <solicitation_number>`", parse_mode="Markdown"
        )
        return

    sol_num = context.args[0].strip()
    mensaje_rfq = (
        "📧 **DRAFT DE SOLICITUD DE COTIZACIÓN (RFQ) PARA PROVEEDOR**\n\n"
        "```text\n"
        f"Subject: Urgent RFQ / Price Quote Request - L.A.M.B. Logistics LLC (Ref: {sol_num})\n\n"
        "Dear Sales Department,\n\n"
        f"L.A.M.B. Logistics LLC is currently preparing a formal proposal for U.S. Federal Solicitation {sol_num}.\n\n"
        "Could you please provide us with your best wholesale pricing, product availability, and lead time for the required items?\n\n"
        "Key Delivery Requirements:\n"
        "• Freight Destination: Specified Drop-shipment Zip Code\n"
        "• Terms: Net 30 / Credit Card\n"
        "• Packing Slip: Must reference Solicitation Number on outer packaging\n\n"
        "Please send your formal quote back at your earliest convenience.\n\n"
        "Best Regards,\n"
        "Purchasing Department\n"
        "L.A.M.B. Logistics LLC\n"
        "logistics@lamblogistics.com\n"
        "```"
    )
    await update.message.reply_text(mensaje_rfq, parse_mode="Markdown")


async def cmd_postulado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registra una licitación para monitoreo de adjudicación."""
    if not context.args:
        await update.message.reply_text(
            "⚠️ **Uso correcto:** `/postulado SPE8E6-26-U-0012`", parse_mode="Markdown"
        )
        return

    sol_num = context.args[0].strip()
    LICITACIONES_POSTULADAS.add(sol_num)
    await update.message.reply_text(
        f"✅ **Licitación registrada para seguimiento:** `{sol_num}`\nEl bot te avisará cuando SAM.gov publique la adjudicación.",
        parse_mode="Markdown",
    )


async def cmd_mis_postulaciones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra la lista de licitaciones bajo seguimiento activo."""
    if not LICITACIONES_POSTULADAS:
        await update.message.reply_text("ℹ️ No tienes licitaciones registradas bajo seguimiento actualmente.")
        return

    lista_str = "\n".join([f"• `{num}`" for num in LICITACIONES_POSTULADAS])
    await update.message.reply_text(
        f"📋 **Licitaciones bajo monitoreo activo:**\n\n{lista_str}",
        parse_mode="Markdown",
    )


def nombre_job(chat_id):
    return f"monitor_{chat_id}"


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enciende el temporizador para escaneo cada hora."""
    chat_id = update.effective_chat.id
    job_name = nombre_job(chat_id)

    if context.job_queue is None:
        await update.message.reply_text("❌ Error: Sistema JobQueue no inicializado.")
        return

    if context.job_queue.get_jobs_by_name(job_name):
        await update.message.reply_text("🟢 El monitoreo automático ya se encuentra **ACTIVO**.", parse_mode="Markdown")
        return

    context.job_queue.run_repeating(
        buscar_y_notificar, interval=3600, first=1, chat_id=chat_id, name=job_name
    )
    await update.message.reply_text(
        "🚀 **Motor activado:** Se realizarán escaneos automáticos en SAM.gov cada 60 minutos.",
        parse_mode="Markdown",
    )


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pausa el temporizador de escaneo."""
    chat_id = update.effective_chat.id
    job_name = nombre_job(chat_id)

    if context.job_queue is None:
        return

    jobs = context.job_queue.get_jobs_by_name(job_name)
    if not jobs:
        await update.message.reply_text("🔴 El monitoreo automático ya se encuentra **DESACTIVADO**.", parse_mode="Markdown")
        return

    for job in jobs:
        job.schedule_removal()
    await update.message.reply_text("⏸ **Motor pausado:** Escaneo automático desactivado.", parse_mode="Markdown")


async def boton_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja las acciones de los botones interactivos (Descarga de PDFs)."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("pkg_"):
        lic_id = data.replace("pkg_", "")
        lic_data = MEMORIA_LICITACIONES.get(
            lic_id,
            {"solicitation_number": lic_id, "producto": "Producto COTS", "cantidad": 1},
        )
        pdf_file = generar_pdf_packing_list(lic_data)
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=pdf_file,
            caption="📄 **Packing List / Slip Generado**",
            parse_mode="Markdown",
        )

    elif data.startswith("po_"):
        lic_id = data.replace("po_", "")
        lic_data = MEMORIA_LICITACIONES.get(
            lic_id,
            {"solicitation_number": lic_id, "producto": "Producto COTS", "cantidad": 1},
        )
        pdf_file = generar_pdf_po(lic_data)
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=pdf_file,
            caption="📝 **Purchase Order (PO) Generada**",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------
# PUNTO DE ENTRADA PRINCIPAL
# ---------------------------------------------------------


def main():
    # Iniciar servidor web Flask en hilo secundario (keep-alive)
    threading.Thread(target=run_flask, daemon=True).start()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN no configurado en variables de entorno.")
        return

    # Inicializar Telegram Application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Registro de Handlers (Comandos y Callbacks)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("on", cmd_on))
    app.add_handler(CommandHandler("off", cmd_off))
    app.add_handler(CommandHandler("rfq", cmd_rfq))
    app.add_handler(CommandHandler("postulado", cmd_postulado))
    app.add_handler(CommandHandler("mis_postulaciones", cmd_mis_postulaciones))
    app.add_handler(CallbackQueryHandler(boton_callback))

    # Tarea repetitiva predeterminada si TELEGRAM_CHAT_ID está configurado
    if TELEGRAM_CHAT_ID:
        job_name = nombre_job(TELEGRAM_CHAT_ID)
        if app.job_queue is not None:
            try:
                target_chat = (
                    int(TELEGRAM_CHAT_ID)
                    if TELEGRAM_CHAT_ID.isdigit() or TELEGRAM_CHAT_ID.startswith("-")
                    else TELEGRAM_CHAT_ID
                )
                app.job_queue.run_repeating(
                    buscar_y_notificar,
                    interval=3600,
                    first=1,
                    chat_id=target_chat,
                    name=job_name,
                )
                logger.info("Tarea de monitoreo por hora iniciada correctamente.")
            except Exception as e:
                logger.error(f"Error al programar la tarea repetitiva: {e}")

    logger.info("Bot Cazador L.A.M.B. Logistics iniciado y escuchando...")
    app.run_polling()


if __name__ == "__main__":
    main()
