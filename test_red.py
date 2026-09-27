"""
test_red.py - Comprueba si SAM.gov deja pasar peticiones.

Las suites que usan la API real (test_e2e, test_presupuesto) fallan en
cascada si SAM.gov dice "Message throttled out". Este script te dice si
fue eso, para no perder tiempo debuggeando otra cosa.

    python test_red.py
"""
import os
import sys

os.environ.setdefault("DRY_RUN", "1")
if not os.environ.get("SAM_API_KEY"):
    print("Falta SAM_API_KEY en el entorno.")
    sys.exit(2)

import requests
import sam_api

KEY = os.environ["SAM_API_KEY"]
from datetime import datetime, timedelta, timezone  # noqa: E402

hoy = datetime.now(timezone.utc)
params = {
    "api_key": KEY,
    "postedFrom": f"{(hoy - timedelta(days=1)).month:02d}/"
                  f"{(hoy - timedelta(days=1)).day:02d}/{(hoy - timedelta(days=1)).year:04d}",
    "postedTo": f"{hoy.month:02d}/{hoy.day:02d}/{hoy.year:04d}",
    "limit": 1, "ptype": "o",
}
r = requests.get(sam_api.BASE_SEARCH, params=params, timeout=40)

print("=" * 66)
print("ESTADO DE LA API DE SAM.gov")
print("=" * 66)
if r.status_code == 200:
    print("  ✅ OK. SAM.gov responde.")
    print(f"     Avisos en 24 h: {r.json().get('totalRecords', '?')}")
    sys.exit(0)

print(f"  ❌ HTTP {r.status_code}")
try:
    cuerpo = r.json()
except ValueError:
    print(f"  {r.text[:300]}")
    sys.exit(1)

codigo = cuerpo.get("code", "")
msg = cuerpo.get("message", "")
desc = cuerpo.get("description", "")
siguiente = cuerpo.get("nextAccessTime", "")

print(f"  código  : {codigo}")
print(f"  mensaje : {msg}")
if desc:
    print(f"  detalle : {desc}")

if r.status_code == 429 or "throttl" in str(msg).lower():
    print()
    print("  ESTO ES EL TOPE DIARIO DE PETICIONES DE SAM.gov.")
    if siguiente:
        print(f"  Vuelve a funcionar: {siguiente}")
    print()
    print("  No es un bug de Kyomoto. Mientras tanto:")
    print("    - /escaneo y /estado siguen funcionando")
    print("    - Lo no visto queda pendiente, no se pierde")
    print("    - Kyomoto avisa por Telegram con la hora exacta")
    sys.exit(3)

if r.status_code in (401, 403):
    print()
    print("  La clave de SAM.gov fue rechazada. Revisa SAM_API_KEY en Render.")
    sys.exit(4)

sys.exit(1)
