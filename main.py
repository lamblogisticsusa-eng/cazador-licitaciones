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
# 4. INTEGRACIÓN DE GOOGLE GEMINI (MODELOS VIGENTES Y LISTA DE FALLBACK)
# ---------------------------------------------------------------------------
def procesar_con_gemini(prompt: str) -> str:
    """
    Intenta procesar el prompt con la versión más reciente de la API de Gemini.
    Itera sobre una lista de modelos vigentes en caso de que alguno falle.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY no está configurada.")
        return "Error: Clave de API de Gemini no configurada."

    client = genai.Client(api_key=api_key)
    
    # Modelos activos recomendados
    modelos_disponibles = [
        "gemini-2.5-flash",
        "gemini-1.5-flash"
    ]

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
# 5. TAREA PROGRAMADA (MONITOR DE LICITACIONES / SCHEDULER)
# ---------------------------------------------------------------------------
def ejecutar_monitoreo_licitaciones(bot_application):
    logger.info("Iniciando ciclo de monitoreo de licitaciones...")
    
    # Ejemplo de estructura de licitaciones obtenidas (adaptar a tu API/Scraper)
    licitaciones_ejemplo = [
        {"id": "LIC-2026-001", "titulo": "Suministro de Filtros Industriales y Repuestos Motor GMC", "descripcion": "Licitación para provisión de insumos mecánicos y repuestos."},
        {"id": "LIC-2026-002", "titulo": "Servicio de Consultoría en Software y Mantenimiento Web", "descripcion": "Desarrollo de plataforma cloud y consultoría de sistemas."}
    ]

    for lic in licitaciones_ejemplo:
        lic_id = lic["id"]
        
        if esta_procesada(lic_id):
            logger.info(f"Omitiendo {lic_id} por estar procesada anteriormente.")
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
            logger.info(f"Procesado exitoso de {lic_id}.")
            registrar_licitacion(lic_id, lic['titulo'], True)
        else:
            logger.info(f"Omitiendo {lic_id} por no cumplir criterio o fallar en el procesamiento.")
            registrar_licitacion(lic_id, lic['titulo'], False)

# ---------------------------------------------------------------------------
# 6. HANDLERS DEL BOT DE TELEGRAM
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    mensaje = (
        f"Hola {user_name}, el sistema **Kiyomoto Logistics / Cazador de Licitaciones** está en línea.\n\n"
        "Comandos disponibles:\n"
        "/status - Verificar el estado del servicio\n"
        "/forzar_escaneo - Iniciar un escaneo de licitaciones manualmente"
    )
    await update.message.reply_text(mensaje, parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🟢 El bot está activo, Flask respondiendo y el monitoreo programado correctamente.")

async def forzar_escaneo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Iniciando escaneo manual de licitaciones...")
    ejecutar_monitoreo_licitaciones(context.application)
    await update.message.reply_text("✅ Escaneo manual completado.")

# ---------------------------------------------------------------------------
# 7. PUNTO DE ENTRADA PRINCIPAL
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # A. Inicializar Base de Datos
    init_db()

    # B. Levantar Flask en un hilo secundario
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Servidor web Flask iniciado en segundo plano.")

    # C. Obtener Token de Telegram
    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    if not telegram_token:
        logger.critical("Error: TELEGRAM_TOKEN no está definido en las variables de entorno.")
        exit(1)

    # D. Construir Aplicación de Telegram
    bot_app = ApplicationBuilder().token(telegram_token).build()

    # Registrar Handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("status", status_command))
    bot_app.add_handler(CommandHandler("forzar_escaneo", forzar_escaneo_command))

    # E. Configurar APScheduler
    scheduler = BackgroundScheduler(timezone="UTC")
    # Se programa la tarea periódica (ej. cada 1 hora)
    scheduler.add_job(
        ejecutar_monitoreo_licitaciones,
        "interval",
        hours=1,
        args=[bot_app],
        id="job_monitoreo_licitaciones"
    )
    scheduler.start()
    logger.info("Scheduler iniciado con éxito.")

    # F. Iniciar Polling de Telegram en el Hilo Principal
    # 'drop_pending_updates=True' descarta peticiones previas colgadas y elimina el conflicto 404
    logger.info("Iniciando Polling del Bot de Telegram...")
    bot_app.run_polling(drop_pending_updates=True)
