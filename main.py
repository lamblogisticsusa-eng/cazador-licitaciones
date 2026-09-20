from keep_alive import keep_alive

keep_alive()

import os
import json
import logging
from datetime import datetime, timezone, timedelta
import requests
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SAM_API_KEY = os.getenv("SAM_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

LICITACIONES_NOTIFICADAS = set()

ai_client = None
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logging.error(f"Error al inicializar cliente de Gemini: {e}")

def analizar_licitacion_con_ia(titulo, descripcion):
    if not ai_client:
        return {
            "producto": titulo,
            "cantidad": 1,
            "unidad": "Unidades",
            "detalles": "Análisis de IA no disponible (Falta GEMINI_API_KEY)."
        }

    prompt = f"""
    Analiza esta licitación del gobierno federal de EE. UU.
    Título: {titulo}
    Descripción: {descripcion}

    Extrae en formato JSON únicamente:
    1. "producto": Nombre claro y conciso del producto o ítem físico solicitado.
    2. "cantidad": Cantidad numérica total solicitada (si no se especifica, asume 1).
    3. "unidad": Unidad de medida (ej. Cajas, Unidades, Paquetes, Sets).
    4. "detalles": Breve resumen de 1 oración con especificaciones clave o número de parte/marca.
    """

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception as e:
        logging.error(f"Error al analizar con Gemini: {e}")
        return {
            "producto": titulo,
            "cantidad": 1,
            "unidad": "Unidades",
            "detalles": "No se pudo extraer detalle con IA."
        }

def buscar_distribuidores_locales(producto, zip_code):
    if not ai_client:
        return [
            {"nombre": "Fastenal Industrial Supply", "tel": "(800) 416-9400", "web": "fastenal.com"},
            {"nombre": "Grainger Industrial Supply", "tel": "(800) 472-4643", "web": "grainger.com"}
        ]

    prompt = f"""
    Genera 2 distribuidores o mayoristas industriales reales o recomendados en EE. UU. que vendan el producto '{producto}' cerca del código postal/región '{zip_code}'.
    
    Responde ÚNICAMENTE en JSON con una lista de objetos conteniendo: "nombre", "tel" y "web".
    """

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        data = json.loads(response.text)
        if isinstance(data, list):
            return data[:2]
        return data.get("distribuidores", [])[:2]
    except Exception as e:
        logging.error(f"Error al buscar distribuidores con IA: {e}")
        return [
            {"nombre": "Fastenal Co.", "tel": "Consulte web", "web": "fastenal.com"},
            {"nombre": "Grainger Supply", "tel": "Consulte web", "web": "grainger.com"}
        ]

def obtener_mejores_licitaciones_sam():
    if not SAM_API_KEY:
        logging.error("SAM_API_KEY no encontrada en Secrets.")
        return []

    url = "https://api.sam.gov/prod/opportunities/v2/search"
    hoy = datetime.now(timezone.utc)
    fecha_desde = (hoy - timedelta(days=2)).strftime("%m/%d/%Y")
    
    params = {
        "api_key": SAM_API_KEY,
        "postedFrom": fecha_desde,
        "postedTo": hoy.strftime("%m/%d/%Y"),
        "limit": 25,
        "ptype": "o,k"
    }

    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
        
        candidatas = []
        for opp in data.get("opportunitiesData", []):
            opp_id = opp.get("noticeId")
            if not opp_id or opp_id in LICITACIONES_NOTIFICADAS:
                continue

            psc_code = opp.get("classificationCode", "")
            if psc_code and psc_code[0].isalpha():
                continue

            response_date_str = opp.get("responseDeadLine")
            if not response_date_str:
                continue
            
            try:
                fecha_cierre = datetime.fromisoformat(response_date_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            
            dias_restantes = (fecha_cierre - hoy).days
            if not (5 <= dias_restantes <= 7):
                continue

            monto_estimado = float(opp.get("award", {}).get("amount", 25000) or 25000)
            if not (5000 <= monto_estimado <= 100000):
                continue

            zip_code = opp.get("placeOfPerformance", {}).get("zip", "EE. UU.")
            
            candidatas.append({
                "id": opp_id,
                "titulo": opp.get("title", "Sin título"),
                "descripcion": opp.get("description", "Sin descripción"),
                "solicitation_number": opp.get("solicitationNumber", "N/A"),
                "agencia": opp.get("department", "Agencia Federal"),
                "cierre_str": fecha_cierre.strftime("%d/%m/%Y"),
                "dias_restantes": dias_restantes,
                "monto_est": monto_estimado,
                "zip_code": zip_code,
                "link": opp.get("uiLink", f"https://sam.gov/opp/{opp_id}/view")
            })

        candidatas.sort(key=lambda x: x["dias_restantes"])
        return candidatas[:5]

    except Exception as e:
        logging.error(f"Error al consultar SAM.gov: {e}")
        return []

async def buscar_y_notificar(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id or TELEGRAM_CHAT_ID
    licitaciones = obtener_mejores_licitaciones_sam()

    for lic in licitaciones:
        LICITACIONES_NOTIFICADAS.add(lic["id"])
        
        ia_data = analizar_licitacion_con_ia(lic["titulo"], lic["descripcion"])
        cantidad = max(1, int(ia_data.get("cantidad", 1)))
        producto = ia_data.get("producto", lic["titulo"])
        
        distribuidores = buscar_distribuidores_locales(producto, lic["zip_code"])
        
        monto_total = lic["monto_est"]
        precio_unitario_bid = monto_total / cantidad
        target_cost_unitario = (monto_total * 0.70) / cantidad
        
        costo_proveedor = monto_total * 0.70
        flete = 400.00
        factoring = monto_total * 0.03
        ganancia_neta = monto_total - (costo_proveedor + flete + factoring)

        distrib_str = ""
        for idx, dist in enumerate(distribuidores, 1):
            distrib_str += f"{idx}. *{dist.get('nombre')}* | 📞 {dist.get('tel')} | 🌐 {dist.get('web')}\n"

        mensaje = (
            "🚨 *NUEVA LICITACIÓN DE SUMINISTROS (DROPSHIPPING)* 🚨\n\n"
            f"📋 *Producto:* {producto}\n"
            f"📦 *Cantidad:* {cantidad} {ia_data.get('unidad', 'Unidades')}\n"
            f"🏛️ *Agencia:* {lic['agencia']}\n"
            f"📍 *Entrega (ZIP):* {lic['zip_code']}\n"
            f"📄 *Solicitation #:* `{lic['solicitation_number']}`\n"
            f"📅 *Cierre:* {lic['cierre_str']} (En {lic['dias_restantes']} días ⚡)\n\n"
            "📊 *ANÁLISIS UNITARIO & ESTRATEGIA (TARGET BID)*\n"
            f"• Presupuesto Total Est.: ${monto_total:,.2f} USD\n"
            f"• Precio Bid Unitario Sugerido:* ${precio_unitario_bid:,.2f} / unid.\n"
            f"• Costo Máx. Compra Objetivo:* <= ${target_cost_unitario:,.2f} / unid.\n"
            f"💵 *Ganancia Neta Est.:* ${ganancia_neta:,.2f} USD (Margen 30%)\n\n"
            f"🏬 *DISTRIBUIDORES SUGERIDOS CERCA:* \n{distrib_str}"
        )

        keyboard = [
            [
                InlineKeyboardButton("🔗 Ver en SAM.gov", url=lic["link"]),
                InlineKeyboardButton("📦 Packing List", callback_data=f"pkg_{lic['id']}")
            ],
            [
                InlineKeyboardButton("🏢 Generar Vendor PO", callback_data=f"po_{lic['id']}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=chat_id,
            text=mensaje,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Cazador de Licitaciones Operativo con IA (Gemini)*\n\n"
        "Filtros activos:\n"
        "• Monto: $5,000 — $100,000 USD\n"
        "• Ventana de cierre: 5 a 7 días\n"
        "• Exclusivo: Suministros y Productos Físicos\n\n"
        "Comandos:\n"
        "• `/on` - Enciende la búsqueda automática\n"
        "• `/off` - Pausa la búsqueda",
        parse_mode="Markdown"
    )

async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_name = str(chat_id)

    if context.job_queue is None:
        await update.message.reply_text("❌ La función JobQueue no está activa.")
        return

    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    if current_jobs:
        await update.message.reply_text("⚡ El motor con IA ya está activado y monitoreando.")
        return

    context.job_queue.run_repeating(
        buscar_y_notificar,
        interval=3600,
        first=1,
        chat_id=chat_id,
        name=job_name
    )

    await update.message.reply_text("✅ *Motor con IA encendido.* Monitoreando productos y distribuidores...", parse_mode="Markdown")

async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_name = str(chat_id)

    if context.job_queue is None:
        await update.message.reply_text("❌ La función JobQueue no está activa.")
        return

    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    if not current_jobs:
        await update.message.reply_text("💤 El motor ya está apagado.")
        return

    for job in current_jobs:
        job.schedule_removal()

    await update.message.reply_text("🛑 *Motor pausado.*", parse_mode="Markdown")

async def boton_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("pkg_"):
        lic_id = data.split("_")[1]
        await query.message.reply_text(f"⏳ Generando Packing List para licitación `{lic_id}`...", parse_mode="Markdown")
    elif data.startswith("po_"):
        lic_id = data.split("_")[1]
        await query.message.reply_text(f"⏳ Generando Orden de Compra (Vendor PO) para `{lic_id}`...", parse_mode="Markdown")

def main():
    if not TELEGRAM_BOT_TOKEN:
        print("Error: No se encontró TELEGRAM_BOT_TOKEN en Secrets.")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("on", cmd_on))
    app.add_handler(CommandHandler("off", cmd_off))
    app.add_handler(CallbackQueryHandler(boton_callback))

    print("🤖 Cazador de Licitaciones con IA iniciando polling...")
    app.run_polling()

if __name__ == "__main__":
    main()