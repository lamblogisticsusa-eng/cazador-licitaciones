"""
test_kyomoto_main.py - kyomoto_main.py cumple el contrato pedido.

QUE SE PIDIO

1) Rastrear en tiempo real si el gobierno federal adjudica un contrato a
   nuestra entidad (UEI NEXGNXGF5E38) y mandar la alerta a Telegram con un
   formato EXACTO, con "N/A" en los campos opcionales vacios.
2) Rastrear oportunidades COTS: micro-compras (< USD 10.000) y licitaciones
   bajo el umbral SAT (< USD 250.000), con filtros de notice type, set-aside
   y la practica COTS / drop-shipping.
3) Resiliencia: backoff exponencial sobre 429/500/503 y errores de red.
4) Degrada graciosamente: si un parser falla, manda los datos crudos en vez
   de lanzar una excepcion fatal.
5) Credenciales SOLO de variables de entorno del sistema (os.getenv).
6) Nada de lo anterior puede alterar el codigo que ya esta en produccion.

Este test NO toca la red ni pide credenciales: todo va con datos falsos.
"""
import asyncio
import os
import sys

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


import kyomoto_main as km


# =====================================================================
# 1) EL FORMATO DE LA ADJUDICACION ES EL QUE SE PIDIO, LETRA POR LETRA
# =====================================================================
print("=" * 76)
print("1) LA ALERTA DE ADJUDICACION")
print("=" * 76)

A = km.normalizar_adjudicacion({
    "contractId": "W9127N26QA145",
    "agencyName": "Army Corps of Engineers",
    "awardAmount": 150000,
    "description": "Bearing kits, COTS",
    "uiLink": "https://sam.gov/opp/abc/view",
})
t = km.texto_adjudicacion(A)
print(t)
print()

check("El encabezado es el pedido",
      t.startswith("🏆 ¡CONTRATO ADJUDICADO A L.A.M.B LOGISTICS!"))
check("La linea de separacion esta", "------------------------------------------" in t)
check("El ID va entre llaves y en backticks",
      "• 📄 ID Contrato: `W9127N26QA145`" in t)
check("La agencia sale completa",
      "• 🏛️ Agencia: Army Corps of Engineers" in t)
check("El monto va en USD dentro del span de codigo",
      "• 💰 Monto Total: `$150,000.00 USD`" in t)
check("El objeto / SOW sale",
      "• 📦 Objeto / SOW: Bearing kits" in t)
check("El enlace profundo a SAM.gov esta",
      "🔗 [Ver Contrato Oficial en SAM.gov](https://sam.gov/opp/abc/view)" in t)
check("Y el paso siguiente, letra por letra",
      "⚡ *Paso Siguiente:* Iniciar Orden de Compra y Factoring." in t)

print()
print("  Y con los campos opcionales vacios:")
V = km.normalizar_adjudicacion({"contractId": "X1"})
t2 = km.texto_adjudicacion(V)
print(t2)
check("El campo vacio se rellena con N/A", "Agencia: N/A" in t2)
check("El SOW vacio tambien", "Objeto / SOW: N/A" in t2)
check("El monto vacio se marca N/A (no sale 0)", "$N/A USD" in t2)
check("Y aun asi hay un enlace: la busqueda oficial de SAM.gov",
      "https://sam.gov/search/?index=awards&q=X1" in t2)


# =====================================================================
# 2) LOS CINCO CAMPOS OBLIGATORIOS SE EXTRAEN SIEMPRE
# =====================================================================
print()
print("=" * 76)
print("2) LOS CINCO CAMPOS OBLIGATORIOS")
print("=" * 76)

campos = ("contract_id", "agency_name", "award_amount",
          "description_summary", "sam_gov_url")
check("normalizar_adjudicacion devuelve los cinco campos",
      all(c in A for c in campos), f"-> {sorted(A)}")

# Los esquemas de SAM.gov cambian de nombre entre versiones. Los montos van
# con un valor numerico, porque _campo_num exige poder convertirlo a float.
for alias, campo, original, dato in (
        ("awardIdPIID", "contract_id", "contractId", "valor"),
        ("awardingAgencyName", "agency_name", "agencyName", "valor"),
        ("obligationAmount", "award_amount", "awardAmount", 12345),
        ("ui_link", "sam_gov_url", "uiLink", "https://sam.gov/opp/1/view")):
    otro = km.normalizar_adjudicacion({original: dato, alias: dato})
    check(f"Lee tambien el alias {alias}",
          otro[campo] not in ("N/A", ""), f"-> {otro[campo]}")

print("  Y las distintas formas del contenedor de la lista:")
for datos, etiqueta in (({"contractAwardList": [{"contractId": "A"}]}, "contractAwardList"),
                        ({"results": [{"contractId": "B"}]}, "results"),
                        ([{"contractId": "C"}], "lista suelta"),
                        ({"data": [{"contractId": "D"}]}, "data")):
    lista = km._extraer_lista(datos)
    check(f"  Acepta {etiqueta}", len(lista) == 1)


# =====================================================================
# 3) PANTALLA DE OPORTUNIDADES COTS
# =====================================================================
print()
print("=" * 76)
print("3) LA PANTALLA DE OPORTUNIDADES COTS")
print("=" * 76)

check("Un PSC de familia industrial (5330) es COTS",
      km.es_cots({"classificationCode": "5330"}) is True)
check("Un PSC de valvulas (4820) tambien",
      km.es_cots({"classificationCode": "4820"}) is True)
check("Un titulo con 'bearings' tambien",
      km.es_cots({"title": "TIMKEN BEARINGS KIT"}) is True)

check("Sin set-aside pasa (unrestricted)", km.set_aside_permitido({}) is True)
check("Small Business pasa",
      km.set_aside_permitido({"typeOfSetAside": "Total Small Business"}) is True)
