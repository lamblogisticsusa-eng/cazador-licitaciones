"""
filters.py - Pre-filtro de oportunidades. Coste CERO en API de IA.

Funciona en dos fases para no malgastar cuota de Gemini:

  Fase A (solo titulo + NAICS + PSC):  descarta de golpe la mayoria del ruido.
  Fase B (titulo + NAICS + PSC + descripcion):  reordena las que pasaron.

Regla de oro: si NAICS_SERVICIOS dice que es servicio, es servicio, aunque el
titulo diga "supply". Por eso el veto de NAICS gana sobre todo lo demas.
"""
from __future__ import annotations

import re

import config
import sam_api

_NORMAL = re.compile(r"[^a-z0-9 ]+")


def _texto(valor) -> str:
    return _NORMAL.sub(" ", str(valor or "").lower())


def naics_de(opp: dict) -> str:
    """Devuelve el prefijo NAICS de 3 digitos. La API puede dar naicsCode o
    naicsCodes[] (lista), y a veces ya viene con guiones."""
    crudo = opp.get("naicsCode") or ""
    if not crudo:
        lista = opp.get("naicsCodes") or []
        crudo = lista[0] if lista else ""
    digitos = re.sub(r"\D", "", str(crudo))[:3]
    return digitos


def es_bien_por_naics(opp: dict) -> bool | None:
    """True=bien, False=servicio, None=NAICS desconocido."""
    p = naics_de(opp)
    if len(p) < 3:
        return None
    if p in config.NAICS_SERVICIOS:
        return False
    if p in config.NAICS_BIENES:
        return True
    return None


def _pais_extranjero(opp: dict) -> tuple[str, bool]:
    """
    Devuelve (pais, es_confiable) si el lugar de entrega NO es Estados Unidos.

    es_confiable=True cuando el countryCode de la API lo dice. False cuando
    solo aparece el nombre en el texto, que puede ser incidental ("fabricado
    en Alemania" en un aviso que se entrega en Ohio).
    """
    pop = opp.get("placeOfPerformance") or {}
    pais = pop.get("country")
    codigo = ""
    if isinstance(pais, dict):
        codigo = str(pais.get("code") or pais.get("name") or "").upper()
    if codigo and codigo not in ("USA", "US", "UNITED STATES", "AMERICA"):
        return codigo, True

    crudo = " ".join(
        str(pop.get(clave, "")) for clave in ("city", "country", "state")
    ).lower()
    if not crudo.strip():
        ofi = opp.get("officeAddress") or {}
        crudo = f"{ofi.get('city','')} {ofi.get('countryCode','')}".lower()

    for nombre in config.PAISES_EXTRANJEROS:
        if nombre in crudo:
            return nombre, False
    return "", False


def _coincide(texto: str, diccionario: set) -> int:
    return sum(1 for palabra in diccionario if palabra in texto)


