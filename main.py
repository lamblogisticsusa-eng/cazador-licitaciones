import io
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask
from google import genai
from google.genai import types
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# ---------------------------------------------------------
# SERVIDOR FLASK (Para mantener activo Render 24/7)
# ---------------------------------------------------------
flask_app = Flask(__name__)


@flask_app.route("/")
def health_check():
    return "Bot Cazador de Licitaciones L.A.M.B. Logistics activo 24/7", 200


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)


# ---------------------------------------------------------
# CONFIGURACIÓN Y VARIABLES DE ENTORNO
# ---------------------------------------------------------
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SAM_API_KEY = os.getenv("SAM_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

LICITACIONES_NOTIFICADAS = set()
LICITACIONES_POSTULADAS = set()
MEMORIA_LICITACIONES = {}  # Guarda los detalles parseados para la generación de documentos

ai_client = None
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"❌ Error al inicializar Gemini API: {e}", flush=True)

# ---------------------------------------------------------
# GENERADOR DE DOCUMENTOS PDF (ReportLab)
# ---------------------------------------------------------


def generar_pdf_po(lic_data):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setFont("Helvetica-Bold", 16)

    c.drawString(50, 750, "L.A.M.B. LOGISTICS LLC")
    c.setFont("Helvetica", 10)
    c.drawString(50, 735, "Government Contracting & Supply Chain Operations")
    c.drawString(50, 720, "Email: logistics@lamblogistics.com")

    c.setFont("Helvetica-Bold", 14)
    c.drawString(400, 750, "PURCHASE ORDER")
    c.setFont("Helvetica", 10)
    c.drawString(400, 735, f"PO Date: {datetime.now().strftime('%Y-%m-%d')}")
    c.drawString(400, 720, f"Ref Solicitation: {lic_data.get('solicitation_number', 'N/A')}")

    c.line(50, 705, 550, 705)

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, 680, "Item Details:")
    c.setFont("Helvetica", 10)
    c.drawString(50, 660, f"Product: {lic_data.get('producto', 'N/A')}")
    c.drawString(50, 645, f"Quantity: {lic_data.get('cantidad', 1)} {lic_data.get('unidad', 'Units')}")
    c.drawString(50, 630, f"Delivery ZIP Code: {lic_data.get('zip_code', 'N/A')}")
    c.drawString(50, 615, f"Target Unit Cost: ${lic_data.get('target_cost', 0):,.2f} USD")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, 580, "Instructions:")
    c.setFont("Helvetica", 9)
    c.drawString(
        50,
        565,
        "1. Direct drop-shipment required to final Government delivery point specified in RFQ.",
    )
    c.drawString(50, 550, "2. Packing list must reference the solicitation number listed above.")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


def generar_pdf_packing_list(lic_data):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setFont("Helvetica-Bold", 16)

    c.drawString(50, 750, "PACKING LIST / SLIP")
    c.setFont("Helvetica", 10)
    c.drawString(50, 735, "L.A.M.B. LOGISTICS LLC - Prime Contractor")
    c.drawString(50, 720, f"Date: {datetime.now().strftime('%Y-%m-%d')}")

    c.line(50, 705, 550, 705)

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, 680, "Shipment Details:")
    c.setFont("Helvetica", 10)
    c.drawString(50, 660, f"Solicitation Number: {lic_data.get('solicitation_number', 'N/A')}")
    c.drawString(50, 645, f"Item Description: {lic_data.get('producto', 'N/A')}")
    c.drawString(50, 630, f"Total Quantity: {lic_data.get('cantidad', 1)} {lic_data.get('unidad', 'Units')}")
    c.drawString(50, 615, f"Destination ZIP: {lic_data.get('zip_code', 'N/A')}")

    c.setFont("Helvetica-Oblique", 9)
    c.drawString(50, 570, "Inspection & Acceptance: Destination")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


# ---------------------------------------------------------
# FUNCIONES IA (GEMINI) Y SAM.GOV
# ---------------------------------------------------------


