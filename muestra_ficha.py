"""
muestra_ficha.py - Manda UNA ficha de ejemplo al chat, tal cual llegara.

No inventa una licitacion: reutiliza el caso real de la suite de formato
(FA8601-26-Q-0182, la oportunidad de la USAF que se uso para acordar el
formato), con la economia ya cerrada y coherente. Importarlo desde alli en vez
de reescribirlo a mano garantiza que las claves son las mismas que espera
telegram_notify.formatear_analisis: si el esquema cambiara, esto falla en vez
de mandar una ficha con campos vacios.

Sirve para:
  1. Probar el ULTIMO eslabon, el que nunca se habia probado: que una ficha
     analizada llegue entera a Telegram, con el HTML bien cerrado.
  2. Que el usuario vea el formato exacto antes del primer barrido real.

    python muestra_ficha.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALTAN = [v for v in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
          if not os.environ.get(v)]
if FALTAN:
    print("Faltan en el entorno: " + ", ".join(FALTAN))
    sys.exit(2)

# Se toma el FICHA tal cual de la suite de formato, sin tocarlo.
_f = io.open("test_formato.py", encoding="utf-8").read()
_i = _f.index("FICHA = {")
_j = _f.index("\n}\n", _i) + 3
_ns = {"float": float, "int": int}
exec(compile(_f[_i:_j], "test_formato.py:FICHA", "exec"), _ns)
FICHA = _ns["FICHA"]

import telegram_notify as tn

html = tn.formatear_analisis(FICHA)
print("=" * 70)
print("  FICHA QUE LLEGARA A TU CHAT")
print("=" * 70)
print()
print(html)
print()
print("=" * 70)
print(f"  Longitud: {len(html)} caracteres (limite de Telegram: 4096 por trozo)")

# El HTML tiene que estar balanceado o Telegram lo rechaza entero.
import re

for etiqueta, patron in (("<b>", r"<b>"), ("</b>", r"</b>"),
                         ("<code>", r"<code>"), ("</code>", r"</code>"),
                         ("<a href>", r"<a href="), ("</a>", r"</a>")):
    n = len(re.findall(patron, html))
    if etiqueta in ("<b>", "</b>", "<code>", "</code>", "<a href=", "</a>"):
        pass
print(f"  <b>={len(re.findall(r'<b>', html))}  </b>={len(re.findall(r'</b>', html))}")
print(f"  <code>={len(re.findall(r'<code>', html))}  "
      f"</code>={len(re.findall(r'</code>', html))}")
print(f"  <a href={len(re.findall(r'<a href=', html))}  "
      f"</a>={len(re.findall(r'</a>', html))}")

ok = tn.enviar(os.environ["TELEGRAM_CHAT_ID"], html, html_mode=True)
print()
print("  Envio a Telegram:", "OK, revisa el chat" if ok else "FALLO")
sys.exit(0 if ok else 1)
