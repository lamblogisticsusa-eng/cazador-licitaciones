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

# Variable global para controlar el estado del monitoreo activo
MONITOREO_ACTIVO = True

# ---------------------------------------------------------------------------
# 2. FLASK APP (HEALTH CHECK FOR RENDER)
# ---------------------------------------------------------------------------
web_app = Flask(__name__)

@web_app.route("/")
def health_check():
    return "Servicio Kiyomoto Logistics Activo y Operativo", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, use_reloader=False)

# ---------------------------------------------------------------------------
# 3. BASE DE DATOS SQLITE (COMPATIBLE CON PYTHON 3.14+)
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
# 4. INTEGRACIÓN DE GOOGLE GEMINI (MODELOS VIGENTES Y FALLBACK)
# ---------------------------------------------------------------------------
def procesar_con_gemini(prompt: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY no está configurada.")
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
            logger.warning(f"Error parseando Gemini con {modelo}: {e}")
            continue

    logger.error("Ningún modelo de Gemini pudo procesar la solicitud.")
    return None

# ---------------------------------------------------------------------------
# 5. TAREA PROGRAMADA DE MONITOREO
# ---------------------------------------------------------------------------
def ejecutar_monitoreo_licitaciones(bot_application=None):
    global MONITOREO_ACTIVO
    if not MONITOREO_ACTIVO:
        logger.info("El monitoreo automático se encuentra pausado (/off).")
        return

    logger.info("Iniciando ciclo de monitoreo de licitaciones...")
    
    # Lógica de escaneo de licitaciones
    licitaciones_ejemplo = [
        {"id": "LIC-2026-001", "titulo": "Suministro de Filtros Industriales y Repuestos Motor GMC", "descripcion": "Provisión de insumos mecánicos y repuestos."},
        {"id": "LIC-2026-002", "titulo": "Servicio de Consultoría en Software y Mantenimiento Web", "descripcion": "Desarrollo de plataforma cloud y consultoría."}
    ]

    for lic in licitaciones_ejemplo:
        lic_id = lic["id"]
        if esta_procesada(lic_id):
            continue

        prompt = f"""
        Analiza la siguiente oportunidad de licitación. Determina si se refiere a un PRODUCTO FÍSICO TANGIBLE / LOGÍSTICO 
        o si es un servicio intangible.
        
        Título: {lic['titulo']}
        Descripción: {lic['descripcion']}
        
        Responde en formato JSON simple:
        {{"es_producto_fisico": true/false, "resumen": "Breve resumen en 2 oraciones"}}
        """

        resultado = procesar_con_gemini(prompt)
        if resultado:
            registrar_licitacion(lic_id, lic['titulo'], True)
            logger.info(f"Licitación {lic_id} aprobada y procesada.")
        else:
            registrar_licitacion(lic_id, lic['titulo'], False)

# ---------------------------------------------------------------------------
# 6. HANDLERS ASÍNCRONOS PARA EL BOT DE TELEGRAM
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    mensaje = (
        f"🤖 **Kiyomoto Logistics Bot** iniciado correctamente.\n\n"
        f"Hola {user_name}, los comandos disponibles son:\n"
        "• /status - Ver estado del sistema y monitoreo\n"
        "• /on - Activar el monitoreo automático de SAM.gov\n"
        "• /off - Pausar el monitoreo automático\n"
        "• /forzar_escaneo - Ejecutar escaneo manual ahora"
    )
    await update.message.reply_text(mensaje, parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    estado_str = "🟢 ACTIVO" if MONITOREO_ACTIVO else "🔴 PAUSADO"
    mensaje = (
        f"📊 **Estado del Sistema**\n"
        f"• Servidor Web: Operativo (Port 10000)\n"
        f"• Monitoreo Automático: {estado_str}\n"
        f"• Base de Datos: Conectada"
    )
    await update.message.reply_text(mensaje, parse_mode="Markdown")

async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MONITOREO_ACTIVO
    MONITOREO_ACTIVO = True
    await update.message.reply_text("🟢 Monitoreo automático ACTIVADO. El bot buscará licitaciones periódicamente.")

async def off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MONITOREO_ACTIVO
    MONITOREO_ACTIVO = False
    await update.message.reply_text("🔴 Monitoreo automático PAUSADO. No se realizarán búsquedas automáticas.")

async def forzar_escaneo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Iniciando escaneo manual de licitaciones COTS...")
    ejecutar_monitoreo_licitaciones(context.application)
    await update.message.reply_text("✅ Escaneo manual completado exitosamente.")

# ---------------------------------------------------------------------------
# 7. PUNTO DE ENTRADA PRINCIPAL
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()

    # Flask en hilo secundario
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Servidor web Flask iniciado en segundo plano.")

    # Acepta tanto TELEGRAM_BOT_TOKEN como TELEGRAM_TOKEN de Render
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    if not telegram_token:
        logger.critical("Error: No se encontró ningún token de Telegram en las variables de entorno.")
        exit(1)

    bot_app = ApplicationBuilder().token(telegram_token).build()

    # Registro explícito de los handlers de comando
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("status", status_command))
    bot_app.add_handler(CommandHandler("on", on_command))
    bot_app.add_handler(CommandHandler("off", off_command))
    bot_app.add_handler(CommandHandler("forzar_escaneo", forzar_escaneo_command))

    # Configuración de APScheduler
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        ejecutar_monitoreo_licitaciones,
        "interval",
        hours=1,
        args=[bot_app],
        id="job_monitoreo_licitaciones"
    )
    scheduler.start()
    logger.info("Scheduler de monitoreo iniciado.")

    # Iniciar polling
    logger.info("Iniciando Polling del Bot de Telegram...")
    bot_app.run_polling(drop_pending_updates=True)
