"""
Nivel de complejidad de una licitacion: la bandera que decide si el RFQ es
una compra comercial de catalogo o un expediente que puede atascarse.

Tres niveles, y solo tres, porque son los que se pueden ACTUAR:

    🚩 ALTA COMPLEJIDAD   DLA critica + FAT / CSI / ITAR / SAR / NMR
    🟡 COMPLEJIDAD MEDIA  empaque militar, Buy American, Berry o
                          entrega en varias ubicaciones
    🟢 OPTIMA             lo demas: COTS comercial de catalogo

COMO SE DECIDE, y esto es lo importante de este modulo:

    Gemini NO clasifica. Gemini detecta LOS HECHOS (en el campo "cumple"
    trae los gatillos que encuentra escritos, y en "entregas" las
    direcciones), y aqui se aplican las REGLAS, que son un if-else
    literal. No es una preferencia estetica: una clasificacion que sale
    de un modelo no se puede reproducir, no se puede testear, y una
    noche la misma licitacion puede salir 🟢 y a la siguiente 🚩. Con
    los hechos separados de las reglas, el resultado es siempre el mismo
    y se puede pinchar con un assert.

    Es el mismo criterio que ya usa el resto del repo con la estrategia
    de oferta: Gemini aporta, Python decide (ver
    gemini_analyzer.calcular_estrategia_oferta).

Tambien es lo que hace util el interruptor config.
FILTER_OUT_HIGH_RISK_DLA: filtrar por el NIVEL es filtrar por una regla
comprobable, no por el humor de un modelo.

NINGUN campo de aqui se inventa. Si los gatillos no estan (los analisis
antiguos se guardaron antes de existir este campo), no se declara ningun
riesgo: se dice "sin datos", que es distinto de "no hay riesgo". Un
aviso 🟢 que en realidad era 🚩 es exactamente el error que cuesta un
expediente.
"""

from __future__ import annotations

import re

# Las tres claves. Se guardan en ingles y sin emoji: la clave es dato, el
# emoji es presentacion, y mezclar los dos hace que buscar "ALTA" en la
# base de datos dependa de que nadie haya cambiado el emoji.
ALTA = "ALTA"
MEDIA = "MEDIA"
OPTIMA = "OPTIMA"

# Como se PINTA en Telegram.
ETIQUETAS = {
    ALTA: "\U0001f6a9 ALTA COMPLEJIDAD",
    MEDIA: "\U0001f7e1 COMPLEJIDAD MEDIA",
    OPTIMA: "\U0001f7e2 \u00d3PTIMA",
}

# --- Los gatillos -------------------------------------------------------
#
# Vocabulario CERRADO: es la lista literal que se le pide a Gemini en el
# prompt. Cada entrada es (nombre canonico, variantes que lo escriben).
#
# El nombre canonico es lo unico que llega a guardarse (ver canonizar), y
# eso cierra dos huecos de una vez: no puede entrar un gatillo inventado,
# y no puede colarse "NOT ITAR" fingiendo que la licitacion restringe
# exportacion cuando dice lo contrario.
_GATILLOS_ALTA = (
    ("First Article Testing (FAT)",
     ("FIRST ARTICLE TESTING", "FIRST ARTICLE", "FAT")),
    ("Critical Safety Item (CSI)",
     ("CRITICAL SAFETY ITEM", "CSI")),
    ("ITAR", ("ITAR",)),
    ("Source Approval Request (SAR)",
     ("SOURCE APPROVAL REQUEST", "SOURCE APPROVAL", "SAR")),
    ("Non-Manufacturer Rule (NMR) sin exencion SBA",
     ("NON-MANUFACTURER RULE", "NMR")),
)

_GATILLOS_MEDIA = (
    # MIL-STD-129 cuenta igual que 2073: los dos son empaque militar, y
    # separarlos dejaria una licitacion con empaque militar en 🟢 solo
    # porque el numero fuera el otro.
    ("Empaque militar MIL-STD", ("MIL-STD-2073", "MIL-STD-129")),
    ("Buy American Act (FAR 52.225-1)",
     ("BUY AMERICAN ACT", "FAR 52.225-1")),
    ("Berry Amendment", ("BERRY AMENDMENT",)),
)

_GATILLOS = _GATILLOS_ALTA + _GATILLOS_MEDIA

