import os
import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from flask import Flask
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

# ---------------------------------------------------------
# SERVIDOR FLASK (Para mantener activo Render vía UptimeRobot)
# ---------------------------------------------------------
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "Bot Cazador de Licitaciones activo 24/7 en Render", 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    flask_app.run(host='0.0.0.0', port=port)

# ---------------------------------------------------------
# CONFIGURACIÓN DE LOGS Y VARIABLES DE ENTORNO
# ---------------------------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SAM_API_KEY = os.getenv("SAM_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

LICITACIONES_NOTIFICADAS = set()
LICITACIONES_POSTULADAS = set()

ai_client = None
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logging.error(f"Error al inicializar cliente de Gemini: {e}")

# ---------------------------------------------------------
# FUNCIONES DE IA Y SAM.GOV
# ---------------------------------------------------------
def analizar_licitacion_con_ia(titulo, descripcion):
    if not ai_client:
        return {
            "producto": titulo,
            "cantidad": 1,
            "unidad": "Unidades",
            "detalles": "Análisis de IA no disponible."
        }

    prompt = f"""
    Analiza esta licitación del gobierno federal de EE. UU. orientada a compra/suministro de productos COTS (Commercial Off-The-Shelf).
    Título: {titulo}
    Descripción: {descripcion}

    Extrae en formato JSON únicamente:
    1. "producto": Nombre claro y conciso del producto o ítem físico solicitado.
    2. "cantidad": Cantidad numérica total solicitada (si no se especifica o es ambigua, responde 1).
    3. "unidad": Unidad de medida (ej. Cajas, Unidades, Paquetes, Sets, Kits).
    4. "detalles": Breve resumen de 1 oración con especificaciones clave o número de parte/marca requerida.
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
        # Asegurar cantidad válida
        try:
            cant = int(data.get("cantidad", 1))
            data["cantidad"] = cant if cant > 0 else 1
        except (ValueError, TypeError):
            data["cantidad"] = 1
        return data
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
            {"nombre": "Grainger Industrial Supply", "tel": "(800) 472-4643", "web": "grainger.com"},
            {"nombre": "Fastenal Company", "tel": "(800) 416-9400", "web": "fastenal.com"}
        ]

    prompt = f"""
    Sugiere 2 distribuidores o mayoristas industriales/comerciales reales en EE. UU. que vendan el producto '{producto}' y que puedan despachar a la región con código postal '{zip_code}'.

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
            {"nombre": "Grainger Supply", "tel": "Consulte web", "web": "grainger.com"},
            {"nombre": "Fastenal Co.", "tel": "Consulte web", "web": "fastenal.com"}
        ]


def obtener_mejores_licitaciones_sam():
    if not SAM_API_KEY:
        logging.error("SAM_API_KEY no encontrada en Secrets.")
        return []

    url = "https://api.sam.gov/prod/opportunities/v2/search"
    hoy = datetime.now(timezone.utc)
    fecha_desde = (hoy - timedelta(days=7)).strftime("%m/%d/%Y")

    # NAICS clave de comercio mayorista, tecnología, suministros y manufactura comercial (Dropshipping friendly)
    naics_objetivo = "423430,423420,423830,423450,334111,423610,423840,423120"

    params = {
        "api_key": SAM_API_KEY,
        "postedFrom": fecha_desde,
        "postedTo": hoy.strftime("%m/%d/%Y"),
        "limit": 100,
        "ncode": naics_objetivo,
        "ptype": "k,o,r"  # Combined Synopsis/Solicitation (k), Solicitations (o), Sources Sought (r)
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        data = response.json()

        candidatas = []
        # Palabras clave incompatibles con dropshipping (requieren trabajo físico en sitio)
        excluir_keywords = ["construction", "installation required", "on-site service", "site visit mandatory", "janitorial service"]

        for opp in data.get("opportunitiesData", []):
            opp_id = opp.get("noticeId")

            if not opp_id or opp_id in LICITACIONES_NOTIFICADAS:
                continue

            titulo = opp.get("title", "")
            descripcion = opp.get("description", "")
            texto_completo = f"{titulo} {descripcion}".lower()

            # Descartar licitaciones con requerimientos de servicio u obra en sitio
            if any(kw in texto_completo for kw in excluir_keywords):
                continue

            response_date_str = opp.get("responseDeadLine")
            if not response_date_str:
                continue

            try:
                fecha_cierre = datetime.fromisoformat(
                    response_date_str.replace("Z", "+00:00")
                )
            except ValueError:
                continue

            dias_restantes = (fecha_cierre - hoy).days

            # Ventana ampliada de 1 a 45 días para dar tiempo de cotización con distribuidores
            if not (1 <= dias_restantes <= 45):
                continue

            raw_amount = opp.get("award", {}).get("amount")
            if raw_amount:
                monto_estimado = float(raw_amount)
            else:
                monto_estimado = 25000.0

            zip_code = opp.get(
                "placeOfPerformance", {}
            ).get("zip", "EE. UU.")

            candidatas.append({
                "id": opp_id,
                "titulo": titulo or "Sin título",
                "descripcion": descripcion or "Sin descripción",
                "solicitation_number": opp.get(
                    "solicitationNumber", "N/A"
                ),
                "agencia": opp.get(
                    "department", "Agencia Federal"
                ),
                "cierre_str": fecha_cierre.strftime("%d/%m/%Y"),
                "dias_restantes": dias_restantes,
                "monto_est": monto_estimado,
                "zip_code": zip_code,
                "link": opp.get(
                    "uiLink",
                    f"https://sam.gov/opp/{opp_id}/view"
                )
            })

        candidatas.sort(key=lambda x: x["dias_restantes"])

        return candidatas[:8]  # Retornar las 8 más relevantes por ejecución

    except Exception as e:
        logging.error(f"Error al consultar SAM.gov: {e}")
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
        "ptype": "a"  # Award Notices
    }

    adjudicadas_encontradas = []

    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()

        for opp in data.get("opportunitiesData", []):
            sol_num = opp.get("solicitationNumber", "")

            if sol_num in LICITACIONES_POSTULADAS:
                award_data = opp.get("award", {})

                adjudicadas_encontradas.append({
                    "solicitation_number": sol_num,
                    "titulo": opp.get("title", "Sin título"),
                    "monto_adjudicado": award_data.get("amount", "No especificado"),
                    "adjudicatario": award_data.get("awardee", {}).get("name", "Contratista"),
                    "fecha_adjudicacion": award_data.get("date", "Reciente"),
                    "link": opp.get("uiLink", f"https://sam.gov/opp/{opp.get('noticeId')}/view")
                })

        return adjudicadas_encontradas

    except Exception as e:
        logging.error(f"Error al verificar adjudicaciones en SAM.gov: {e}")
        return []