def puntuar(opp: dict, descripcion: str = "") -> tuple[int, list[str]]:
    """
    Devuelve (puntaje, motivos). Los motivos se muestran en /debug para que
    puedas ver por que Kyomoto si o no una oportunidad.
    """
    titulo = _texto(opp.get("title"))
    cuerpo = _texto(descripcion)
    combo = f"{titulo} {cuerpo}"
    motivos: list[str] = []
    puntos = 0

    # --- Veto por NAICS (gana sobre todo) ---
    veredicto = es_bien_por_naics(opp)
    if veredicto is False:
        return -100, [f"NAICS {naics_de(opp)} = sector servicios, veto automatico"]
    if veredicto is True:
        puntos += 4
        motivos.append(f"NAICS {naics_de(opp)} = manufactura/comercio de bienes")
    else:
        motivos.append(f"NAICS {naics_de(opp)} = sin clasificar")

    # --- PSC ---
    psc = str(opp.get("classificationCode") or "").strip()
    if psc in config.PSC_BIENES:
        puntos += 1
        motivos.append(f"PSC {psc} = tangible")

    # --- Palabras clave ---
    hits_prod = _coincide(titulo, config.PALABRAS_PRODUCTO)
    hits_serv = _coincide(combo, config.PALABRAS_SERVICIO)

    # El titulo pesa el doble que la descripcion.
    p_titulo = _coincide(titulo, config.PALABRAS_PRODUCTO)
    if p_titulo:
        puntos += min(p_titulo * 2, 6)
        motivos.append(f"{p_titulo} termino(s) de producto en el titulo")
    elif hits_prod:
        puntos += min(hits_prod, 3)
        motivos.append(f"{hits_prod} termino(s) de producto en la descripcion")

    if hits_serv:
        # 3+ servicios distintos en el titulo = casi seguro servicio puro.
        s_titulo = _coincide(titulo, config.PALABRAS_SERVICIO)
        if s_titulo >= 3:
            return -100, [f"{s_titulo} terminos de servicio en el titulo, veto automatico"]
        puntos -= min(hits_serv * 2, 8)
        motivos.append(f"-{min(hits_serv * 2, 8)} por {hits_serv} termino(s) de servicio")

    # --- Base de trabajo ---
    if "supplies" in titulo or "materials" in titulo or "equipment" in titulo:
        puntos += 1

    # --- Descalificadores que se repiten en cada analisis real -------------
    # Se detectan aqui para no gastar una de las 10 llamadas diarias en
    # descubrir lo que ya sabemos. Peso alto, no veto: si queda duda, que lo
    # decida Gemini con la descripcion completa.

    certs = [c for c in config.PALABRAS_CERTIFICACION if c in combo]
    if certs:
        # "or equal" y "authorized distributor" son condicionantes suaves;
        # las certificaciones de seguridad si son un veto practico.
        duros = [c for c in certs if c in config.PALABRAS_CERTIFICACION_DUROS]
        if duros:
            puntos -= 7
            motivos.append(f"certificacion inaccesible: {duros[0][:40]}")
        else:
            puntos -= 3
            motivos.append(f"condicionante: {certs[0][:40]}")

    servicios = [s for s in config.PALABRAS_SERVICIO_OCULTO if s in combo]
    if servicios:
        puntos -= 5
        motivos.append(f"es servicio, no venta: {servicios[0][:36]}")

    pais, fiable = _pais_extranjero(opp)
    if pais and fiable:
        # El countryCode de la API lo dice: veto practico. Kyomoto despacha
        # desde Chile, no puede servir una base en Dubai.
        puntos -= 9
        motivos.append(f"DESTINO EN EL EXTRANJERO ({pais}): no despachable")
    elif pais:
        # Solo aparece en el texto; puede ser incidental ("fabricado en
        # Alemania" de algo que se entrega en Ohio). Penaliza, no veta.
        puntos -= 5
        motivos.append(f"menciona destino extranjero ({pais}): revisar")

    set_aside = str(opp.get("typeOfSetAside") or "").upper()
    if set_aside in ("BPA", "SBA", "TOTAL", "EDWOSB", "WOSB", "HBC", "SDVOSBC", "VOSBC"):
        puntos += 1
        motivos.append(f"Set-aside {set_aside} (small business)")

    # --- Ventana para postular ---
    # 999 significa "la API no informo fecha limite" (muchos Pre-solicitation
    # y los IDV de DANFE no la traen). Eso NO es tiempo de sobra: es dato
    # ausente, asi que no se da puntos ni se penaliza.
    dias = sam_api.dias_restantes(opp)
    if dias < 0:
        return -100, ["Fecha limite ya vencida"]
    if dias >= 999:
        motivos.append("Sin fecha limite informada (verificar en SAM.gov)")
    elif dias < config.DIAS_MINIMO_PARA_POSTULAR:
        puntos -= 2
        motivos.append(f"Solo {dias} dia(s) para postular")
    elif dias > 30:
        puntos += 1
        motivos.append(f"{dias} dias para postular (holgado)")

    # --- Avisos sin descripcion: no sirve analizarlos ---
    if not descripcion.strip() and not titulo.strip():
        return -100, ["Aviso sin titulo ni descripcion"]

    return puntos, motivos


def es_viable(opp: dict, descripcion: str = "", minimo: int | None = None) -> bool:
    minimo = config.PUNTAJE_MINIMO if minimo is None else minimo
    puntos, _ = puntuar(opp, descripcion)
    return puntos >= minimo