def analizar_licitacion_con_ia(titulo, descripcion):
    if not ai_client:
        return {
            "producto": titulo,
            "cantidad": 1,
            "unidad": "Unidades",
            "detalles": "Análisis de IA no disponible.",
        }

    prompt = f"""
    Analiza esta licitación del gobierno federal de EE. UU. orientada a compras COTS (Commercial Off-The-Shelf).
    Título: {titulo}
    Descripción: {descripcion}

    Extrae en formato JSON únicamente:
    1. "producto": Nombre claro del producto solicitado.
    2. "cantidad": Cantidad numérica total (si no es clara, asigna 1).
    3. "unidad": Unidad de medida (Cajas, Unidades, Paquetes, Kits).
    4. "detalles": Resumen de 1 oración con especificaciones clave.
    """

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text)
        try:
            cant = int(data.get("cantidad", 1))
            data["cantidad"] = cant if cant > 0 else 1
        except (ValueError, TypeError):
            data["cantidad"] = 1
        return data
    except Exception as e:
        print(f"⚠️ Error en análisis Gemini: {e}", flush=True)
        return {
            "producto": titulo,
            "cantidad": 1,
            "unidad": "Unidades",
            "detalles": "Detalle no extraído.",
        }


def buscar_distribuidores_locales(producto, zip_code):
    if not ai_client:
        return [
            {"nombre": "Grainger Supply", "tel": "(800) 472-4643", "web": "grainger.com"},
            {"nombre": "Fastenal Company", "tel": "(800) 416-9400", "web": "fastenal.com"},
        ]

    prompt = f"""
    Sugiere 2 distribuidores o mayoristas comerciales en EE. UU. que vendan el producto '{producto}' y despachen a la zona con código postal '{zip_code}'.
    Responde ÚNICAMENTE en JSON con una lista de objetos conteniendo: "nombre", "tel" y "web".
    """

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text)
        if isinstance(data, list):
            return data[:2]
        return data.get("distribuidores", [])[:2]
    except Exception as e:
        print(f"⚠️ Error al buscar distribuidores: {e}", flush=True)
        return [
            {"nombre": "Grainger Supply", "tel": "Ver web", "web": "grainger.com"},
            {"nombre": "Fastenal Co.", "tel": "Ver web", "web": "fastenal.com"},
        ]


def obtener_mejores_licitaciones_sam():
    if not SAM_API_KEY:
        print("❌ SAM_API_KEY no encontrada en variables de entorno.", flush=True)
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
        "ptype": "k,o,p",
        "is_active": "true",
    }

    print(f"🔍 [SAM.gov] Escaneando licitaciones activas ({fecha_desde} - {fecha_hasta})...", flush=True)

    try:
        response = requests.get(url, params=params, timeout=20)
        print(f"📊 [SAM.gov] Código HTTP de respuesta: {response.status_code}", flush=True)

        if response.status_code != 200:
            return []

        data = response.json()
        opps = data.get("opportunitiesData", [])
        candidatas = []

        excluir_keywords = [
            "construction",
            "installation required",
            "on-site service",
            "janitorial",
            "repair service",
            "maintenance",
        ]

        for opp in opps:
            opp_id = opp.get("noticeId")
            if not opp_id or opp_id in LICITACIONES_NOTIFICADAS:
                continue

            titulo = opp.get("title", "")
            descripcion = opp.get("description", "")
            texto_completo = f"{titulo} {descripcion}".lower()

            if any(kw in texto_completo for kw in excluir_keywords):
                continue

            response_date_str = opp.get("responseDeadLine")
            dias_restantes = 15
            cierre_str = "A la brevedad"

            if response_date_str:
                try:
                    fecha_cierre = datetime.fromisoformat(response_date_str.replace("Z", "+00:00"))
                    dias_restantes = (fecha_cierre - hoy).days
                    cierre_str = fecha_cierre.strftime("%d/%m/%Y")
                except Exception:
                    pass

            if dias_restantes < 1 or dias_restantes > 60:
                continue

            raw_amount = opp.get("award", {}).get("amount")
            if raw_amount:
                try:
                    monto_estimado = float(raw_amount)
                except ValueError:
                    monto_estimado = 35000.0
            else:
                monto_estimado = 35000.0

            if monto_estimado > 250000.0:
                continue

            zip_code = opp.get("placeOfPerformance", {}).get("zip", "EE. UU.")

            candidatas.append(
                {
                    "id": opp_id,
                    "titulo": titulo or "Sin título",
                    "descripcion": descripcion or "Sin descripción",
                    "solicitation_number": opp.get("solicitationNumber", "N/A"),
                    "agencia": opp.get("department", "Agencia Federal"),
                    "cierre_str": cierre_str,
                    "dias_restantes": dias_restantes,
                    "monto_est": monto_estimado,
                    "zip_code": zip_code,
                    "link": opp.get("uiLink", f"https://sam.gov/opp/{opp_id}/view"),
                }
            )

        candidatas.sort(key=lambda x: x["dias_restantes"])
        print(f"🎯 [SAM.gov] {len(candidatas)} licitaciones COTS válidas listadas.", flush=True)

        return candidatas[:5]

    except Exception as e:
        print(f"⚠️ Error durante la consulta SAM.gov: {e}", flush=True)
        return []