def nombre_job(chat_id):
    return f"monitor_{chat_id}"


async def buscar_y_notificar(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id or TELEGRAM_CHAT_ID

    # 1. RASTREAR ADJUDICACIONES
    adjudicaciones = verificar_adjudicaciones_sam()
    for adj in adjudicaciones:
        sol_num = adj["solicitation_number"]
        monto = adj["monto_adjudicado"]
        ganador = adj["adjudicatario"]

        mensaje_adj = (
            "🏆 *¡RESULTADO DE ADJUDICACIÓN REGISTRADO!* 🏆\n\n"
            f"📄 *Solicitation #:* `{sol_num}`\n"
            f"📋 *Título:* {adj['titulo']}\n"
            f"💵 *Monto Adjudicado:* ${monto}\n"
            f"🏢 *Adjudicatario registrado:* {ganador}\n\n"
            f"🔗 [Ver Registro Oficial en SAM.gov]({adj['link']})\n\n"
            "💡 _Si L.A.M.B Logistics LLC figura como adjudicataria, revisa tu correo para el documento SF-1449._"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=mensaje_adj,
            parse_mode="Markdown"
        )

        LICITACIONES_POSTULADAS.discard(sol_num)

    # 2. BUSCAR NUEVAS OPORTUNIDADES DROPSHIPPING
    licitaciones = obtener_mejores_licitaciones_sam()

    for lic in licitaciones:
        LICITACIONES_NOTIFICADAS.add(lic["id"])

        ia_data = analizar_licitacion_con_ia(
            lic["titulo"],
            lic["descripcion"]
        )

        cantidad = max(1, int(ia_data.get("cantidad", 1)))
        producto = ia_data.get("producto", lic["titulo"])

        distribuidores = buscar_distribuidores_locales(
            producto,
            lic["zip_code"]
        )

        monto_total = lic["monto_est"]
        precio_unitario_bid = monto_total / cantidad
        target_cost_unitario = (monto_total * 0.70) / cantidad

        costo_proveedor = monto_total * 0.70
        flete = 400.00
        factoring = monto_total * 0.03
        ganancia_neta = monto_total - (costo_proveedor + flete + factoring)

        distrib_str = ""
        for idx, dist in enumerate(distribuidores, 1):
            distrib_str += (
                f"{idx}. *{dist.get('nombre')}* | "
                f"📞 {dist.get('tel')} | "
                f"🌐 {dist.get('web')}\n"
            )

        mensaje = (
            "🚨 *NUEVA LICITACIÓN DE PRODUCTOS (DROPSHIPPING)* 🚨\n\n"
            f"📋 *Producto:* {producto}\n"
            f"📦 *Cantidad:* {cantidad} {ia_data.get('unidad', 'Unidades')}\n"
            f"🏛️ *Agencia:* {lic['agencia']}\n"
            f"📍 *Entrega (ZIP):* {lic['zip_code']}\n"
            f"📄 *Solicitation #:* `{lic['solicitation_number']}`\n"
            f"📅 *Cierre:* {lic['cierre_str']} (En {lic['dias_restantes']} días ⚡)\n\n"
            "📊 *ANÁLISIS UNITARIO & TARGET BID*\n"
            f"• Presupuesto Est.: ${monto_total:,.2f} USD\n"
            f"• Precio Bid Unitario Sugerido: ${precio_unitario_bid:,.2f} / unid.\n"
            f"• Costo Máx. Compra Objetivo: <= ${target_cost_unitario:,.2f} / unid.\n"
            f"💵 *Ganancia Neta Est.:* ${ganancia_neta:,.2f} USD (Margen ~30%)\n\n"
            f"🏬 *DISTRIBUIDORES SUGERIDOS CERCA:*\n"
            f"{distrib_str}"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔗 Ver en SAM.gov",
                    url=lic["link"]
                ),
                InlineKeyboardButton(
                    "📦 Packing List",
                    callback_data=f"pkg_{lic['id']}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🏢 Generar Vendor PO",
                    callback_data=f"po_{lic['id']}"
                )
            ]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=chat_id,
            text=mensaje,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )


