from threading import Thread

from flask import Flask


app = Flask(__name__)


@app.route("/")
def home():
    return "Cazador de Licitaciones está activo."


def _run_server():
    app.run(host="0.0.0.0", port=8080, use_reloader=False)


def keep_alive():
    server_thread = Thread(target=_run_server, daemon=True)
    server_thread.start()