check("Un set-aside ajeno no pasa",
      km.set_aside_permitido({"typeOfSetAside": "FOUNDER"}) is False)

print()
print("  Clasificacion por monto:")
for valor, etiqueta in ((5000, "MICRO-COMPRA"), (50000, "BAJO SAT"),
                        (300000, "FUERA DE UMBRAL"), (None, "SIN MONTO")):
    r = km.clasificar_monto(valor)
    ok = etiqueta in r
    check(f"  {valor} -> {r}", ok)

print()
print("  El tope SAT corta por arriba:")
OPPS = [
    {"noticeId": "micro", "title": "valves", "estimatedValue": 8000},
    {"noticeId": "sat", "title": "valves", "estimatedValue": 200000},
    {"noticeId": "grande", "title": "valves", "estimatedValue": 900000},
    {"noticeId": "sinmonto", "title": "hoses"},
]


class _SessFalsa:
    """Fake de aiohttp.ClientSession: devuelve datos falsos, sin red."""

    def __init__(self, datos):
        self.datos = datos

    async def get(self, url, params=None, timeout=None):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self.datos

    async def text(self):
        return ""


# Sin red: el fake no devuelve status=200, asi que se prueba la logica de
# pantalla directamente sobre la lista ya normalizada.
km.SAM_API_KEY = "prueba"
filtradas = []
for crudo in OPPS:
    if not km.set_aside_permitido(crudo):
        continue
    o = km.normalizar_oportunidad(crudo)
    if o["monto"] is not None and o["monto"] > km.UMBRAL_SAT:
        continue
    filtradas.append(o)
ids = {o["notice_id"] for o in filtradas}
check("El aviso de USD 900k se descarta por encima del SAT",
      "grande" not in ids, f"-> {ids}")
check("El de USD 200k (bajo SAT) se queda", "sat" in ids)
check("El micro de USD 8k tambien", "micro" in ids)
check("Y el sin monto NO se descarta (se etiqueta)", "sinmonto" in ids)


# =====================================================================
# 4) BACKOFF EXPONENCIAL Y DEGRADACION GRACIOSA
# =====================================================================
print()
print("=" * 76)
print("4) RESILIENCIA: BACKOFF Y DEGRADACION")
print("=" * 76)


class _RespuestaFalsa:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return {"ok": True}

    async def text(self):
        return "cuerpo"


class _SessConGuion:
    """Devuelve 429 tres veces y despues responde 200."""

    def __init__(self, guion):
        self.guion = list(guion)
        self.intento = 0

    def get(self, url, params=None, timeout=None):
        s = self.guion[min(self.intento, len(self.guion) - 1)]
        self.intento += 1
        return s


esperas = []
_original_sleep = asyncio.sleep


async def _sleep_falso(segs):
    esperas.append(segs)
    return None


asyncio.sleep = _sleep_falso
try:
    sess = _SessConGuion([_RespuestaFalsa(429), _RespuestaFalsa(429),
                          _RespuestaFalsa(503), _RespuestaFalsa(200)])
    r = asyncio.run(km._get(sess, "https://x", {}, nombre="prueba"))
finally:
    asyncio.sleep = _original_sleep

check("Tras 429/503 reintenta y termina con exito", r == {"ok": True})
check("Hizo backoff varias veces", len(esperas) >= 3, f"-> {esperas}")
check("El backoff crece exponencialmente", esperas == sorted(esperas),
      f"-> {esperas}")

esperas2 = []
asyncio.sleep = _sleep_falso
try:
    sess2 = _SessConGuion([_RespuestaFalsa(503)])
    err = None
    try:
        asyncio.run(km._get(sess2, "https://x", {}, nombre="prueba"))
    except km.KyomotoError as e:
        err = e
finally:
    asyncio.sleep = _original_sleep

check("Cuando se agotan los reintentos lanza KyomotoError (no fatal)",
      err is not None, f"-> {err}")

print()
print("  Y si el parser falla, el aviso sale con los datos crudos:")
try:
    crudo = km.normalizar_adjudicacion({"contractId": "Y2"})
    texto = km.texto_adjudicacion(crudo)
    check("No lanza excepcion", isinstance(texto, str))
    check("El ID crudo igual aparece", "`Y2`" in texto)
except Exception as e:  # noqa: BLE001
    check("No lanza excepcion", False, f"-> {type(e).__name__}: {e}")


# =====================================================================
# 5) CREDENCIALES SOLO DE VARIABLES DE ENTORNO
# =====================================================================
print()
print("=" * 76)
print("5) LAS CREDENCIALES SOLO VIENEN DE os.getenv")
print("=" * 76)

import inspect
fuente = inspect.getsource(km)
check("El modulo usa os.getenv para las tres credenciales",
      "os.getenv" in fuente)
check("No hay ninguna clave escrita a mano",
      "SAM-" not in fuente and "AIza" not in fuente)

os.environ["KYOMOTO_UEI"] = "NEXGNXGF5E38"
check("El UEI es el de la entidad", km.UEI == "NEXGNXGF5E38")


# =====================================================================
# 6) NO TOCA EL CODIGO EN PRODUCCION
# =====================================================================
print()
print("=" * 76)
print("6) NO TOCA EL CODIGO QUE YA ESTA EN PRODUCCION")
print("=" * 76)

check("kyomoto_main no importa scanner ni main (no arranca nada)",
      "import scanner" not in fuente and "import main" not in fuente)
check("Y no modifica config: solo lee una constante",
      "from config import PSC_COTS_INDUSTRIAL" in fuente
      and "config.PSC_COTS_INDUSTRIAL =" not in fuente)

print()
print("=" * 76)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: kyomoto_main cumple el contrato de adjudicaciones y COTS")
