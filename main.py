import os
import sqlite3
import requests
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from google import genai

# --- CONFIGURACIÓN DE APIS Y ENTORNOS ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8896302862:AAHWOOP1ohoFeQqMYBv5ewB7ydrKkZcWDio")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SAM_API_KEY = os.getenv("SAM_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

DB_NAME = "licitaciones.db"

# Inicializar cliente de Gemini con la nueva SDK (google-genai)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

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

# --- BÚSQUEDA Y EVALUACIÓN DE LICITACIONES (SAM.GOV + GEMINI) ---
def buscar_licitaciones_sam():
    if not SAM_API_KEY:
        print("⚠️ SAM_API_KEY no configurada.")
        return []
    
    # Rango de fechas: últimos 2 días
    hoy = datetime.now()
    hace_dos_dias = hoy - timedelta(days=2)
    
    posted_from = hace_dos_dias.strftime("%m/%d/%Y")
    posted_to = hoy.strftime("%m/%d/%Y")
    
    url = f"https://api.sam.gov/prod/opportunities/v2/search?api_key={SAM_API_KEY}&postedFrom={posted_from}&postedTo={posted_to}&limit=100"
    
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
    1. Determina si se trata de la compra/suministro de PRODUCTOS FÍSICOS (materiales, equipos, repuestos, componentes, insumos, etc.). Rechaza o marca como NO VIABLE si es puramente servicios intangibles, consultorías o servicios de personal.
    2. Realiza un análisis financiero estimado:
       - Presupuesto o Valor Estimado del Contrato.
       - Costo Aprox. de Adquisición/Proveedor.
       - Margen y Ganancia Neta Proyectada.
    3. Sugiere términos de búsqueda en inglés para encontrar proveedores/distribuidores clave en EE. UU.

    FORMATO DE RESPUESTA:
    Si la oportunidad NO cumple con los parámetros (ej. es solo servicios o no es adecuada), responde únicamente: "NO_VIABLE".

    Si la oportunidad SÍ CUMPLE con los parámetros de productos físicos, responde en el siguiente formato exacto en español y estructurado para Telegram:

    📦 **{title}**
    🔢 **Solicitud:** {solicitation_number}
    
    📝 **Descripción del Producto:**
    [Breve resumen claro del producto o bien requerido]

    💰 **Análisis Financiero Proyectado:**
    • **Valor Est. Contrato:** [Monto aproximado en USD]
    • **Costo Est. Proveedor:** [Monto aproximado en USD]
    • **Ganancia Neta Est.:** [Monto aproximado en USD] ([Porcentaje]% de margen)

    🔍 **Distribuidores Sugeridos:**
    Buscar en EE. UU.: `[Términos de búsqueda sugeridos]`

    🔗 **Enlace SAM.gov:** {ui_link}
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

# --- LÓGICA PRINCIPAL DE ESCANEO ---
def ejecutar_monitoreo_licitaciones(app_telegram):
    print("🔍 Iniciando rastreo en SAM.gov...")
    oportunidades = buscar_licitaciones_sam()
    
    count_notificadas = 0
    for opp in oportunidades:
        notice_id = opp.get("noticeId")
        if not notice_id or esta_procesada(notice_id):
            continue
        
        # Evaluar con Gemini
        analisis = analizar_oportunidad_con_gemini(opp)
        
        if analisis and "NO_VIABLE" not in analisis:
            # Enviar notificación a Telegram
            try:
                app_telegram.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=analisis,
                    parse_mode="Markdown"
                )
                count_notificadas += 1
            except Exception as e:
                print(f"⚠️ Error al enviar mensaje a Telegram: {e}")
        
        marcar_como_procesada(notice_id)
        
    print(f"✅ Rastreo completado. Notificaciones enviadas: {count_notificadas}")

# --- COMANDOS Y HANDLERS DE TELEGRAM ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✨ **Kiyomoto Logistics Bot** está activa y lista.\n\n"
        "Comandos disponibles:\n"
        "• `/forzar_escaneo` - Limpia la base de datos y reevalúa el lote actual de SAM.gov.",
        parse_mode="Markdown"
    )

async def forzar_escaneo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧹 **Limpiando historial y reevaluando 100 licitaciones con análisis financiero...** (⁠✦⁠_⁠✦⁠)", parse_mode="Markdown")
    
    # 1. Limpiamos la base de datos para forzar la reevaluación completa
    limpiar_historial_db()
    
    # 2. Ejecutamos el monitoreo
    ejecutar_monitoreo_licitaciones(context.application)
    
    await update.message.reply_text("✨ **¡Escaneo completado!** Revisa arriba las oportunidades clasificadas. 🌸", parse_mode="Markdown")

# --- SERVIDOR FLASK (KEEP ALIVE / HEALTH CHECK) ---
server = Flask(__name__)

@server.route("/")
def home():
    return "Kiyomoto Logistics Bot está activo y funcionando.", 200

# --- APLICACIÓN PRINCIPAL ---
def main():
    init_db()
    
    # Configuración de python-telegram-bot
    telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("forzar_escaneo", forzar_escaneo_command))
    
    # Configurar programador en segundo plano (APScheduler) para escaneos periódicos cada 4 horas
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=ejecutar_monitoreo_licitaciones,
        trigger="interval",
        hours=4,
        args=[telegram_app],
        id="ejecutar_monitoreo_licitaciones",
        replace_existing=True
    )
    scheduler.start()
    
    print("✨ Kiyomoto lista y escuchando...")
    
    # Iniciar Telegram Polling en segundo plano y servidor Web Flask
    telegram_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    # Si se ejecuta directamente, arranca la aplicación
    main()
