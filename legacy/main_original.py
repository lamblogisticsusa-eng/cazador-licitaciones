import os
import sqlite3
import asyncio
import requests
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from google import genai

# --- CONFIGURACIÓN DE APIS Y ENTORNOS ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SAM_API_KEY = os.getenv("SAM_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

DB_NAME = "licitaciones.db"
BOT_ACTIVO = True  # Estado global del bot

# Inicializar cliente de Gemini con SDK oficial
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# --- BASE DE DATOS LOCAL ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS licitaciones_procesadas (
            notice_id TEXT PRIMARY KEY,
            fecha_procesado TEXT
        )
    ''')
    conn.commit()
    conn.close()

def esta_procesada(notice_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT 1 FROM licitaciones_procesadas WHERE notice_id = ?", (notice_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

def marcar_como_procesada(notice_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO licitaciones_procesadas (notice_id, fecha_procesado) VALUES (?, ?)",
              (notice_id, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def limpiar_historial_db():
    """Limpia el registro de licitaciones procesadas para reevaluarlas todas."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM licitaciones_procesadas")
    conn.commit()
    conn.close()

# --- ENVÍO SEGURO DE MENSAJES (PREVIENE ERRORES DE MARKDOWN) ---
def enviar_mensaje_telegram(texto, chat_id=None):
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ TELEGRAM_BOT_TOKEN no configurado.")
        return
    
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not target_chat:
        print("⚠️ TELEGRAM_CHAT_ID no configurado.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Intento 1: Enviar con formato Markdown
    payload = {
        "chat_id": target_chat,
        "text": texto,
        "parse_mode": "Markdown"
    }
    
    try:
        res = requests.post(url, json=payload, timeout=10)
        # Intento 2: Si Telegram rebota el formato, enviar como texto plano limpio
        if res.status_code != 200:
            payload.pop("parse_mode")
            requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"⚠️ Error al enviar mensaje a Telegram: {e}")

# --- BÚSQUEDA Y EVALUACIÓN DE LICITACIONES ---
def buscar_licitaciones_sam():
    if not SAM_API_KEY:
        print("⚠️ SAM_API_KEY no configurada.")
        return []
    
    hoy = datetime.now()
    hace_diez_dias = hoy - timedelta(days=10)
    
    posted_from = hace_diez_dias.strftime("%m/%d/%Y")
    posted_to = hoy.strftime("%m/%d/%Y")
    
    url = f"https://api.sam.gov/prod/opportunities/v2/search?api_key={SAM_API_KEY}&postedFrom={posted_from}&postedTo={posted_to}&limit=250"
    
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return data.get("opportunitiesData", [])
        else:
            print(f"⚠️ Error al consultar SAM.gov: {response.status_code}")
            return []
    except Exception as e:
        print(f"⚠️ Excepción al conectar con SAM.gov: {e}")
        return []

def analizar_oportunidad_con_gemini(opp):
    if not ai_client:
        print("⚠️ GEMINI_API_KEY no configurada.")
        return "NO_VIABLE"

    title = opp.get("title", "Sin título")
    description = opp.get("description", "Sin descripción disponible.")
    solicitation_number = opp.get("solicitationNumber", "N/A")
    ui_link = opp.get("uiLink", "")
    
    prompt = f"""
    Actúa como un experto analista financiero y de licitaciones gubernamentales de EE. UU.
    Evalúa la siguiente oportunidad de contratación:

    Título: {title}
    Número de Solicitud: {solicitation_number}
    Descripción: {description}

    REQUISITOS DE EVALUACIÓN:
    1. Determina si es una licitación en la cual se pueda postular entregando PRODUCTOS FÍSICOS O BIENES MATERIALES (equipos, suministros, piezas, consumibles, herramientas, etc.). Marca como NO VIABLE únicamente si es un servicio intangible puro, consultoría o contratación de personal sin suministro físico.
    2. Realiza un análisis financiero estimado y estratégico:
       - Presupuesto o Valor Estimado del Contrato.
       - Costo Aprox. de Adquisición/Proveedor.
       - Margen de Ganancia Neta Proyectada.
       - Recomienda el precio exacto o rango a licitar (Offer Price) para maximizar la probabilidad de ganar manteniendo un excelente margen.
    3. Sugiere términos de búsqueda en inglés para encontrar distribuidores en EE. UU.

    FORMATO DE RESPUESTA:
    Si la oportunidad NO cumple con los parámetros, responde únicamente: "NO_VIABLE".

    Si la oportunidad SÍ CUMPLE, responde en el siguiente formato exacto en español (manteniendo un toque amable y profesional):

    📦 {title}
    🔢 Solicitud: {solicitation_number}
    
    📝 Descripción del Producto:
    [Breve resumen claro del producto o bien a entregar]

    💰 Análisis Financiero Proyectado:
    • Valor Est. Contrato: [Monto aprox. USD]
    • Costo Est. Proveedor: [Monto aprox. USD]
    • Ganancia Neta Est.: [Monto aprox. USD] ([Porcentaje]% de margen)

    🎯 Estrategia de Oferta Recomendada:
    • Oferta Sugerida para Licitar: [Monto recomendado en USD para ganar la licitación con buen margen]

    🔍 Distribuidores Sugeridos:
    Buscar en EE. UU.: [Términos de búsqueda sugeridos]

    🔗 Enlace SAM.gov: {ui_link}
    """

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text.strip() if response.text else "NO_VIABLE"
    except Exception as e:
        print(f"⚠️ Error al invocar Gemini API: {e}")
        return "NO_VIABLE"

def ejecutar_monitoreo_licitaciones(chat_id_target=None):
    global BOT_ACTIVO
    if not BOT_ACTIVO:
        print("⏸️ Monitoreo pausado por comando /off.")
        return 0

    print("🔍 Iniciando rastreo en SAM.gov...")
    oportunidades = buscar_licitaciones_sam()
    
    count_notificadas = 0
    for opp in oportunidades:
        notice_id = opp.get("noticeId")
        if not notice_id or esta_procesada(notice_id):
            continue
        
        analisis = analizar_oportunidad_con_gemini(opp)
        
        if analisis and "NO_VIABLE" not in analisis:
            enviar_mensaje_telegram(analisis, chat_id=chat_id_target)
            count_notificadas += 1
        
        marcar_como_procesada(notice_id)
        
    print(f"✅ Rastreo completado. Notificaciones enviadas: {count_notificadas}")
    return count_notificadas

# --- HANDLERS DE TELEGRAM ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola Amo, Kyomoto reportandose >< es hora de trabajar!\n\n"
        "✨ Lista para ayudarte con las licitaciones. Comandos disponibles:\n"
        "• /forzar_escaneo - Limpia el historial y realiza un escaneo inmediato (~10 días).\n"
        "• /off - Pausa el monitoreo automático.\n"
        "• /on - Reactiva el monitoreo automático."
    )

async def off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ACTIVO
    BOT_ACTIVO = False
    await update.message.reply_text("⏸️ Monitoreo automático pausado, Amo (⁠.⁠–⁠ Anchor ⁠.⁠)")

async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ACTIVO
    BOT_ACTIVO = True
    await update.message.reply_text("▶️ Monitoreo automático reactivado! De vuelta al trabajo (⁠✦⁠_⁠✦⁠)")

async def forzar_escaneo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text("🧹 Limpiando historial e iniciando escaneo completo en SAM.gov... ¡Un momento, Amo! (⁠•⁠̀⁠ᴗ⁠•⁠́⁠)⁠و")
    
    # Ejecutar la búsqueda de forma no bloqueante
    loop = asyncio.get_running_loop()
    
    def tarea():
        limpiar_historial_db()
        return ejecutar_monitoreo_licitaciones(chat_id_target=chat_id)

    total = await loop.run_in_executor(None, tarea)
    await context.bot.send_message(chat_id=chat_id, text=f"✨ ¡Escaneo completado con éxito, Amo! Se encontraron y notificaron {total} oportunidades viables (⁠≧⁠◡⁠≦⁠)")

# --- SERVIDOR FLASK (HEALTH CHECK) ---
server = Flask(__name__)

@server.route("/")
def home():
    return "Kiyomoto Logistics Bot está activo.", 200

def ejecutar_flask():
    port = int(os.environ.get("PORT", 10000))
    server.run(host="0.0.0.0", port=port, use_reloader=False)

# --- APLICACIÓN PRINCIPAL ---
def main():
    init_db()
    
    # Servidor Flask para Render
    import threading
    thread_flask = threading.Thread(target=ejecutar_flask, daemon=True)
    thread_flask.start()
    
    # Configurar Telegram Bot
    telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("on", on_command))
    telegram_app.add_handler(CommandHandler("off", off_command))
    telegram_app.add_handler(CommandHandler("forzar_escaneo", forzar_escaneo_command))
    
    # Configurar Scheduler (Monitoreo cada 4 horas)
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=ejecutar_monitoreo_licitaciones,
        trigger="interval",
        hours=4,
        id="ejecutar_monitoreo_licitaciones",
        replace_existing=True
    )
    scheduler.start()
    
    print("✨ Kiyomoto lista y escuchando...")
    telegram_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
