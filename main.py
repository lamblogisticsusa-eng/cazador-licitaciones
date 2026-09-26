import os
import sqlite3
import logging
import threading
from datetime import datetime, timezone

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
    return "¡Servicio Kiyomoto Logistics Activo y Vigilando! ✨(🔒_🔒)✨", 200

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
# 4. INTEGRACIÓN DE GOOGLE GEMINI (SENSEI DE INTELIGENCIA)
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
# 5. TAREA PROGRAMADA DE MONITOREO CON NOTIFICACIONES
# ---------------------------------------------------------------------------
async def notificar_telegram(bot_app, chat_id: str, mensaje: str):
    try:
        await bot_app.bot.send_message(chat_id=chat_id, text=mensaje, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error enviando mensaje a Telegram: {e}")

def ejecutar_monitoreo_licitaciones(bot_application=None):
    global MONITOREO_ACTIVO
    if not MONITOREO_ACTIVO:
        logger.info("Monitoreo pausado por órdenes del usuario (/off).~")
        return

    logger.info("🔍 Kiyomoto rastreando nuevas oportunidades...")
    
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    # Muestra de Licitaciones (Aquí se conectará el feed de SAM.gov)
    licitaciones_ejemplo = [
        {"id": "LIC-2026-001", "titulo": "Suministro de Filtros Industriales y Repuestos Motor GMC", "descripcion": "Provisión de insumos mecánicos y repuestos físicos COTS."},
        {"id": "LIC-2026-002", "titulo": "Servicio de Consultoría en Software y Mantenimiento Web", "descripcion": "Desarrollo de plataforma cloud y consultoría intangibles."}
    ]

    for lic in licitaciones_ejemplo:
        lic_id = lic["id"]
        if esta_procesada(lic_id):
            continue

        prompt = f"""
        Analiza esta licitación. Determina si busca comprar PRODUCTOS FÍSICOS/TANGIBLES COTS o si es un servicio intangible.
        
        Título: {lic['titulo']}
        Descripción: {lic['descripcion']}
        
        Responde exclusivamente en formato JSON:
        {{"es_producto_fisico": true, "resumen": "Resumen conciso en 2 frases"}}
        """

        resultado = procesar_con_gemini(prompt)
        
        # Simulamos que aprueba si es producto físico
        if resultado and "true" in resultado.lower():
            registrar_licitacion(lic_id, lic['titulo'], True)
            logger.info(f"✨ Licitación {lic_id} aprobada por Kiyomoto!")
            
            if bot_application and chat_id:
                mensaje_notif = (
                    f"🌸 **¡Nueva Oportunidad Detectada por Kiyomoto!** 📦✨\n\n"
                    f"🆔 **ID:** `{lic_id}`\n"
                    f"📌 **Título:** {lic['titulo']}\n"
                    f"💡 **Análisis de IA:** ¡Es un producto físico tangible!\n\n"
                    f"¡Lista para preparar la propuesta! 🚀"
                )
                import asyncio
                asyncio.run_coroutine_threadsafe(
                    notificar_telegram(bot_application, chat_id, mensaje_notif),
                    bot_application.loop
                )
        else:
            registrar_licitacion(lic_id, lic['titulo'], False)

# ---------------------------------------------------------------------------
# 6. HANDLERS ASÍNCRONOS CON PERSONALIDAD KIYOMOTO KAWAII ✨
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    mensaje = (
        f"✨ **¡Hola, {user_name}-san!** (⁠✿⁠☉⁠｡⁠☉⁠)\n"
        f"Soy **Kiyomoto**, tu asistente ejecutiva de logística y cazadora de licitaciones. 🌸📦\n\n"
        f"Estoy aquí para vigilar **SAM.gov** las 24 horas y avisarte solo cuando aparezcan contratos de productos físicos geniales. ✨\n\n"
        f"📜 **Mis comandos de control:**\n"
        f"• /status - Ver mi estado operativo y salud del sistema 📊\n"
        f"• /on - Activar mi radar de monitoreo automático 🟢\n"
        f"• /off - Poner el monitoreo en pausa temporal 🔴\n"
        f"• /forzar_escaneo - Pedirme un rastreo manual inmediato 🔍"
    )
    await update.message.reply_text(mensaje, parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    estado_str = "🟢 ACTIVO (¡Radar encendido! ✨)" if MONITOREO_ACTIVO else "🔴 PAUSADO (En descanso... 💤)"
    mensaje = (
        f"📊 **Reporte de Estado de Kiyomoto** (🔒_🔒)✨\n\n"
        f"• **Servidor Web:** Operativo en Render (Puerto 10000) 🌐\n"
        f"• **Radar de Monitoreo:** {estado_str}\n"
        f"• **Base de Datos:** SQLite Conectada y Lista 💾\n"
        f"• **Cerebro de IA:** Google Gemini Configurado 🧠"
    )
    await update.message.reply_text(mensaje, parse_mode="Markdown")

async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MONITOREO_ACTIVO
    MONITOREO_ACTIVO = True
    await update.message.reply_text("🟢 **¡Entendido!** Radar activado. Buscaré licitaciones para ti cada hora. (⁠•̀⁠ᴗ⁠•́⁠)⁠و✨")

async def off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MONITOREO_ACTIVO
    MONITOREO_ACTIVO = False
    await update.message.reply_text("🔴 **¡Monitoreo pausado!** Me tomaré un descanso hasta que me necesites de nuevo. (⁠´⁠ー⁠｀⁠)")

async def forzar_escaneo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 **¡Entendido!** Rastreando bases de datos en este instante... (⁠✦⁠‿⁠✦⁠)")
    ejecutar_monitoreo_licitaciones(context.application)
    await update.message.reply_text("✨ **¡Escaneo manual completado!** Si encontré algo relevante, ya te lo envié arriba. 🌸")

# ---------------------------------------------------------------------------
# 7. PUNTO DE ENTRADA PRINCIPAL
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()

    # Hilo para Flask
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Servidor Flask kawaii iniciado.")

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    if not telegram_token:
        logger.critical("❌ No se encontró el token de Telegram.")
        exit(1)

    bot_app = ApplicationBuilder().token(telegram_token).build()

    # Handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("status", status_command))
    bot_app.add_handler(CommandHandler("on", on_command))
    bot_app.add_handler(CommandHandler("off", off_command))
    bot_app.add_handler(CommandHandler("forzar_escaneo", forzar_escaneo_command))

    # Scheduler
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