# Contratos grandes citada explicitamente en la descripcion. Se usan para
# descartar el tope de USD antes de gastar una llamada de Gemini.
_MONEDA = re.compile(
    r"(?:NTE|not[\s\-]?to[\s\-]?exceed(?:ed)?|estimated(?:[\s\-]?value)?|"
    r"max(?:imum)?|up[\s\-]?to|ceiling|total|contract[\s\-]?value|budget|"
    r"award|estimated[\s\-]?at|value[\s\-]?of)"
    r"[^0-9$]{0,40}\$?\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]{4,}(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)
_MONEDA_SUELTA = re.compile(r"\$\s*([0-9][0-9,\.]{4,})")


# Palabras que anteceden a una CANTIDAD de piezas, no a un valor de contrato.
_CANTIDAD = re.compile(
    r"(quantity|qty|\bea\b|\beach\b|per\s|units?|items?|pieces?|lots?|"
    r"months?|years?|days?|weeks?|hours?|pages?|copies|boxes|pallets|"
    r"shipments?|making\s|total\s+(?:items|pieces|units|lots))",
    re.IGNORECASE,
)


def _a_numero(bruto: str) -> float | None:
    """
    Convierte el texto de un monto en float, sin inventarse un factor.

    SAM.gov escribe "85,000.00" (coma de miles, punto decimal) y casi siempre
    seguido de un punto que cierra la frase. Una regex demasiado codiciosa se
    traga ese punto final y termina intentando leer "85,000.00.": al partirlo
    por puntos salen tres trozos, no cae en la rama de los centavos y el
    numero se va a "8500000", cien veces el monto real.

    Reglas, en orden:
      - se ignoran todo menos digitos, comas y puntos
      - los separadores del final no son decimales: "85,000.00." es 85,000
      - un punto solo es decimal si lo siguen uno o dos digitos
      - las comas son siempre separadores de miles (SAM.gov es de EE.UU.)
    Devuelve None si no queda un numero usable.
    """
    s = re.sub(r"[^0-9.,]", "", bruto or "")
    # "85,000.00." -> "85,000.00"  (el punto final cierra la frase, no es decimal)
    s = s.rstrip(".,")
    if not s or not any(ch.isdigit() for ch in s):
        return None

    if "." in s:
        entero, _, decimal = s.rpartition(".")
        # Un punto con 3+ digitos detras es de miles ("1.000.000"), no decimal.
        if len(decimal) in (1, 2) and entero:
            # float() no acepta comas: hay que quitarlas SIEMPRE, tanto en la
            # parte entera como en la decimal. Sin esto "85,000.00" levanta
            # ValueError y el monto se pierde entero.
            s = f"{entero.replace(',', '')}.{decimal}"
        else:
            s = re.sub(r"[^0-9]", "", s)
    else:
        s = re.sub(r"[^0-9]", "", s)

    try:
        valor = float(s)
    except ValueError:
        return None
    return valor or None


def valor_declarado(descripcion: str) -> tuple[float | None, str]:
    """
    Extrae el valor del contrato del texto de la oportunidad.
    Devuelve (monto, fragmento). monto es None si no se encuentra nada.

    Sirve para aplicar el tope de TOPE_USD ANTES de llamar a Gemini: si el
    aviso dice "Not to Exceed USD 350,000", no tiene caso gastar una de las
    10 llamadas diarias en confirmarlo.
    """
    if not descripcion:
        return None, ""

    candidatos: list[tuple[float, str]] = []
    for patron in (_MONEDA, _MONEDA_SUELTA):
        for m in patron.finditer(descripcion):
            previo = descripcion[max(0, m.start() - 20):m.start()]
            valor = _a_numero(m.group(1))
            if valor is None:
                continue

            if valor < 1_000 or valor > 50_000_000:
                continue

            # "Estimated quantity 2,000 ; Not to Exceed 350,000.00": el 2,000 es
            # un conteo de piezas, no el contrato.
            #
            # La palabra de cantidad se busca en el texto ANTES y DENTRO del
            # match, no solo antes. Antes solo se miraba `previo`, o sea los 20
            # caracteres previos al INICIO del match, pero el match arranca en
            # la palabra clave ("Estimated quantity 2,000"), asi que "quantity"
            # quedaba dentro del match y no se veia. Resultado: "Estimated
            # quantity 2,000 units" se tomaba por un contrato de USD 2,000 y,
            # como queda bajo MIN_USD, la licitacion buena se descartaba sola.
            #
            # Solo descalifica cuando el numero es CHICO: si es grande, es
            # dinero, aunque la palabra "quantity" este cerca.
            contexto = previo + " " + m.group(0)
            if valor < 10_000 and _CANTIDAD.search(contexto):
                continue

            inicio = max(0, m.start() - 35)
            candidatos.append((valor, descripcion[inicio:m.end() + 10].strip()))

    if not candidatos:
        return None, ""
    # Con varios montos gana el mayor: "NTE 350,000" es el techo del contrato,
    # y es el numero que decide si excede tu tope.
    return max(candidatos, key=lambda x: x[0])


def clave_titulo(titulo: str) -> str:
    """
    SAM.gov duplica el mismo aviso bajo varios noticeId (los 'parts kit' de
    distintas agencias, los Pre-solicitation duplicados). Sin esta clave, los 15
    espacios de Gemini se llenan con 15 copias del mismo producto.
    """
    return _texto(titulo)[:70]


def lugar_de_entrega(opp: dict) -> str:
    pop = opp.get("placeOfPerformance") or {}
    partes = []
    ciudad = pop.get("city")
    if isinstance(ciudad, dict):
        ciudad = ciudad.get("name")
    if ciudad:
        partes.append(ciudad)
    estado = pop.get("state")
    if isinstance(estado, dict):
        estado = estado.get("name") or estado.get("code")
    if estado:
        partes.append(estado)
    pais = pop.get("country")
    if isinstance(pais, dict):
        pais = pais.get("code") or pais.get("name")
    if pais:
        partes.append(str(pais))
    if not partes:
        ofi = opp.get("officeAddress") or {}
        for clave in ("city", "state", "countryCode"):
            if ofi.get(clave):
                partes.append(str(ofi[clave]))
    return " / ".join(partes) if partes else "No especificado"
