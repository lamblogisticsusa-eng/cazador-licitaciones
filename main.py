import os
import sqlite3
import logging
import threading
import requests
import asyncio
from datetime import datetime, timezone, timedelta

from flask import Flask
from google import genai
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)
from apscheduler.schedulers.background import BackgroundScheduler

# ---------------------------------------------------------------------------
# 1. LOGGING CONFIGURATION
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("Kiyomoto_Logistics")

MONITOREO_ACTIVO = True

# ---------------------------------------------------------------------------
# 2. FLASK APP (HEALTH CHECK FOR RENDER)
# ---------------------------------------------------------------------------
web_app = Flask(__name__)

@web_app.route("/")
def health_check():
    return "¡Servicio Kiyomoto Logistics Activo y Vigilando SAM.gov! ✨(🔒_🔒)✨", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, use_reloader=False)

# ---------------------------------------------------------------------------
# 3. BASE DE DATOS SQLITE
# ---------------------------------------------------------------------------
DB_NAME = "licitaciones.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS licitaciones_procesadas (
            id TEXT PRIMARY KEY,
            titulo TEXT,
            filtro_pasado INTEGER,
            fecha_procesado TEXT
        )
    """)
    conn.commit()
    conn.close()

def esta_procesada(licitacion_id: str) -> bool:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT 1 FROM licitaciones_procesadas WHERE id = ?", (licitacion_id,))
    existe = c.fetchone() is not None
    conn.close()
    return existe

def registrar_licitacion(licitacion_id: str, titulo: str, filtro_pasado: bool):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    fecha_iso = datetime.now(timezone.utc).isoformat()
    c.execute(
        "INSERT OR REPLACE INTO licitaciones_procesadas (id, titulo, filtro_pasado, fecha_procesado) VALUES (?, ?, ?, ?)",
        (licitacion_id, titulo, 1 if filtro_pasado else 0, fecha_iso)
    )
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# 4. INTEGRACIÓN DE GOOGLE GEMINI
# ---------------------------------------------------------------------------
def procesar_con_gemini(prompt: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("❌ GEMINI_API_KEY no configurada.")
        return None

    client = genai.Client(api_key=api_key)
    modelos_disponibles = ["gemini-2.5-flash", "gemini-1.5-flash"]

    for modelo in modelos_disponibles:
        try:
            response = client.models.generate_content(
                model=modelo,
                contents=prompt
            )
            if response and response.text:
                return response.text
        except Exception as e:
            logger.warning(f"⚠️ Error consultando Gemini ({modelo}): {e}")
            continue

    logger.error("❌ Ningún modelo de Gemini estuvo disponible.")
    return None

# ---------------------------------------------------------------------------
# 5. INTEGRACIÓN API SAM.GOV
# ---------------------------------------------------------------------------
def obtener_oportunidades_sam():
    sam_api_key = os.environ.get("SAM_API_KEY")
    if not sam_api_key:
        logger.warning("⚠️ SAM_API_KEY no encontrada.")
        return []

    url = "https://api.sam.gov/prod/opportunities/v2/search"
    
    # Formato estrictamente requerido por SAM.gov v2 API: YYYY-MM-DD
    fecha_hasta = datetime.now(timezone.utc)
    fecha_desde = fecha_hasta - timedelta(days=2)
    
    params = {
        "api_key": sam_api_key,
        "postedFrom": fecha_desde.strftime("%Y-%m-%d"),
        "postedTo": fecha_hasta.strftime("%Y-%m-%d"),
        "ptype": "o,k,p",  # Solicitations, Combined Synopsis, Presolicitations
        "limit": 25
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        if response.status_code == 200:
            data = response.json()
            opps = data.get("opportunitiesData", [])
            logger.info(f"📊 SAM.gov devolvió {len(opps)} registros.")
            return opps
        else:
            logger.error(f"❌ Error API SAM.gov ({response.status_code}): {response.text}")
            return []
    except Exception as e:
        logger.error(f"❌ Excepción al conectar con SAM.gov: {e}")
        return []

# ---------------------------------------------------------------------------
# 6. MONITOREO CON NOTIFICACIONES
# ---------------------------------------------------------------------------
async def notificar_telegram(bot_app, chat_id: str, mensaje: str):
    try:
        await bot_app.bot.send_message(chat_id=chat_id, text=mensaje, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error enviando mensaje a Telegram: {e}")

def ejecutar_monitoreo_licitaciones(bot_application=None):
    global MONITOREO_ACTIVO
    if not MONITOREO_ACTIVO:
        logger.info("Monitoreo pausado por órdenes del usuario (/off).~")
        return

    logger.info("🔍 Kiyomoto rastreando nuevas oportunidades en SAM.gov...")
    
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    oportunidades = obtener_oportunidades_sam()

    if not oportunidades:
        logger.info("No se encontraron nuevas oportunidades en este ciclo.")
        return

    for opp in oportunidades:
        notice_id = opp.get("noticeId") or opp.get("solicitationNumber")
        if not notice_id or esta_procesada(notice_id):
            continue

        titulo = opp.get("title", "Sin Título")
        descripcion = opp.get("description", opp.get("subject", "Sin Descripción"))
        agencia = opp.get("fullParentPathName", "Agencia Federal")
        ui_link = opp.get("uiLink", f"https://sam.gov/opp/{notice_id}/view")

        prompt = f"""
        Analiza esta oportunidad de licitación de SAM.gov.
        Determina si busca comprar PRODUCTOS FÍSICOS/TANGIBLES COTS (equipos, repuestos, suministros, partes) o si es un servicio intangible/mantenimiento.

        Título: {titulo}
        Descripción: {descripcion[:1500]}

        Responde exclusivamente en este formato JSON:
        {{"es_producto_fisico": true/false, "resumen": "Resumen conciso en 2 frases de lo que piden"}}
        """

        resultado = procesar_con_gemini(prompt)
        
        if resultado and "true" in resultado.lower():
            registrar_licitacion(notice_id, titulo, True)
            logger.info(f"✨ Licitación {notice_id} aprobada por Kiyomoto!")
            
            if bot_application and chat_id:
                mensaje_notif = (
                    f"🌸 <b>¡Nueva Oportunidad Detectada en SAM.gov!</b> 📦✨\n\n"
                    f"🆔 <b>ID:</b> <code>{notice_id}</code>\n"
                    f"🏢 <b>Agencia:</b> {agencia}\n"
                    f"📌 <b>Título:</b> {titulo}\n"
                    f"🔗 <b>Enlace:</b> <a href='{ui_link}'>Ver en SAM.gov</a>\n\n"
                    f"💡 <b>Análisis de Kiyomoto:</b> Es un suministro o producto tangible listo para cotizar. 🚀"
                )
                asyncio.run_coroutine_threadsafe(
                    notificar_telegram(bot_application, chat_id, mensaje_notif),
                    bot_application.loop
                )
        else:
            registrar_licitacion(notice_id, titulo, False)

# ---------------------------------------------------------------------------
# 7. HANDLERS ASÍNCRONOS KIYOMOTO KAWAII ✨
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    mensaje = (
        f"✨ <b>¡Hola, {user_name}-san!</b> (⁠✿⁠☉⁠｡⁠☉⁠)\n"
        f"Soy <b>Kiyomoto</b>, tu cazadora de licitaciones de logística. 🌸📦\n\n"
        f"Vigilo <b>SAM.gov</b> en tiempo real para avisarte cuando publiquen contratos de productos físicos. ✨\n\n"
        f"📜 <b>Comandos:</b>\n"
        f"• /status - Estado del sistema 📊\n"
        f"• /on - Activar monitoreo 🟢\n"
        f"• /off - Pausar monitoreo 🔴\n"
        f"• /forzar_escaneo - Escaneo manual inmediato 🔍"
    )
    await update.message.reply_text(mensaje, parse_mode="HTML")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    estado_str = "🟢 ACTIVO (¡Radar encendido! ✨)" if MONITOREO_ACTIVO else "🔴 PAUSADO (En descanso... 💤)"
    sam_key_ok = "Configurada ✅" if os.environ.get("SAM_API_KEY") else "No Configurada ⚠️"
    mensaje = (
        f"📊 <b>Reporte de Estado de Kiyomoto</b> (🔒_🔒)✨\n\n"
        f"• <b>Servidor Web:</b> Operativo (Puerto 10000) 🌐\n"
        f"• <b>Radar de Monitoreo:</b> {estado_str}\n"
        f"• <b>API SAM.gov:</b> {sam_key_ok}\n"
        f"• <b>Cerebro Gemini IA:</b> Conectado 🧠"
    )
    await update.message.reply_text(mensaje, parse_mode="HTML")

async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MONITOREO_ACTIVO
    MONITOREO_ACTIVO = True
    await update.message.reply_text("🟢 <b>¡Entendido!</b> Radar de SAM.gov activado. (⁠•̀⁠ᴗ⁠•́⁠)⁠و✨", parse_mode="HTML")

async def off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MONITOREO_ACTIVO
    MONITOREO_ACTIVO = False
    await update.message.reply_text("🔴 <b>¡Monitoreo pausado!</b> En pausa hasta tu orden. (⁠´⁠ー⁠｀⁠)", parse_mode="HTML")

async def forzar_escaneo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 <b>¡Buscando en SAM.gov ahora mismo!</b>... (⁠✦⁠‿⁠✦⁠)", parse_mode="HTML")
    ejecutar_monitoreo_licitaciones(context.application)
    await update.message.reply_text("✨ <b>¡Escaneo completado!</b> Si hay novedades te las notifiqué arriba. 🌸", parse_mode="HTML")

# ---------------------------------------------------------------------------
# 8. PUNTO DE ENTRADA PRINCIPAL
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Servidor Flask kawaii iniciado.")

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    if not telegram_token:
        logger.critical("❌ No se encontró el token de Telegram.")
        exit(1)

    bot_app = ApplicationBuilder().token(telegram_token).build()

    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("status", status_command))
    bot_app.add_handler(CommandHandler("on", on_command))
    bot_app.add_handler(CommandHandler("off", off_command))
    bot_app.add_handler(CommandHandler("forzar_escaneo", forzar_escaneo_command))

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        ejecutar_monitoreo_licitaciones,
        "interval",
        hours=1,
        args=[bot_app],
        id="job_monitoreo_licitaciones"
    )
    scheduler.start()

    logger.info("✨ Kiyomoto lista y escuchando...")
    bot_app.run_polling(drop_pending_updates=True)
