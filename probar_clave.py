"""
probar_clave.py - Diagnostica una clave de Gemini en 30 segundos.

Uso:
    python probar_clave.py                  -> lee del PORTAPAPELES (lo normal)
    python probar_clave.py "TU_CLAVE"       -> la pasa como argumento
    python probar_clave.py --pegar          -> la pide oculta en la terminal

No guarda la clave en ningun archivo ni la imprime completa.
"""
from __future__ import annotations

import getpass
import json
import sys
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta/models"
MODELOS = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite"]

CUERPO = json.dumps({
    "contents": [{"parts": [{"text": "Responde exactamente: OK"}]}],
    "generationConfig": {"maxOutput_tokens": 2048},
}).encode()


def leer_portapapeles() -> str:
    """PowerShell en el portapapeles del escritorio de Windows."""
    import subprocess
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=15,
        )
        return (r.stdout or "").strip()
    except Exception as e:
        print(f"No pude leer el portapapeles: {e}")
        return ""


def _pedir(url: str, cabeceras: dict) -> tuple[bool, str]:
    req = urllib.request.Request(url, data=CUERPO, headers=cabeceras, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            datos = json.loads(r.read())
            texto = datos["candidates"][0]["content"]["parts"][0].get("text", "")
            return True, f"respondio: {texto!r}"
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(cuerpo)["error"]
            return False, f"HTTP {e.code} {err.get('code')} - {err.get('status')}"
        except Exception:
            return False, f"HTTP {e.code} - {cuerpo[:160]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def probar(clave: str) -> bool:
    print("=" * 66)
    print("DIAGNOSTICO DE LA CREDENCIAL")
    print("=" * 66)
    print(f"  Longitud : {len(clave)} caracteres")
    print(f"  Prefijo  : {clave[:6]}...")
    print(f"  Puntos   : {clave.count('.')}  (una API key de Google no suele tener)")
    print()

    print("  1) Header x-goog-api-key  (metodo oficial actual)")
    for m in MODELOS:
        ok, det = _pedir(
            f"{BASE}/{m}:generateContent",
            {"Content-Type": "application/json", "x-goog-api-key": clave},
        )
        print(f"     {m:22} {'OK  ' if ok else 'FALLA'} {det if not ok else det}")
        if ok:
            print()
            print(f"  FUNCIONA con {m}. Usa esta en Render:")
            print(f"     GEMINI_MODEL={m}")
            print(f"     GEMINI_API_KEY=<la clave>")
            return True

    print()
    print("  2) Parametro ?key=  (metodo antiguo, claves estandar)")
    ok, det = _pedir(
        f"{BASE}/{MODELOS[0]}:generateContent?key={clave}",
        {"Content-Type": "application/json"},
    )
    print(f"     {'OK' if ok else 'FALLA'} {det}")

    print()
    print("  3) Authorization: Bearer  (OAuth)")
    ok, det = _pedir(
        f"{BASE}/{MODELOS[0]}:generateContent",
        {"Content-Type": "application/json", "Authorization": f"Bearer {clave}"},
    )
    print(f"     {'OK' if ok else 'FALLA'} {det}")

    print()
    print("  4) tokeninfo  (dice si es un token OAuth valido)")
    try:
        with urllib.request.urlopen(
            f"https://oauth2.googleapis.com/tokeninfo?access_token={clave}", timeout=25
        ) as r:
            print(f"     OK  {r.read().decode()[:150]}")
    except urllib.error.HTTPError as e:
        print(f"     FALLA HTTP {e.code} - {e.read().decode()[:120]}")
    except Exception as e:
        print(f"     FALLA {type(e).__name__}: {str(e)[:100]}")

    print()
    print("  5) Listar modelos (solo lectura, sin cuerpo)")
    try:
        req = urllib.request.Request(
            f"{BASE}?pageSize=1", headers={"x-goog-api-key": clave}
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            print(f"     OK  {json.loads(r.read())['models'][0]['name']}")
    except urllib.error.HTTPError as e:
        print(f"     FALLA HTTP {e.code} - {e.read().decode()[:110]}")
    except Exception as e:
        print(f"     FALLA {type(e).__name__}: {str(e)[:100]}")

    print()
    print("=" * 66)
    print("NINGUNA VIA FUNCIONO")
    print("=" * 66)
    print("""
  Antes de culpar a Google, descarta lo mas probable de todo:

  A) LA CADENA ESTA MAL COPIADA  <-- revisa esto primero
     Si copiaste la clave a mano,_LEYENDOLA DE UNA CAPTURA_, es casi seguro
     que un caracter este mal. Son 54 caracteres llenos de 1/l/I, 0/O y 5/S,
     que en esa fuente se ven iguales. Un solo caracter mal da 401.
     -> Usa el boton "Copiar clave" de AI Studio y pega el TEXTO.
     -> O en esta maquina:  python probar_clave.py   (lee el portapapeles)

  B) RESTRICCION DE IP U ORIGEN
     Render usa IPs dinamicas. Una clave restringida a tu IP local funciona
     en tu casa y falla en Render. Para un bot alojado, quita la restriccion.

  C) API GENERATIVE LANGUAGE NO HABILITADA
     En Cloud Console > APIs & Services > Biblioteca, busca
     "Generative Language API" y habilitala en el proyecto.

  D) La clave no es de AI Studio
     Si la copiaste de otro panel (Firebase, Cloud Console > credenciales),
     no sirve para Gemini aunque tenga el mismo formato.

  SOLUCION: en AI Studio, boton "Copiar clave" -> python probar_clave.py
  y pega aqui SOLO lo que el script imprima, nunca la clave.
""")
    return False


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--pegar":
        clave = getpass.getpass("Pega la clave: ")
    elif args:
        clave = args[0]
    else:
        print("Leyendo el portapapeles...")
        clave = leer_portapapeles()
        print(f"(pestana: {len(clave)} caracteres)\n")
    clave = clave.strip().strip('"').strip("'")
    if not clave:
        print("No se encontro ninguna clave en el portapapeles.")
        sys.exit(1)
    sys.exit(0 if probar(clave) else 1)
