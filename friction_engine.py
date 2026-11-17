# -*- coding: utf-8 -*-
"""
friction_engine.py - Lo que un pliego cuesta de atravesarlo.

Kyomoto ya filtra por puntuacion y por titulo, y aun asi se cuela el aviso
que en el encabezado parece una compra de catalogo y en el pliego pide
SUBSAFE, inspeccion de Nivel I o radiografia de soldadura. Esas son las
licitaciones TRAMPA: requisitos de calificacion e inspeccion que un
intermediario sin planta propia no puede satisfacer por mas dinero que
ponga, y que solo se ven leyendo el texto entero.

Tres reglas de casa, y las tres son el motivo de que este modulo exista
separado:

  1. AQUI NO CLASIFICA NINGUN MODELO. Gemini sigue detectando LOS HECHOS
     (eso ya hace); aqui se aplican IF-ELSE literales sobre el texto. Lo
     que sale de un modelo no se puede reproducir: una noche la misma
     licitacion saldria candidata y a la siguiente rechazada, y no
     habria ni forma de pincharlo con un assert.
  2. NADA DE RED. El modulo no importa sam_api, usaspending_client ni
     telegram_notify: recibe el texto y un Contexto ya preparado y
     devuelve un Diagnostico. Descarga y cache viven en pliego.py y
     sam_api.py; la ficha de Telegram unicamente pinta lo que aqui sale.
  3. TODO lo configurable son estructuras de datos: REGLAS_*, pesos,
     umbrales y etiquetas. Anadir un patron no toca logica de negocio.

DOS ESCALONES, y el motivo de no meterlo todo en el mismo cajon:

    CRITICA  -> AUTO_REJECT. Requisitos que un reseller NO puede
        satisfacer nunca: calificacion de material nuclear (SUBSAFE),
        nivel de inspeccion I / LI-SS, QMS de contrato de nivel superior,
        pruebas no destructivas de soldadura (radiografia, RT, RSS), los
        datos DI obligatorios (DI-MISC-80678 / 81020, DI-QCIC-81110),
        la prohibicion de reparar por soldadura, QALI, o disolver el
        small business set aside del que depende la oferta. No hay
        negociacion posible: el expediente no es para un intermediario.

    ALTA -> HIGH_FRICTION_REVIEW y BLOQUEA el RFQ. Friccion seria pero
        superable con canal, documentacion y tiempo: empaque militar
        (MIL-STD-2073 / 129), inspeccion de origen, distribuidor
        autorizado, CAC/PKI, GFE/GFM, DPAS/DO, DD250, CMMC, ITAR, FAT.
        Se puede tramitar, pero un borrador de RFQ "listo para enviar"
        seria mentir sobre lo rapido que es. Por eso no sale el bloque:
        sale el aviso de revision manual.

    MEDIA -> solo penaliza el score. Es el mismo 🟡 MEDIA que ya sabe
        complejidad.py (Buy American, Berry, TDP, plan de
        subcontratacion, ISO 9001): estorba, no descalifica.

Una licitacion sin texto tampoco es candidata: sin texto no hay filtros
que pasar, y habilitar el RFQ a ciegas seria exactamente el fallo que
este modulo existe para evitar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

import config

# ---------------------------------------------------------------------------
# Vocabulario: severidad de una regla y veredicto de un diagnostico.
# ---------------------------------------------------------------------------
CRITICA = "critica"
ALTA = "alta"
MEDIA = "media"
POSITIVA = "positiva"

AUTO_REJECT = "AUTO_REJECT"
HIGH_FRICTION_REVIEW = "HIGH_FRICTION_REVIEW"
CANDIDATE_RFQ = "CANDIDATE_RFQ"

VEREDICTOS = (AUTO_REJECT, HIGH_FRICTION_REVIEW, CANDIDATE_RFQ)

# Lo que se pinta en la ficha. El veredicto DEL DIAGNOSTICO es el literal
# ingles (es el contrato del dato, y se guarda asi en la base); la frase
# que lee el usuario es esta, en castellano, igual que hace
# complejidad.ETIQUETAS con la bandera de complejidad.
ETIQUETAS = {
    AUTO_REJECT: "🚫 Rechazo automático",
    HIGH_FRICTION_REVIEW: "⚠️ Fricción alta - revisión manual",
    CANDIDATE_RFQ: "✅ Candidato a RFQ",
}

# Pesos. Se leen como constants y no se reparten por el codigo a proposito:
# si un dia hay que subir la penalizacion de "alta", se sube en un sitio.
PESO_CRITICA = -100   # hunde el score a cero
PESO_ALTA = -25
PESO_MEDIA = -10
PESO_POSITIVA = 8
# Tope del bonus acumulado. Con el score partiendo de 100, sin tope una
# sola licitacion con tres señales buenas borraria cualquier penalizacion
# de las medias, y el numero dejaria de significar nada.
TOPE_BONUS = 24
BONUS_AGENCIA_CIVIL = 8
BONUS_SET_ASIDE = 8
BONUS_UNIDAD_SIMPLE = 8
BONUS_MARGEN_SANO = 8
BONUS_HISTORIAL = 6
PENALIZACION_CIERRA_PRONTO = -15
PENALIZACION_FUERA_DEL_FOCO = -10

# Agencias civiles: compran bienes comerciales y no suelen arrastrar los
# requisitos de calificacion de defensa. Se mira el nombre de la agencia,
# que ya viene en el aviso, no el texto del pliego.
AGENCIAS_CIVILES = (
    "DEPARTMENT OF VETERANS AFFAIRS", "VETERANS AFFAIRS", " VA ",
    "U.S. DEPARTMENT OF AGRICULTURE", "DEPARTMENT OF AGRICULTURE",
    "UNITED STATES FOREST SERVICE", "FOREST SERVICE",
    "ENVIRONMENTAL PROTECTION AGENCY", "GENERAL SERVICES ADMINISTRATION",
    "DEPARTMENT OF HOMELAND SECURITY", "HEALTH AND HUMAN SERVICES",
    "DEPARTMENT OF THE INTERIOR", "DEPARTMENT OF TRANSPORTATION",
    "DEPARTMENT OF COMMERCE", "NATIONAL AERONAUTICS",
    "NATIONAL SCIENCE FOUNDATION", "DEPARTMENT OF EDUCATION",
    "SOCIAL SECURITY ADMINISTRATION", "DEPARTMENT OF ENERGY",
    "DEPARTMENT OF LABOR", "SMALL BUSINESS ADMINISTRATION",
    "FOOD AND DRUG ADMINISTRATION", "NASA", "GSA", "EPA", "VA", "USDA",
    "NOAA", "NIH",
)

# Unidades en las que una sola linea se cotiza sin tener que levantar una
# fabrica. El resto (KG, GALLON, LINEAR FOOT con tolerancias) ya exige
# muestreo y certificado de lote.
UNIDADES_SIMPLES = {
    "EA", "EACH", "LOT", "BOX", "BX", "PK", "PACK", "SET", "ST", "PCE",
    "PCS", "CASE", "CT", "DR", "JB", "BG", "KT",
}


@dataclass(frozen=True)
class Regla:
    """Una regla: patron, severidad y la frase que se le muestra al usuario.

    `contexto` es un patron OPCIONAL que tiene que aparecer en el texto
    para que la regla cuente. Existe por una sola razon: siglas cortas
    como RT o CAC son ambiguas fuera de su terreno ("RT" en "room
    temperature", "CAC" en cualquier sigla interna), y una regla que
    dispara con una palabra suelta acaba rechazando licitaciones que no
    tienen nada raro.
    """

    id: str
    patron: str
    severidad: str
    etiqueta: str
    contexto: str | None = None
    peso: int = 0

    def buscar(self, texto: str) -> bool:
        """True si el patron (y su contexto, si lo tiene) esta en el texto."""
        if not texto:
            return False
        if not re.search(self.patron, texto, re.IGNORECASE):
            return False
        if self.contexto and not re.search(self.contexto, texto, re.IGNORECASE):
            return False
        return True


# ---------------------------------------------------------------------------
# Las reglas. Solo datos: patron + severidad + etiqueta.
# ---------------------------------------------------------------------------

# Filtro duro. Un intermediario sin planta propia no pasa ni pagando.
CRITICAS: tuple[Regla, ...] = (
    Regla("subsafe", r"\bSUBSAFE\b", CRITICA,
          "SUBSAFE: cualificación de seguridad nuclear"),
    Regla("nivel_i", r"\bLevel\s+I\b|\bLI\s*/\s*SS\b", CRITICA,
          "Inspección de Nivel I / LI-SS"),
    Regla("higher_level_qms", r"Higher[-\s]?Level\s+Contract\s+Quality",
          CRITICA, "Higher-Level Contract Quality (QMS de contrato)"),
    Regla("radiografia", r"\bRadiograph\w*", CRITICA,
          "Radiografía de soldadura"),
    Regla("rt", r"\bRT\b", CRITICA,
          "RT: radiografía por penetración",
          contexto=r"RADIOGRAPH|NON[-\s]?DESTRUCTIVE|\bNDT\b|WELD|FILM|PENETRANT|ULTRASONIC"),
    Regla("rss", r"\bRSS\b", CRITICA,
          "RSS: ensayo no destructivo sobre soldadura",
          contexto=r"RADIOGRAPH|WELD|\bNDT\b|INSPECT|FILM|PENETRANT|SOLDER"),
    Regla("di_misc_80678", r"\bDI-MISC-80678\b", CRITICA,
          "DI-MISC-80678 obligatorio (datos de contrato)"),
    Regla("di_misc_81020", r"\bDI-MISC-81020\b", CRITICA,
          "DI-MISC-81020 obligatorio (datos de contrato)"),
    Regla("di_qcic_81110", r"\bDI-QCIC-81110\b", CRITICA,
          "DI-QCIC-81110 obligatorio (datos de calidad)"),
    Regla("soldadura_reparacion",
          r"repair\s+welding\s+is\s+not\s+permitted", CRITICA,
          "Repair welding is not Permitted"),
    Regla("qali", r"\bQALI\b", CRITICA,
          "QALI: inspección y calidad obligatorias"),
    Regla("set_aside_disuelto",
          r"dissolve\s+(?:the\s+)?small\s+business\s+set[-\s]?aside"
          r"|dissolve\s+(?:the\s+)?set[-\s]?aside", CRITICA,
          "Disuelve el small business set aside"),
)

# Penalizacion critica: friccion alta, pero superable con canal y tiempo.
ALTAS: tuple[Regla, ...] = (
    Regla("dpas", r"\bDPAS\b", ALTA, "DPAS: prioridad de defensa"),
    Regla("do_rated", r"\bDO[-\s]rated\b|\brated\s+DO\b|\bDO[-\s]rating\b",
          ALTA, "DO rated (prioridad de defensa)"),
    Regla("mil_std_792", r"\bMIL-STD-792\b", ALTA, "MIL-STD-792"),
    Regla("gfe_gfm",
          r"Government[-\s]Furnished\s+(?:Material|Equipment|Property)"
          r"|\bGFM\b|\bGFE\b", ALTA,
          "GFE/GFM: material o equipo proporcionado por el gobierno"),
    Regla("ecds", r"\bECDS\b", ALTA, "ECDS: entrega electrónica obligatoria"),
    Regla("pki", r"\bPKI\b", ALTA, "PKI: certificado o firma digital"),
    Regla("cac", r"\bCAC\b", ALTA, "CAC: Common Access Card para acceder",
          contexto=r"CARD|PKI|SMART|CERTIF|COMMON\s+ACCESS|LOGIN|PORTAL|CREDENTIAL|SIGNATURE|DS\s+LOGON"),
    Regla("distribuidor_autorizado",
          r"proof\s+of\s+being\s+an\s+authori[sz]ed"
          r"|proof\s+of\s+authori[sz]ation"
          r"|authori[sz]ed\s+distribu?tor"
          r"|authori[sz]ed\s+reseller"
          r"|authori[sz]ed\s+source", ALTA,
          "Prueba de ser distribuidor/fuente autorizada"),
    Regla("mil_std_2073", r"\bMIL-STD-2073\b", ALTA,
          "MIL-STD-2073: empaque militar"),
    Regla("mil_std_129", r"\bMIL-STD-129\b", ALTA,
          "MIL-STD-129: marcado y empaque militar"),
    Regla("inspeccion_origen",
          r"Government\s+Source\s+Inspection|\bSource\s+Inspection\b"
          r"|inspection\s+at\s+(?:the\s+)?(?:contractor|manufacturer|origin|plant|supplier)",
          ALTA, "Inspección de origen / Government Source Inspection"),
    Regla("ciberseguridad",
          r"\bCMMC\b|NIST\s+SP\s+800-171|DFARS\s+252\.204-7012", ALTA,
          "Ciberseguridad CMMC / NIST SP 800-171"),
    Regla("itar", r"\bITAR\b", ALTA, "ITAR: control de exportación"),
    Regla("first_article", r"first\s+article\s+(?:testing|inspection)", ALTA,
          "First Article Testing / inspección de primer artículo"),
    Regla("csi", r"critical\s+safety\s+item", ALTA, "Critical Safety Item"),
    Regla("source_approval", r"source\s+approval\s+(?:request|requirement)",
          ALTA, "Source Approval obligatorio"),
    Regla("nmr", r"non[-\s]manufacturer\s+rule", ALTA,
          "Non-Manufacturer Rule"),
    Regla("sole_source", r"sole\s+source", ALTA,
          "Adquisición de fuente única"),
    Regla("origen_defensa",
          r"\bDD\s?250\b|Joint\s+Certification\s+Program|\bJCP\b"
          r"|certificate\s+of\s+conformance\s+to\s+origin", ALTA,
          "DD250 / JCP / certificado de origen de defensa"),
)

# Estorba pero no descalifica: solo penaliza el score.
MEDIAS: tuple[Regla, ...] = (
    Regla("buy_american", r"Buy\s+American", MEDIA, "Buy American Act"),
    Regla("berry", r"Berry\s+Amendment|\bBerry\s+Act\b", MEDIA,
          "Berry Amendment"),
    Regla("tdp", r"Technical\s+Data\s+Package|\bTDP\b", MEDIA,
          "Technical Data Package"),
    Regla("varias_ubicaciones",
          r"delivery\s+to\s+(?:multiple|several)\s+(?:locations|addresses|sites)"
          r"|multiple\s+delivery\s+locations", MEDIA,
          "Entrega en varias ubicaciones"),
    Regla("plan_subcontratacion",
          r"subcontracting\s+plan|FAR\s+52\.219-9", MEDIA,
          "Plan de subcontratación obligatorio"),
    Regla("iso_9001", r"\bISO\s?9001\b|\bAS9100\b", MEDIA,
          "ISO 9001 / AS9100 exigido"),
    Regla("seccion_889",
          r"section\s+889|FAR\s+52\.204-25", MEDIA,
          "Section 889 (proveedores de telecomunicaciones chinos)"),
)

# Senales buenas: el lado de la balanza que suele faltar.
POSITIVAS: tuple[Regla, ...] = (
    Regla("far_part_12", r"\bFAR\s+(?:Part\s+)?12\b", POSITIVA,
          "FAR Part 12: adquisición comercial"),
    Regla("empaque_comercial", r"Commercial\s+Packaging", POSITIVA,
          "Commercial Packaging"),
    Regla("fob_destino",
          r"FOB\s+Destination|inspection\s+at\s+destination", POSITIVA,
          "FOB Destination / inspección en destino"),
)

REGLAS: tuple[Regla, ...] = CRITICAS + ALTAS + MEDIAS + POSITIVAS
_PESOS = {
    CRITICA: PESO_CRITICA,
    ALTA: PESO_ALTA,
    MEDIA: PESO_MEDIA,
    POSITIVA: PESO_POSITIVA,
}


@dataclass(frozen=True)
class Contexto:
    """Lo que NO esta en el texto pero cambia el juicio.

    Se inyecta, nunca se descarga aqui: este modulo no sabe que hay una
    API de SAM.gov detras, ni una de USAspending, ni un chat de Telegram.
    Asi el motor se prueba con datos puestos a mano y sin red.
    """

    titulo: str = ""
    agencia: str = ""
    set_aside: str = ""
    unidad: str = ""
    limite: str | None = None          # fecha de cierre, en cualquier formato
    monto: float | None = None         # valor estimado del aviso
    margen_neto: float | None = None   # margen neto ya con factoring
    historial_favorable: bool | None = None
    hoy: date | None = None


@dataclass(frozen=True)
class Diagnostico:
    """Resultado estructurado y trazable del juicio de friccion.

    Los seis primeros campos son el contrato con el resto del sistema (y
    lo que se guarda en la base); los ultimos son los cruces que pinta la
    ficha de Telegram y el gate del RFQ. Todo es JSON puro, con
    `a_dict()` / `desde_dict()` para ir y volver de store.
    """

    verdict: str
    score: int
    days_to_deadline: int | None
    matched_rules: tuple[str, ...]
    key_findings: tuple[str, ...]
    viability_summary: str
    criticas: tuple[str, ...] = ()
    altas: tuple[str, ...] = ()
    avisos: tuple[str, ...] = ()
    rfq_habilitado: bool = False
    motivo_rfq: str = ""

    def a_dict(self) -> dict:
        """Forma JSON segura, para store.guardar_analisis."""
        return {
            "verdict": self.verdict,
            "score": int(self.score),
            "days_to_deadline": self.days_to_deadline,
            "matched_rules": list(self.matched_rules),
            "key_findings": list(self.key_findings),
            "viability_summary": self.viability_summary,
            "criticas": list(self.criticas),
            "altas": list(self.altas),
            "avisos": list(self.avisos),
            "rfq_habilitado": bool(self.rfq_habilitado),
            "motivo_rfq": self.motivo_rfq,
        }

    @staticmethod
    def desde_dict(d: dict) -> "Diagnostico | None":
        """Vuelve desde lo que salio de la base. None si no hay diagnostico."""
        if not isinstance(d, dict) or not str(d.get("verdict") or "").strip():
            return None
        if d.get("verdict") not in VEREDICTOS:
            return None

        def _tupla(v) -> tuple[str, ...]:
            if isinstance(v, (list, tuple)):
                return tuple(str(x) for x in v)
            return ()

        dias = d.get("days_to_deadline")
        return Diagnostico(
            verdict=str(d["verdict"]),
            score=int(d.get("score") or 0),
            days_to_deadline=int(dias) if isinstance(dias, (int, float)) else None,
            matched_rules=_tupla(d.get("matched_rules")),
            key_findings=_tupla(d.get("key_findings")),
            viability_summary=str(d.get("viability_summary") or ""),
            criticas=_tupla(d.get("criticas")),
            altas=_tupla(d.get("altas")),
            avisos=_tupla(d.get("avisos")),
            rfq_habilitado=bool(d.get("rfq_habilitado")),
            motivo_rfq=str(d.get("motivo_rfq") or ""),
        )


# ---------------------------------------------------------------------------
# Hechos que se arrancan del texto para el resumen ejecutivo.
# ---------------------------------------------------------------------------
_RE_UNIDAD = re.compile(
    r"\b(\d{1,9}(?:[.,]\d{3})*)\s*"
    r"(EA|EACH|EACHES|LOT|LOTS|BOX|BX|PK|PACK|SET|SETS|ST|PCE|PCS|CASE|CT)\b",
    re.IGNORECASE,
)
_RE_DIAS = re.compile(
    r"\b(\d{1,4})\s+(?:calendar\s+|working\s+|business\s+)?days?\b",
    re.IGNORECASE,
)
_RE_ENTREGA_CTX = re.compile(
    r"(after|from|upon|of)\s+(receipt|award|acceptance|order|po|contract|placement)",
    re.IGNORECASE,
)
_RE_ENMIENDA_NUM = re.compile(
    r"\b(?:Amendment|AMD|Addendum|Mod)\s*0*(\d{1,3})\b", re.IGNORECASE
)
_RE_ENMIENDA_PAL = re.compile(r"\b(?:amendment|addendum|modification)\w*\b",
                              re.IGNORECASE)


def _unidades(texto: str) -> str:
    """La primera cantidad+unidad que aparezca escrita, o ""."""
    m = _RE_UNIDAD.search(texto or "")
    if not m:
        return ""
    cifra = m.group(1)
    if "," in cifra or "." in cifra:
        cifra = cifra.replace(",", "")
    return f"{cifra} {m.group(2).upper()}"


def _plazo_entrega(texto: str) -> str:
    """El plazo de entrega que este escrito, con su contexto si lo trae."""
    if not texto:
        return "plazo de entrega: no consta"
    m = _RE_DIAS.search(texto)
    if not m:
        return "plazo de entrega: no consta"
    dias = m.group(1)
    cola = texto[m.end(): m.end() + 160]
    ctx = _RE_ENTREGA_CTX.search(cola)
    if ctx:
        return f"entrega {dias} días ({ctx.group(1)} {ctx.group(2)})"
    return f"entrega {dias} días"


def _enmiendas(texto: str) -> str:
    """Si el pliego ha sido enmendado, y cuantas veces."""
    if not texto:
        return "enmiendas: sin texto que revisar"
    nums = sorted({int(n) for n in _RE_ENMIENDA_NUM.findall(texto)})
    if nums:
        vistos = ", ".join(str(n) for n in nums[:4])
        extra = "..." if len(nums) > 4 else ""
        return f"enmiendas: {len(nums)} ({vistos}{extra})"
    menciones = len(_RE_ENMIENDA_PAL.findall(texto))
    if menciones:
        return f"enmiendas: {menciones} mención(es) sin número"
    return "enmiendas: ninguna detectada"


def _pedido(texto: str, ctx: Contexto) -> str:
    """Que se pide realmente: la cantidad si esta escrita, si no el objeto."""
    u = _unidades(texto)
    if u:
        return u
    titulo = " ".join(str(ctx.titulo or "").split())
    if titulo:
        return titulo[:90]
    return "sin dato en el texto"


def _exigencia(n_crit: int, n_alta: int, n_media: int) -> str:
    if n_crit:
        return f"crítica ({n_crit} requisito(s) de calificación o inspección)"
    if n_alta:
        return f"alta ({n_alta} requisito(s) de friccion)"
    if n_media:
        return f"media ({n_media} condicion(es) que estorban)"
    return "baja: compra comercial de catálogo"


def _agencia_civil(agencia: str) -> bool:
    a = str(agencia or "").strip().upper()
    if not a:
        return False
    # Palabra entera. "VA" tiene que coincidir con "VA" o con "DEPARTMENT
    # OF VETERANS AFFAIRS", no con cualquier sitio donde aparezcan esas
    # dos letras seguidas: un bonus de agencia civil que se gana quien no
    # lo merece ensucia el score sin que se pueda ver de donde salio.
    return any(re.search(r"\b" + re.escape(s.strip()) + r"\b", a)
               for s in AGENCIAS_CIVILES)


def _unidad_simple(unidad: str) -> bool:
    u = str(unidad or "").strip().upper().rstrip(".")
    return u in UNIDADES_SIMPLES


def _a_fecha(v) -> datetime | None:
    """Cualquier formato de fecha de aviso a datetime, sin lanzar."""
    if not v:
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        t = str(v).strip()
        if not t:
            return None
        dt = None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y",
                    "%m/%d/%Y %H:%M:%S"):
            try:
                dt = datetime.strptime(t[:19], fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _dias_al_cierre(limite, hoy) -> int | None:
    cierre = _a_fecha(limite)
    if cierre is None:
        return None
    base = hoy or datetime.now(timezone.utc)
    if isinstance(base, datetime):
        base_d = base.astimezone(timezone.utc).date() if base.tzinfo else base.date()
    else:
        base_d = base
    return (cierre.date() - base_d).days


def _mora_tiempo(dias: int | None) -> bool:
    """Cierre a menos de DIAS_MIN_CIERRE dias: aviso, no descalificacion."""
    if dias is None:
        return False
    return dias < int(getattr(config, "DIAS_MIN_CIERRE", 5))


def _fuera_del_foco(monto) -> bool:
    if monto is None:
        return False
    try:
        tope = float(getattr(config, "MONTO_SIMPLIFICADO_USD", 250000.0))
        return float(monto) > tope
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# El motor.
# ---------------------------------------------------------------------------
def evaluar(texto: str, contexto: Contexto | None = None) -> Diagnostico:
    """
    Juzga un pliego y devuelve el diagnostico. Nunca lanza.

    `texto` es el material completo: descripcion oficial + adjuntos +
    pliego largo, todo ya limpio por pliego.py. `contexto` lleva lo que no
    esta en el texto (agencia, set-aside, monto, margen, fecha de cierre).

    El orden de la decision es fijo y deliberado:

        1. cualquier regla CRITICA  -> AUTO_REJECT (score a 0)
        2. texto vacio              -> HIGH_FRICTION_REVIEW (score a 0)
        3. cualquier regla ALTA     -> HIGH_FRICTION_REVIEW
        4. score >= umbral          -> CANDIDATE_RFQ
        5. en otro caso             -> HIGH_FRICTION_REVIEW

    Y el score NUNCA contradice el veredicto: si el veredicto no es
    candidato, el numero se recorta por debajo del umbral. Un ficha que
    diga "Score 99/100" y "Fricción alta - revisión manual" a la vez es
    una ficha en la que no se puede confiar.
    """
    ctx = contexto or Contexto()
    bruto = str(texto or "")
    cuerpo = bruto.strip()
    dias = _dias_al_cierre(ctx.limite, ctx.hoy)

    # --- 1. Que reglas disparan ---
    coincidencias: list[Regla] = [r for r in REGLAS if r.buscar(cuerpo)]

    criticas = tuple(r.etiqueta for r in coincidencias if r.severidad == CRITICA)
    altas = tuple(r.etiqueta for r in coincidencias if r.severidad == ALTA)
    medias = tuple(r.etiqueta for r in coincidencias if r.severidad == MEDIA)
    ids = tuple(r.id for r in coincidencias)

    # --- 2. Score ---
    if not cuerpo:
        score = 0
    else:
        score = 100
        bonus = 0
        for r in coincidencias:
            peso = r.peso or _PESOS.get(r.severidad, 0)
            if r.severidad == POSITIVA:
                bonus += peso
            else:
                score += peso
        # Senales que no estan en el texto sino en los metadatos.
        if _agencia_civil(ctx.agencia):
            bonus += BONUS_AGENCIA_CIVIL
        if ctx.set_aside and not criticas and not _falso_set_aside(coincidencias):
            bonus += BONUS_SET_ASIDE
        if _unidad_simple(ctx.unidad):
            bonus += BONUS_UNIDAD_SIMPLE
        if ctx.margen_neto is not None:
            try:
                if float(ctx.margen_neto) >= 100 * float(
                        getattr(config, "MARGEN_NETO_MIN", 0.12)):
                    bonus += BONUS_MARGEN_SANO
            except (TypeError, ValueError):
                pass
        if ctx.historial_favorable:
            bonus += BONUS_HISTORIAL
        score += min(bonus, TOPE_BONUS)
        if _mora_tiempo(dias):
            score += PENALIZACION_CIERRA_PRONTO
        if _fuera_del_foco(ctx.monto):
            score += PENALIZACION_FUERA_DEL_FOCO
        score = max(0, min(100, score))

    # --- 3. Veredicto ---
    avisos: list[str] = []
    if _mora_tiempo(dias):
        avisos.append(
            f"Cierre en {dias} días (menos de "
            f"{int(getattr(config, 'DIAS_MIN_CIERRE', 5))}): no da tiempo "
            "a verificar stock, empaque y envío.")
    if _fuera_del_foco(ctx.monto):
        try:
            tope = float(getattr(config, "MONTO_SIMPLIFICADO_USD", 250000.0))
            avisos.append(
                f"Monto de {ctx.monto:,.0f} USD por encima del foco de "
                f"compra simplificada ({tope:,.0f} USD).")
        except (TypeError, ValueError):
            pass

    umbral = int(getattr(config, "SCORE_CANDIDATO_MIN", 70))

    if criticas:
        verdict = AUTO_REJECT
        score = 0
    elif not cuerpo:
        verdict = HIGH_FRICTION_REVIEW
        score = 0
        avisos.insert(0, "Sin texto que evaluar (descripción y pliego vacíos).")
    elif altas:
        verdict = HIGH_FRICTION_REVIEW
        score = min(score, max(0, umbral - 1))
    elif score >= umbral:
        verdict = CANDIDATE_RFQ
    else:
        verdict = HIGH_FRICTION_REVIEW

    # --- 4. Hechos para el resumen ---
    hallazgos = (
        f"Qué se pide: {_pedido(cuerpo, ctx)}",
        f"Exigencia técnica/documental: {_exigencia(len(criticas), len(altas), len(medias))}",
        f"Plazos: cierre en {dias} días; {_plazo_entrega(cuerpo)}"
        if dias is not None
        else f"Plazos: fecha de cierre no legible; {_plazo_entrega(cuerpo)}",
        _enmiendas(cuerpo).capitalize(),
        f"Veredicto: {ETIQUETAS[verdict]} ({score}/100)",
    )

    habilitado = verdict == CANDIDATE_RFQ
    motivo = (
        "Candidato a RFQ: fricción baja y score por encima del umbral."
        if habilitado else
        ("Rechazo automático: requisitos que un intermediario sin planta "
         "propia no puede satisfacer."
         if verdict == AUTO_REJECT else
         ("Sin texto no se habilita el RFQ: todavía no hay nada que "
          "evaluar." if not cuerpo else
          "Fricción alta: revisión manual antes de redactar el RFQ."))
    )

    return Diagnostico(
        verdict=verdict,
        score=score,
        days_to_deadline=dias,
        matched_rules=ids,
        key_findings=hallazgos,
        viability_summary=_resumen(cuerpo, ctx, verdict, dias,
                                   criticas, altas, medias),
        criticas=criticas,
        altas=altas,
        avisos=tuple(avisos),
        rfq_habilitado=habilitado,
        motivo_rfq=motivo,
    )


def _falso_set_aside(coincidencias: list[Regla]) -> bool:
    """El set-aside ya no vale si el pliego dice que se disuelve."""
    return any(r.id == "set_aside_disuelto" for r in coincidencias)


def _resumen(texto: str, ctx: Contexto, verdict: str,
             dias: int | None, criticas: tuple, altas: tuple,
             medias: tuple) -> str:
    """
    El analisis de viabilidad en tres frases: que se pide, que exige el
    pliego y que significa eso para un intermediario sin planta propia.

    Se arma aqui con hechos extraidos, no con un modelo: una conclusion
    que no se puede reproducir tampoco se puede testear, y este bloque es
    el primero que lee el usuario.
    """
    piezas: list[str] = []

    pedido = _pedido(texto, ctx)
    piezas.append(f"Piden {pedido}." if not pedido.startswith("sin ")
                  else "El texto no deja ver que se pide.")

    if verdict == AUTO_REJECT:
        piezas.append(
            "El pliego exige "
            + ", ".join(list(criticas)[:3])
            + (f" y {len(criticas) - 3} más" if len(criticas) > 3 else "")
            + ": requisitos de calificación e inspección que no se "
              "satisfacen comprando y revendiendo.")
        piezas.append("Veredicto: no es para un intermediario sin planta "
                      "propia; RFQ bloqueado.")
    elif verdict == HIGH_FRICTION_REVIEW:
        if not texto:
            piezas.append("No hay descripción ni pliego que leer, así que "
                          "no se puede juzgar la fricción.")
            piezas.append("Veredicto: revisión manual antes de ofertar; "
                          "RFQ en pausa.")
        else:
            lista = list(altas) or list(medias)
            piezas.append(
                "El pliego añade " + (", ".join(lista[:3])
                                      if lista else "condiciones que estorban")
                + (f" y {len(lista) - 3} más" if len(lista) > 3 else "")
                + ": se puede tramitar, pero exige canal, documentación o "
                  "inspección.")
            piezas.append("Veredicto: viable solo tras revisión manual; el "
                          "RFQ queda en pausa.")
    else:
        piezas.append("No aparecen requisitos de calificación, inspección ni "
                      "canal autorizado.")
        piezas.append("Veredicto: compra comercial que un intermediario sin "
                      "planta propia puede llevar; RFQ habilitado.")

    cola: list[str] = []
    if dias is not None:
        cola.append(f"Cierre en {dias} días")
    ent = _plazo_entrega(texto)
    if not ent.endswith("no consta"):
        cola.append(ent)
    enm = _enmiendas(texto)
    if not enm.startswith("enmiendas: ninguna"):
        cola.append(enm)
    if cola:
        piezas.append("; ".join(cola) + ".")

    return " ".join(piezas)


def evaluar_pegado(pegado: str, contexto: Contexto | None = None) -> Diagnostico:
    """
    Entrada interna para bloques largos de especificaciones PEGADOS a mano.

    Esta es la funcion que expone el motor a quien tenga texto suelto (un
    pliego copiado del portal, un correo del comprador, un CDRL) sin
    pasar por SAM.gov: normaliza el trozo y lo juzga igual que a cualquier
    otro aviso, con el MISMO motor y las MISMAS reglas.

        friction_engine.evaluar_pegado(texto_pegado_de_correo)

    Solo normaliza; no descarga, no cachea y no envia nada. El pliego se
    limpia en pliego.texto_pegado para que aqui no haya dos versiones de
    "como se limpia un texto".
    """
    try:
        import pliego
        bruto = pliego.texto_pegado(pegado)
    except Exception:
        bruto = str(pegado or "")
    return evaluar(bruto, contexto)


def rfq_permitido(a: dict) -> tuple[bool, str]:
    """
    (puede salir el bloque de RFQ?, motivo).

    Es EL gate de la Fase 9 y vive aqui y no en Telegram, para que la
    ficha, el registro y cualquier otro consumidor decidan lo mismo.

    Cuatro casos, y los cuatro son decisiones, no accidentes:

    1. `rfq_aprobado_manual` -> si. Aprobacion explicita, manda sobre el
       resto: quien revisa el expediente a mano sabe algo que el motor no.
    2. config.RFQ_APROBAR_TODO -> si. La misma aprobacion, a escala de
       despliegue, para cuando el operador quiera el RFQ siempre.
    3. Sin clave `diagnostico` -> si. "No se juzga lo que no se pudo
       evaluar": un analisis anterior a la Fase 9 (o uno que fallo) no
       puede quedar sin RFQ por un dato que nunca se calculo. Si se
       bloqueara aqui, todas las fichas antiguas y todos los tests de la
       Fase 5 se romperian por una ausencia, que es justo lo contrario
       de un juicio.
    4. Con diagnostico -> solo CANDIDATE_RFQ.
    """
    if not isinstance(a, dict):
        return True, "no es un analisis"
    if a.get("rfq_aprobado_manual"):
        return True, "aprobacion manual del operador"
    if getattr(config, "RFQ_APROBAR_TODO", False):
        return True, "aprobacion global (RFQ_APROBAR_TODO)"
    bruto = a.get("diagnostico")
    d = bruto if isinstance(bruto, Diagnostico) else Diagnostico.desde_dict(
        bruto if isinstance(bruto, dict) else {})
    if d is None:
        return True, "sin diagnostico de friccion"
    if d.verdict == CANDIDATE_RFQ:
        return True, "candidato a RFQ"
    return False, d.motivo_rfq or ETIQUETAS[d.verdict]