def verificar_adjudicaciones_sam():
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
        "ptype": "a",
    }

    adjudicadas = []
    try:
        response = requests.get(url, params=params, timeout=15)
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
                        "adjudicatario": award_data.get("awardee", {}).get("name", "Contratista"),
                        "link": opp.get("uiLink", f"https://sam.gov/opp/{opp.get('noticeId')}/view"),
                    }
                )
        return adjudicadas
    except Exception as e:
        print(f"⚠️ Error al consultar adjudicaciones: {e}", flush=True)
        return []


# ---------------------------------------------------------
# BUCLE DE NOTIFICACIONES Y BOT TELEGRAM
# ---------------------------------------------------------


def nombre_job(chat_id):
    return f"monitor_{chat_id}"


async def buscar_y_notificar(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id or TELEGRAM_CHAT_ID
    print("⏰ [Bucle] Ejecutando escaneo automático en SAM.gov...", flush=True)

    # 1. Rastrear Adjudicaciones
    adjudicaciones = verificar_adjudicaciones_sam()
    for adj in adjudicaciones:
        sol_num = adj["solicitation_number"]
        mensaje_adj = (
            "🏆 *¡RESULTADO DE ADJUDICACIÓN PUBLICADO!* 🏆\n\n"
            f"📄 *Solicitation #:* `{sol_num}`\n"
            f"📋 *Título:* {adj['titulo']}\n"
            f"💵 *Monto:* ${adj['monto_adjudicado']}\n"
            f"🏢 *Adjudicatario:* {adj['adjudicatario']}\n\n"
            f"🔗 [Ver Registro Oficial]({adj['link']})"
        )
        await context.bot.send_message(chat_id=chat_id, text=mensaje_adj, parse_mode="Markdown")
        LICITACIONES_POSTULADAS.discard(sol_num)

    # 2. Buscar Oportunidades COTS
    licitaciones = obtener_mejores_licitaciones_sam()

    for lic in licitaciones:
        LICITACIONES_NOTIFICADAS.add(lic["id"])

        ia_data = analizar_licitacion_con_ia(lic["titulo"], lic["descripcion"])
        cantidad = max(1, int(ia_data.get("cantidad", 1)))
        producto = ia_data.get("producto", lic["titulo"])
        unidad = ia_data.get("unidad", "Unidades")

        distribuidores = buscar_distribuidores_locales(producto, lic["zip_code"])

        monto_total = lic["monto_est"]
        precio_unitario_bid = monto_total / cantidad
        target_cost_unitario = (monto_total * 0.70) / cantidad

        costo_proveedor = monto_total * 0.70
        flete = 400.00
        factoring = monto_total * 0.03
        ganancia_neta = monto_total - (costo_proveedor + flete + factoring)

        # Guardar en memoria para generar PDFs posteriormente
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
            distrib_str += f"{idx}. *{dist.get('nombre')}* | 📞 {dist.get('tel')} | 🌐 {dist.get('web')}\n"

        mensaje = (
            "🚨 *NUEVA LICITACIÓN DE PRODUCTOS (<$250k)* 🚨\n\n"
            f"📋 *Producto:* {producto}\n"
            f"📦 *Cantidad:* {cantidad} {unidad}\n"
            f"🏛️ *Agencia:* {lic['agencia']}\n"
            f"📍 *Entrega (ZIP):* {lic['zip_code']}\n"
            f"📄 *Solicitation #:* `{lic['solicitation_number']}`\n"
            f"📅 *Cierre:* {lic['cierre_str']} (En {lic['dias_restantes']} días ⚡)\n\n"
            "📊 *ANÁLISIS UNITARIO & TARGET BID*\n"
            f"• Presupuesto Est.: ${monto_total:,.2f} USD\n"
            f"• Precio Bid Unitario Sugerido: ${precio_unitario_bid:,.2f} / unid.\n"
            f"• Costo Máx. Compra Objetivo: <= ${target_cost_unitario:,.2f} / unid.\n"
            f"💵 *Ganancia Neta Est.:* ${ganancia_neta:,.2f} USD\n\n"
            f"🏬 *DISTRIBUIDORES SUGERIDOS CERCA:*\n"
            f"{distrib_str}"
        )

        keyboard = [
            [
                InlineKeyboardButton("🔗 Ver en SAM.gov", url=lic["link"]),
                InlineKeyboardButton("📦 Packing List (PDF)", callback_data=f"pkg_{lic['id']}"),
            ],
            [InlineKeyboardButton("🏢 Generar Vendor PO (PDF)", callback_data=f"po_{lic['id']}")],
        ]

        await context.bot.send_message(
            chat_id=chat_id,
            text=mensaje,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        print(f"📩 Alerta enviada a Telegram: {lic['id']}", flush=True)


# ---------------------------------------------------------
# COMANDOS & CALLBACKS DE TELEGRAM
# ---------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Cazador de Licitaciones L.A.M.B. Logistics LLC*\n\n"
        "Comandos disponibles:\n"
        "• `/on` - Iniciar escaneo automático cada hora\n"
        "• `/off` - Pausar el escaneo\n"
        "• `/postulado <solicitation_num>` - Dar seguimiento a una postura\n"
        "• `/rfq <solicitation_num>` - Generar borrador de correo RFQ para proveedores\n"
        "• `/mis_postulaciones` - Ver lista de licitaciones bajo monitoreo",
        parse_mode="Markdown",
    )


async def cmd_rfq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Uso: `/rfq <solicitation_number>`", parse_mode="Markdown")
        return

    sol_num = context.args[0].strip()
    mensaje_rfq = (
        "✉️ *SOLICITUD DE COTIZACIÓN (RFQ) - DRAFT PARA PROVEEDOR*\n\n"
        "**Subject:** RFQ / Price Quote Request - L.A.M.B. Logistics LLC (Ref: "
        + sol_num
        + ")\n\n"
        "Dear Sales Department,\n\n"
        "L.A.M.B. Logistics LLC is currently preparing a federal supply bid for "
        + sol_num
        + ".\n"
        "Please provide your best wholesale pricing, availability, and estimated lead times for the required items.\n\n"
        "• **Delivery Zip Code:** As specified in requirements.\n"
        "• **Payment Terms:** Net 30 or Credit Card.\n\n"
        "Thank you,\n"
        "*Purchasing Dept | L.A.M.B. Logistics LLC*"
    )
    await update.message.reply_text(mensaje_rfq, parse_mode="Markdown")


async def cmd_postulado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Uso: `/postulado W9124D-26-Q-0001`", parse_mode="Markdown")
        return

    sol_num = context.args[0].strip()
    LICITACIONES_POSTULADAS.add(sol_num)
    await update.message.reply_text(
        f"🎯 *Licitación en seguimiento:* `{sol_num}`", parse_mode="Markdown"
    )


async def cmd_mis_postulaciones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not LICITACIONES_POSTULADAS:
        await update.message.reply_text("📋 No tienes licitaciones registradas en seguimiento.")
        return

    lista_str = "\n".join([f"• `{num}`" for num in LICITACIONES_POSTULADAS])
    await update.message.reply_text(
        f"📋 *Licitaciones bajo monitoreo:*\n\n{lista_str}", parse_mode="Markdown"
    )


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_name = nombre_job(chat_id)

    if context.job_queue is None:
        await update.message.reply_text("❌ Error: JobQueue no configurado.")
        return

    if context.job_queue.get_jobs_by_name(job_name):
        await update.message.reply_text("⚡ El escaneo automático ya está activo.")
        return

    context.job_queue.run_repeating(
        buscar_y_notificar, interval=3600, first=1, chat_id=chat_id, name=job_name
    )
    await update.message.reply_text("✅ *Motor encendido.* Monitoreando SAM.gov...", parse_mode="Markdown")


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_name = nombre_job(chat_id)

    if context.job_queue is None:
        return

    jobs = context.job_queue.get_jobs_by_name(job_name)
    if not jobs:
        await update.message.reply_text("💤 El motor ya está apagado.")
        return

    for job in jobs:
        job.schedule_removal()
    await update.message.reply_text("🛑 *Motor pausado.*", parse_mode="Markdown")


async def boton_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("pkg_"):
        lic_id = data.replace("pkg_", "")
        lic_data = MEMORIA_LICITACIONES.get(
            lic_id, {"solicitation_number": lic_id, "producto": "Producto COTS", "cantidad": 1}
        )
        pdf_file = generar_pdf_packing_list(lic_data)
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=pdf_file,
            filename=f"Packing_List_{lic_data.get('solicitation_number', 'Doc')}.pdf",
            caption="📦 **Packing List generado exitosamente.**",
            parse_mode="Markdown",
        )

    elif data.startswith("po_"):
        lic_id = data.replace("po_", "")
        lic_data = MEMORIA_LICITACIONES.get(
            lic_id, {"solicitation_number": lic_id, "producto": "Producto COTS", "cantidad": 1}
        )
        pdf_file = generar_pdf_po(lic_data)
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=pdf_file,
            filename=f"Vendor_PO_{lic_data.get('solicitation_number', 'Doc')}.pdf",
            caption="🏢 **Vendor Purchase Order generado exitosamente.**",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------
# INICIALIZACIÓN DE LA APLICACIÓN
# ---------------------------------------------------------


def main():
    threading.Thread(target=run_flask, daemon=True).start()

    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN no definido.", flush=True)
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("on", cmd_on))
    app.add_handler(CommandHandler("off", cmd_off))
    app.add_handler(CommandHandler("rfq", cmd_rfq))
    app.add_handler(CommandHandler("postulado", cmd_postulado))
    app.add_handler(CommandHandler("mis_postulaciones", cmd_mis_postulaciones))
    app.add_handler(CallbackQueryHandler(boton_callback))

    if TELEGRAM_CHAT_ID:
        job_name = nombre_job(TELEGRAM_CHAT_ID)
        if app.job_queue is not None:
            app.job_queue.run_repeating(
                buscar_y_notificar,
                interval=3600,
                first=1,
                chat_id=TELEGRAM_CHAT_ID,
                name=job_name,
            )
            print("🟢 Monitoreo automático en segundo plano activado.", flush=True)

    print("🤖 Cazador de Licitaciones activo. Iniciando polling...", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
