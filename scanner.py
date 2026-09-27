"""
scanner.py - Orquesta el barrido: trae, filtra, puntua, analiza y notifica.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import config
import filters
import gemini_analyzer
import sam_api
import store
import telegram_notify

log = logging.getLogger("kyomoto.scanner")

# Errores que no son culpa de Gemini y hay que reportar aunque el escaneo continue.
FALLOS_IGNORADOS = ()


def _log(txt: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} {txt}", flush=True)


def _traer_candidatos(dias: int) -> tuple[list[dict], int, int]:
    """
    Fase A: trae de la API y descarta con NAICS + titulo solamente.
    No baja descripciones todavia: son la parte cara de la API.
    """
    vistos = store.ids_procesados()
    crudos: list[dict] = []
    total = 0
    stats: dict = {}
    for opp in sam_api.barrer(dias, stats=stats):
        total += 1
        nid = opp.get("noticeId")
        if not nid or nid in vistos:
            continue
        # El veto por NAICS es gratis y elimina la mayoria.
        if filters.es_bien_por_naics(opp) is False:
            continue
        if sam_api.dias_restantes(opp) < 0:
            continue
        if not str(opp.get("title") or "").strip():
            continue
        crudos.append(opp)

    # Si NINGUN bloque se pudo leer, esto no es "un dia tranquilo": es SAM.gov
    # caido o la clave rechazada. Hay que decirlo, no devolver cero y sonreir.
    if stats.get("bloques_ok", 0) == 0 and stats.get("bloques_error", 0) > 0:
        raise sam_api.SamError(
            f"SAM.gov no devolvio ni un bloque de {stats['bloques_error']} "
            f"intentos. Ultimo error: {stats['errores'][0] if stats['errores'] else 'desconocido'}"
        )

    return crudos, total, len(vistos)


def _bajar_descripciones(candidatos: list[dict]) -> dict[str, str]:
    """Fase B: baja el texto real en paralelo (endpoint /v1/noticedesc).

    Ojo: ~50% de los avisos devuelven 404 'Description Not Found' (medido:
    24 de 45). Pasa sobre todo con Award Notice y los Pre-solicitation. Se
    maneja como texto vacio: el titulo suele bastar para el pre-filtro.
    """
    descripciones: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=config.WORKERS) as pool:
        futuros = {
            pool.submit(sam_api.obtener_descripcion, o["noticeId"]): o["noticeId"]
            for o in candidatos
        }
        for fut in as_completed(futuros):
            nid = futuros[fut]
            try:
                descripciones[nid] = fut.result() or ""
            except Exception as e:
                log.warning("descripcion de %s fallo: %s", nid, e)
                descripciones[nid] = ""
    return descripciones


def escanear(chat_id: str, dias: int | None = None, progreso=None) -> dict:
    """
    Corrige un barrido completo. Never lanza: devuelve un resumen con
    'errores' para que main.py pueda avisarle al usuario.
    """
    dias = dias or config.DIAS_DE_VENTANA
    resumen = {
        "traidas": 0,
        "candidatas": 0,
        "puntuales": 0,
        "analizadas": 0,
        "viables": 0,
        "notificadas": 0,
        "errores": [],
        "detalle_puntajes": [],
        "analisis": [],
    }

    def avisar(texto: str) -> None:
        if progreso:
            try:
                progreso(texto)
            except Exception:
                pass

    # ---- Fase A ----
    avisar("🔎 Consultando SAM.gov...")
    try:
        candidatas, total, ya_vistos = _traer_candidatos(dias)
    except sam_api.SamError as e:
        resumen["errores"].append(f"SAM.gov fallo: {e}")
        _log(f"SAM.gov fallo: {e}")
        return resumen

    resumen["traidas"] = total
    resumen["candidatas"] = len(candidatas)
    _log(f"SAM.gov: {total} avisos en {dias}d, {ya_vistos} ya vistos, {len(candidatas)} potencialmente bienes")

    if not candidatas:
        _log("Sin candidatos nuevos. Se extends la ventana en /escaneo 10")
        return resumen

    # ---- Pre-puntaje SOLO con titulo (gratis) para no gastar descripciones ----
    # Bajar la descripcion cuesta una llamada HTTP por aviso. Ordenar primero
    # por titulo garantiza que el presupuesto de MAX_DESCRIPCIONES se gaste en los
    # candidatos que de verdad parecen producto.
    provisionales = []
    for opp in candidatas:
        p, _ = filters.puntuar(opp, "")
        if p >= config.PUNTAJE_MINIMO:
            provisionales.append((p, opp))
    provisionales.sort(key=lambda x: x[0], reverse=True)
    objetivo = [o for _, o in provisionales[: config.MAX_DESCRIPCIONES]]
    _log(
        f"Pre-puntaje por titulo: {len(provisionales)} sobre el piso, "
        f"descargo {len(objetivo)}"
    )

    # ---- Fase B ----
    avisar(f"📄 Bajando descripciones de {len(objetivo)} avisos...")
    descripciones = _bajar_descripciones(objetivo)
    con_texto = sum(1 for v in descripciones.values() if v.strip())
    _log(f"Descripciones con texto real: {con_texto}/{len(descripciones)}")

    # ---- Puntuar y quedarse con las mejores ----
    puntuadas = []
    for opp in candidatas:
        desc = descripciones.get(opp["noticeId"], "")
        puntos, motivos = filters.puntuar(opp, desc)
        if puntos >= config.PUNTAJE_MINIMO:
            puntuadas.append((puntos, opp, desc, motivos))

    puntuadas.sort(key=lambda x: x[0], reverse=True)

    # Deduplicar por titulo: SAM.gov publica el mismo producto bajo varios
    # noticeId y sin esto los cupos de Gemini se llenan con copias.
    vistos_titulo: set[str] = set()
    sin_repetir = []
    repetidas = 0
    for item in puntuadas:
        clave = filters.clave_titulo(item[1].get("title", ""))
        if clave and clave in vistos_titulo:
            repetidas += 1
            continue
        vistos_titulo.add(clave)
        sin_repetir.append(item)
    if repetidas:
        _log(f"Se descartaron {repetidas} duplicados por titulo")
    puntuadas = sin_repetir

    resumen["puntuales"] = len(puntuadas)
    resumen["detalle_puntajes"] = [
        {"puntaje": p, "titulo": o.get("title", "")[:70], "motivos": m[:4]}
        for p, o, _, m in puntuadas[:12]
    ]
    _log(f"Pasan el filtro: {len(puntuadas)} (de {len(candidatas)})")
    for p, o, _, _ in puntuadas[:8]:
        _log(f"   [{p:>3}] {o.get('title', '')[:78]}")

    if not puntuadas:
        return resumen

    a_analizar = puntuadas[: config.MAX_A_GEMINI]
    avisar(f"🧠 Analizando {len(a_analizar)} con Gemini...")
    resumen["analizadas"] = len(a_analizar)

    # ---- Fase C: Gemini ----
    # Pocas llamadas simultaneas y con pausa entre ellas. El plan gratis de
    # Gemini da 429 RESOURCE_EXHAUSTED si le pides 25 analyses en paralelo.
    fallos_gemini: list[str] = []
    viables: list[tuple[int, dict]] = []
    throttle = threading.Semaphore(max(1, config.GEMINI_WORKERS))

    def _analizar_serializado(opp: dict, desc: str, lugar: str) -> dict:
        with throttle:
            resultado = gemini_analyzer.analizar(opp, desc, lugar)
            time.sleep(config.GEMINI_PAUSA_SEG)
            return resultado

    with ThreadPoolExecutor(max_workers=max(1, config.GEMINI_WORKERS)) as pool:
        futuros = {
            pool.submit(
                _analizar_serializado,
                o,
                d,
                filters.lugar_de_entrega(o),
            ): (p, o)
            for p, o, d, _ in a_analizar
        }
        for fut in as_completed(futuros):
            puntos, opp = futuros[fut]
            nid = opp["noticeId"]
            try:
                a = fut.result()
            except gemini_analyzer.GeminiError as e:
                fallos_gemini.append(str(e))
                _log(f"   Gemini fallo en {nid[:8]}: {str(e).splitlines()[0][:90]}")
                # NO se marca como vista: si fallo por cuota o saturacion, este
                # aviso debe reintentarse en el proximo barrido. Si lo marcaras,
                # la oportunidad se perderia para siempre.
                continue
            except Exception as e:
                fallos_gemini.append(f"{type(e).__name__}: {e}")
                _log(f"   Error inesperado en {nid[:8]}: {e}")
                continue

            if not a["viable"]:
                store.marcar(nid, opp.get("title", ""), viable=False, puntaje=puntos)
                _log(f"   [x] descartada: {a['motivo_descarte'][:70]}")
                continue

            store.marcar(nid, opp.get("title", ""), viable=True, puntaje=puntos)
            viables.append((puntos, a))
            resumen["viables"] += 1
            _log(f"   [OK] VIABLE [{puntos}]: {opp.get('title', '')[:62]}")

    viables.sort(key=lambda x: x[0], reverse=True)
    resumen["analisis"] = [a for _, a in viables]
    top = viables[: config.MAX_NOTIFICACIONES]
    if len(viables) > config.MAX_NOTIFICACIONES:
        _log(
            f"Tope: {len(viables)} viables -> notificando las "
            f"{config.MAX_NOTIFICACIONES} con mejor puntaje"
        )

    for puntos, a in top:
        _log(f"   -> notificando [{puntos}]: {a['title'][:62]}")
        if config.DRY_RUN:
            _log("      (DRY_RUN: no se envia)")
            continue
        ok = telegram_notify.enviar(
            chat_id, telegram_notify.formatear_analisis(a), html_mode=True
        )
        if ok:
            resumen["notificadas"] += 1
        else:
            resumen["errores"].append(
                f"No se pudo notificar {str(a.get('notice_id'))[:8]}"
            )

    if fallos_gemini:
        # Esto es lo que el codigo original se tragaba en silencio.
        unicos = sorted(set(fallos_gemini))
        resumen["errores"].append(
            f"Gemini fallo {len(fallos_gemini)}/{len(a_analizar)} veces: "
            + " | ".join(unicos[:3])
        )
        _log(f"⚠️ Gemini fallo {len(fallos_gemini)} veces: {unicos[0][:200]}")

    return resumen


def redactar_fallo(chat_id: str, resumen: dict) -> None:
    """Si algo sale mal de verdad, avisar. Nunca fallar en silencio."""
    if not resumen["errores"] or config.DRY_RUN:
        return
    cuerpo = "🚨 <b>El barrido termino con problemas</b>\n\n"
    for e in resumen["errores"][:5]:
        cuerpo += f"• <code>{telegram_notify._esc(e[:400])}</code>\n"
    if config.GEMINI_API_KEY and any("GEMINI" in e.upper() for e in resumen["errores"]):
        cuerpo += (
            "\n<b>Lo mas probable:</b> la clave de Gemini no es valida. "
            "Las claves de AI Studio empiezan con <code>AIza</code>. "
            "Genera una en https://aistudio.google.com/apikey"
        )
    telegram_notify.enviar(chat_id, cuerpo, html_mode=True)
