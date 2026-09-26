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

    # --- Set aside para small business (te abre puertas) ---
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
