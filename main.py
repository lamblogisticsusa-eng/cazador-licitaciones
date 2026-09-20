import os
import sys
import threading
from flask import Flask

# Inicialización de la aplicación Flask
app = Flask(__name__)

@app.route('/')
def health_check():
    """Ruta raíz para verificar que el servicio está activo (Health Check)."""
    return "Bot Cazador de Licitaciones activo y funcionando.", 200

def run_telegram_bot():
    """Ejecuta la lógica del bot de Telegram en un hilo independiente."""
    try:
        # Importamos la función o cliente de tu bot
        # Ajusta la importación según la estructura original de tu proyecto
        import bot  # o 'from bot import main'
        if hasattr(bot, 'main'):
            bot.main()
        elif hasattr(bot, 'run'):
            bot.run()
        else:
            print("Bot cargado correctamente.")
    except ImportError:
        print("Aviso: No se encontró un módulo 'bot.py' independiente. Ejecutando estructura base.")
    except Exception as e:
        print(f"Error al iniciar el hilo del bot de Telegram: {e}")

if __name__ == '__main__':
    # Lanzar el bot de Telegram en un hilo en segundo plano (daemon)
    bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
    bot_thread.start()

    # Obtener el puerto que Render asigna dinámicamente
    port = int(os.environ.get('PORT', 10000))
    
    # Iniciar servidor web Flask escuchando en todas las interfaces de red
    app.run(host='0.0.0.0', port=port)