# Una negacion delante del gatillo lo anula: "ITAR not applicable" es el
# caso contrario al que se busca. Se deja "WITHOUT" fuera de la lista a
# proposito, porque "NMR without SBA waiver" ES el gatillo y no su
# negacion.
_NEGACION = re.compile(r"\b(NOT|N/A|NONE)\b", re.IGNORECASE)

# DLA. La oficina importa: el criterio pedido es DLA Aviation o DLA Land
# and Maritime, no cualquier DLA. Si el aviso solo dice "Defense Logistics
# Agency" sin oficina, NO se declara critica: exagerar el riesgo hacia
# arriba descarta licitaciones que eran buenas, y ese error no se
# recupera. Quien SUPRIME alertas tiene que dudar, no quien las pinta.
_DLA = ("DLA", "DEFENSE LOGISTICS AGENCY")
_OFICINA_CRITICA = ("AVIATION", "LAND AND MARITIME", "LAND & MARITIME")


def _coincide(texto: str, variante: str) -> bool:
    """Limites de palabra: "FAT" no cas con "FATIGUE" ni "ITAR" con "MILITAR"."""
    return re.search(r"\b" + re.escape(variante) + r"\b", texto) is not None


def canonizar(valores) -> list[str]:
    """
    Los gatillos que trae el analisis, reducidos a sus nombres canonicos.

    Es la UNICA puerta de entrada a "cumple". Lo que no reconoce se
    tira en vez de guardarse: una lista con lo que el modelo dijo con sus
    propias palabras no se puede comprobar, y guardarlo seria dar por
    bueno lo que aun no se ha entendido.
    """
    fuera: list[str] = []
    for bruto in valores or ():
        t = str(bruto or "").strip().upper()
        if not t or _NEGACION.search(t):
            continue
        for nombre, variantes in _GATILLOS:
            if any(_coincide(t, v) for v in variantes):
                if nombre not in fuera:
                    fuera.append(nombre)
                break
    return fuera


def _dla_critica(agencia: str) -> bool:
    """DLA Aviation o DLA Land and Maritime, dicho de las dos formas posibles."""
    u = (agencia or "").upper()
    if not any(_coincide(u, d) for d in _DLA):
        return False
    return any(o in u for o in _OFICINA_CRITICA)


def evaluar(a: dict) -> tuple[str, str]:
    """
    (clave, motivo) para el analisis `a`.

    `clave` es ALTA, MEDIA u OPTIMA. `motivo` es la frase de UNA linea que
    va detras de la bandera en Telegram, y tiene que decir POR QUE: una
    bandera sin motivo no se puede discutir ni se puede aprender a usar.
    """
    agencia = str(a.get("agencia") or "")
    cumple = canonizar(a.get("cumple"))
    entregas = a.get("entregas")

    # --- 🚩 ALTA: DLA critica Y ademas uno de los gatillos duros -------
    if _dla_critica(agencia):
        for nombre, _ in _GATILLOS_ALTA:
            if nombre in cumple:
                oficina = ("Aviation" if "AVIATION" in agencia.upper()
                           else "Land and Maritime")
                return ALTA, f"DLA {oficina} + {nombre}"

    # --- 🟡 MEDIA: empaque militar, origen o multi-entrega -------------
    for nombre, _ in _GATILLOS_MEDIA:
        if nombre in cumple:
            return MEDIA, nombre

    if isinstance(entregas, (list, tuple)) and len(entregas) > 1:
        return MEDIA, f"Multi-entrega ({len(entregas)} ubicaciones)"

    # --- 🟢 OPTIMA ------------------------------------------------------
    # Por descarte, y no por definicion: es el UNICO reparto que cubre
    # "toda" la licitacion, que es lo que se pide. Por eso el motivo nunca
    # afirma que sea COTS: dice lo que se pudo comprobar, que es lo unico
    # que se puede defender.
    if a.get("cumple") is None and a.get("entregas") is None:
        return OPTIMA, "Sin datos de cumplimiento (analisis anterior)"
    return OPTIMA, "Sin gatillos de riesgo detectados"


def riesgo_alto_dla(a: dict) -> bool:
    """True solo para 🚩: es lo que mira config.FILTER_OUT_HIGH_RISK_DLA."""
    return evaluar(a)[0] == ALTA