# ---------------------------------------------------------
# COMANDOS DE TELEGRAM
# ---------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Cazador de Licitaciones & Rastreos Operativo*\n\n"
        "Filtros activos:\n"
        "• Ventana de cierre: 1 a 45 días\n"
        "• Publicaciones: Últimos 7 días\n"
        "• Modelo: Dropshipping / Suministros y Productos COTS\n\n"
        "Comandos:\n"
        "• `/on` - Enciende la búsqueda automática\n"
        "• `/off` - Pausa la búsqueda\n"
        "• `/postulado <solicitation_num>` - Registra una licitación postulada\n"
        "• `/mis_postulaciones` - Ver lista bajo seguimiento",
        parse_mode="Markdown"
    )


async def cmd_postulado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Debes proporcionar el número de solicitud.\n"
            "Ejemplo: `/postulado W9124D-26-Q-0001`",
            parse_mode="Markdown"
        )
        return

    sol_num = context.args[0].strip()
    LICITACIONES_POSTULADAS.add(sol_num)

    await update.message.reply_text(
        f"🎯 *Licitación bajo seguimiento:* `{sol_num}`\n"
        "El bot monitoreará los avisos de adjudicación (Award Notices) en SAM.gov y te avisará cuando haya resultados.",
        parse_mode="Markdown"
    )


async def cmd_mis_postulaciones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not LICITACIONES_POSTULADAS:
        await update.message.reply_text("📋 Actualmente no tienes licitaciones en seguimiento.")
        return

    lista_str = "\n".join([f"• `{num}`" for num in LICITACIONES_POSTULADAS])
    await update.message.reply_text(
        f"📋 *Licitaciones postuladas bajo seguimiento:*\n\n{lista_str}",
        parse_mode="Markdown"
    )


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_name = nombre_job(chat_id)

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

    await update.message.reply_text(
        "✅ *Motor con IA encendido.* Monitoreando oportunidades COTS/Dropshipping y adjudicaciones...",
        parse_mode="Markdown"
    )


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_name = nombre_job(chat_id)

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

        await query.message.reply_text(
            f"⏳ Generando Packing List para licitación `{lic_id}`...",
            parse_mode="Markdown"
        )

    elif data.startswith("po_"):
        lic_id = data.split("_")[1]

        await query.message.reply_text(
            f"⏳ Generando Orden de Compra (Vendor PO) para `{lic_id}`...",
            parse_mode="Markdown"
        )


def main():
    threading.Thread(target=run_flask, daemon=True).start()

    if not TELEGRAM_BOT_TOKEN:
        print("Error: No se encontró TELEGRAM_BOT_TOKEN en Environment Variables.")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("on", cmd_on))
    app.add_handler(CommandHandler("off", cmd_off))
    app.add_handler(CommandHandler("postulado", cmd_postulado))
    app.add_handler(CommandHandler("mis_postulaciones", cmd_mis_postulaciones))
    app.add_handler(CallbackQueryHandler(boton_callback))

    if TELEGRAM_CHAT_ID:
        job_name = nombre_job(TELEGRAM_CHAT_ID)

        if app.job_queue is None:
            logging.error(
                "No se pudo activar el monitoreo automático: JobQueue no está disponible."
            )
        else:
            app.job_queue.run_repeating(
                buscar_y_notificar,
                interval=3600,
                first=1,
                chat_id=TELEGRAM_CHAT_ID,
                name=job_name
            )

            logging.info("Monitoreo automático continuo configurado cada hora.")
    else:
        logging.warning("TELEGRAM_CHAT_ID no configurado; usa /on en Telegram.")

    print("🤖 Cazador de Licitaciones con IA iniciando polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
