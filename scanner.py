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
import quota
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


def _ya_sin_texto(opp: dict) -> bool:
    """Ya se consulto a SAM.gov por este aviso y respondio que no hay texto."""
    import store

    return store.descripcion_consultada(opp["noticeId"]) and not store.leer_descripcion(
        opp["noticeId"]
    )


def _sin_texto_en_ventana(candidatos: list) -> int:
    """
    Cuantos de estos candidatos ya se saben sin descripcion.

    Solo mira la ventana que se va a usar: recorrer los 600 candidatos de cada
    barrido para consultar la cache uno por uno no compensa, y aqui solo hace
    falta una estimacion para abrir la ventana un poco mas.
    """
    import store

    n = 0
    for _, o in candidatos[:60]:
        nid = o["noticeId"]
        if store.descripcion_consultada(nid) and not store.leer_descripcion(nid):
            n += 1
    return n


def _bajar_descripciones(candidatos: list[dict]) -> dict[str, str]:
    """
    Baja el texto real de los candidatos que quedan, con cache.

    Importante por cuota: la API de SAM.gov tiene tope de peticiones por dia.
    Antes Kyomoto bajaba MAX_DESCRIPCIONES (40) descripciones en cada barrido,
    y con barridos cada 2h eso son ~480 peticiones diarias solo en descripciones.
    Ahora:
      - se consulta la cache primero (cuesta 0 peticiones)
      - solo se baja lo que falta
      - lo que se baja se guarda para el siguiente barrido

    Ojo: ~50% de los avisos devuelven 404 'Description Not Found' (medido:
    24 de 45). Pasa sobre todo con Award Notice y los Pre-solicitation. Se
    maneja como texto vacio: el titulo suele bastar para el pre-filtro.
    """
    import store

    pendientes: list[dict] = []
    descripciones: dict[str, str] = {}
    sin_texto = 0
    for o in candidatos:
        nid = o["noticeId"]
        # Primero se pregunta si ya se consulto. Si ya se hizo y salio vacia
        # (SAM.gov devuelve 404 para mas de la mitad de los avisos), no se
        # vuelve a pagar la peticion: se cuenta aparte y se sigue. Antes se
        # repreguntaban en cada barrido, para siempre.
        if store.descripcion_consultada(nid):
            descripciones[nid] = store.leer_descripcion(nid)
            if not descripciones[nid]:
                sin_texto += 1
        else:
            pendientes.append(o)

    if sin_texto:
        _log(
            f"{sin_texto} de {len(candidatos)} sin descripcion en SAM.gov "
            f"(404 de la propia API; se saltan sin gastar peticiones)"
        )

    if not pendientes:
        _log(f"Las {len(descripciones)} descriciones salieron de la cache (0 peticiones)")
        return descripciones

    _log(f"Bajando {len(pendientes)} descripciones nuevas (SAM.gov tiene tope diario)")

    def bajar(o):
        return o["noticeId"], (sam_api.obtener_descripcion(o["noticeId"]) or "")

    with ThreadPoolExecutor(max_workers=config.WORKERS) as pool:
        for fut in as_completed({pool.submit(bajar, o): o for o in pendientes}):
            try:
                nid, texto = fut.result()
            except Exception as e:
                log.warning("descripcion fallo: %s", e)
                continue
            descripciones[nid] = texto
            store.cache_descripcion(nid, texto)

    return descripciones


def escanear(chat_id: str, dias: int | None = None, progreso=None) -> dict:
    """
    Corrige un barrido completo. Never lanza: devuelve un resumen con
    'errores' para que main.py pueda avisarle al usuario.
    """
    dias = dias or config.DIAS_DE_VENTANA
    # Si alguien usa el scanner sin pasar por main(), las tablas pueden no
    # existir. Es idempotente y barato, asi que se asegura aqui.
    quota.init()
    store.init_db()

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
        "cuota": {},
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

    # Cuantas descripciones bajar. Antes era MAX_DESCRIPCIONES (40) en cada
    # barrido, y con barridos cada 2h eso reventaba el tope diario de SAM.gov.
    # Ahora se bajan solo las que pueden entrar al presupuesto de Gemini, con
    # margen: el resto espera su turno en el siguiente barrido.
    presupuesto = quota.estado()
    # Cuantos analisis van a ocurrir de verdad. Antes se calculaba con
    # MAX_A_GEMINI (4) cuando el escaneo real acababa haciendo 1 solo: se
    # pedian 8 descripciones y se usaba 1. Seis por barrido, doce barridos,
    # setenta y dos descripciones al dia para nada.
    previstos = max(1, min(
        quota.por_barrido(), config.MAX_A_GEMINI, presupuesto["restantes"]
    ))
    # Margen sobre lo previsto: parte se cae por el rango de USD y parte por
    # el filtro final, y no se avisara a Gemini de esas.
    extra = max(2, previstos // 2)

    # Se piden algunos mas de los previstos porque parte se cae por el rango
    # de USD. Y se salta a los que ya se sabe que no tienen descripcion: si no,
    # un punte alto de un aviso con 404 se lleva el cupo de uno que si tiene
    # texto, y el embudo se estanca. Medido el 29-sep: cinco barridos seguidos,
    # cuatro descripciones por ronda, una sola con texto, y siempre las
    # mismas. Cero viables por esto solo.
    holgura = _sin_texto_en_ventana(provisionales)
    ventana = provisionales[: previstos + extra + holgura]
    objetivo = [o for _, o in ventana if not _ya_sin_texto(o)][: previstos + extra]
    if holgura:
        _log(
            f"{holgura} de los primeros no tienen descripcion en SAM.gov; "
            f"se amplia la ventana para compensar"
        )
    _log(
        f"Pre-puntaje por titulo: {len(provisionales)} sobre el piso; "
        f"van a ser {previstos} analisis asi que bajo {len(objetivo)} "
        f"descripciones"
    )

    # ---- Fase B ----
    avisar(f"📄 Buscando descripciones de {len(objetivo)} avisos...")
    descripciones = _bajar_descripciones(objetivo)
    con_texto = sum(1 for v in descripciones.values() if v.strip())
    _log(f"Descripciones con texto real: {con_texto}/{len(descripciones)}")

    # Puntuar y quedarse con las mejores
    puntuadas = []
    descartados_por_valor = 0
    for opp in candidatas:
        desc = descripciones.get(opp["noticeId"], "")
        puntos, motivos = filters.puntuar(opp, desc)
        if puntos < config.PUNTAJE_MINIMO:
            continue

        # El tope de USD se aplica AQUI, con los datos que ya tenemos, para no
        # gastar una de las 10 llamadas diarias en un contrato que dice
        # "Not to Exceed USD 350,000".
        valor, fragmento = filters.valor_declarado(desc)
        if valor is not None:
            if valor > config.TOPE_USD:
                descartados_por_valor += 1
                store.marcar(opp["noticeId"], opp.get("title", ""), viable=False, puntaje=puntos)
                continue
            if valor < config.MIN_USD:
                descartados_por_valor += 1
                store.marcar(opp["noticeId"], opp.get("title", ""), viable=False, puntaje=puntos)
                continue
            puntos += 2
            motivos.append(f"Valor declarado {valor:,.0f} USD dentro del rango")

        puntuadas.append((puntos, opp, desc, motivos))

    if descartados_por_valor:
        _log(
            f"{descartados_por_valor} descartados por el rango de USD "
            f"({config.MIN_USD:,.0f}-{config.TOPE_USD:,.0f}) sin gastar Gemini"
        )

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
    # El presupuesto diario manda. Si ya se gastaron las 10 llamadas, no se
    # intenta ni una mas: lo que queda espera a manana sin marcarse como visto.
    presupuesto = quota.estado()
    resumen["cuota"] = presupuesto
    resumen["sam_tope"] = sam_api.proximo_acceso()
    if sam_api.throttled():
        _log(f"SAM.gov tiene el tope diario alcanzado. Vuelve {sam_api.proximo_acceso()}")
    if presupuesto["restantes"] <= 0:
        _log(
            f"Presupuesto diario agotado ({presupuesto['usadas']}/"
            f"{presupuesto['presupuesto']}). Se reintenta tras el reinicio de Google."
        )
        resumen["errores"].append(
            f"Presupuesto de Gemini agotado hoy ({presupuesto['usadas']} llamadas). "
            f"Las {len(puntuadas)} oportunidades siguen pendientes para el "
            f"proximo barrido."
        )
        return resumen

    top = min(len(puntuadas), config.MAX_A_GEMINI,
              quota.por_barrido(), presupuesto["restantes"])

    # A Gemini no se le manda un aviso sin descripcion. Un titulo suelto no
    # alcanza para calcular costo por unidad ni margen: el modelo responderia
    # inventando numeros, que es peor que no responder. Se separan y se avisa
    # si con eso no alcanza para llenar los cupos.
    con_texto = [it for it in puntuadas if it[2].strip()]
    sin_texto = len(puntuadas) - len(con_texto)
    top = min(top, len(con_texto))
    a_analizar = con_texto[:top]
    if sin_texto:
        _log(
            f"{sin_texto} sobre el piso sin descripcion todavia (no se avisa a "
            f"Gemini: solo tienen titulo)"
        )
    if not a_analizar:
        _log("Ningun aviso con descripcion completa todavia. Se reintentara.")
        resumen["errores"].append(
            "Los avisos que pasan el filtro todavia no tienen descripcion "
            "descargada. Se reintentan en el proximo barrido."
        )
        return resumen
    resumen["analizadas"] = len(a_analizar)
    avisar(
        f"🧠 Analizando {top} con Gemini "
        f"({presupuesto['restantes']} de presupuesto libre hoy)..."
    )

    fallos_gemini: list[str] = []
    viables: list[tuple[int, dict]] = []
    throttle = threading.Semaphore(max(1, config.GEMINI_WORKERS))
    corto_por_cuota = False
    # El cortacircuitos. Lo enciende el primer 429 y el resto del lote se
    # detiene en el siguiente punto de control, en vez de que cada rama
    # descubra por su cuenta lo mismo y duerma lo suyo.
    sin_cuota = threading.Event()

    def _continuar() -> bool:
        return not sin_cuota.is_set()

    def _analizar(opp: dict, desc: str, lugar: str) -> dict:
        with throttle:
            r = gemini_analyzer.analizar(opp, desc, lugar, continuar=_continuar)
            time.sleep(config.GEMINI_PAUSA_SEG)
            return r

    with ThreadPoolExecutor(max_workers=max(1, config.GEMINI_WORKERS)) as pool:
        futuros = {}
        for p, o, d, _ in a_analizar:
            if not quota.reservar(o["noticeId"]):
                continue  # ya conto hoy
            futuros[pool.submit(_analizar, o, d, filters.lugar_de_entrega(o))] = (p, o)
        resumen["analizadas"] = len(futuros)

        for fut in as_completed(futuros):
            puntos, opp = futuros[fut]
            nid = opp["noticeId"]
            try:
                a = fut.result()
            except gemini_analyzer.GeminiError as e:
                texto = str(e)
                fallos_gemini.append(texto)
                _log(f"   Gemini fallo en {nid[:8]}: {texto.splitlines()[0][:90]}")
                if "429" in texto or "RESOURCE_EXHAUSTED" in texto:
                    corto_por_cuota = True
                    # Enciende el cortacircuitos: los hilos que aun quedan se
                    # paran en su siguiente punto de control, sin gastar cuota
                    # ni dormir por un 429 que ya se sabe.
                    sin_cuota.set()
                    quota.gastar_fallo()
                # NO se marca como vista: si fallo por cuota o saturacion, este
                # aviso debe reintentarse en el proximo barrido.
                continue
            except Exception as e:
                fallos_gemini.append(f"{type(e).__name__}: {e}")
                _log(f"   Error inesperado en {nid[:8]}: {e}")
                continue

            quota.gastar()
            if not a["viable"]:
                store.marcar(nid, opp.get("title", ""), viable=False, puntaje=puntos)
                _log(f"   [x] descartada: {a['motivo_descarte'][:70]}")
                continue

            store.marcar(nid, opp.get("title", ""), viable=True, puntaje=puntos)
            viables.append((puntos, a))
            resumen["viables"] += 1

    # Si la cuota de Google se agoto a mitad del barrido, se abandona el resto:
    # cada reintento solo gasta mas cuota y retrasa el dia siguiente.
    if corto_por_cuota:
        pendientes = len(futuros) - len(viables) - len(fallos_gemini)
        _log(f"Cuota de Google agotada. Se abandona el resto del barrido.")
        resumen["errores"].append(
            f"Google corto la cuota a mitad del barrido; Kyomoto paro las "
            f"analisis que faltaban para no gastar mas. Se guardaron "
            f"{len(viables)} viables; el resto queda pendiente para manana."
        )

    # "Las mejores" se ordenan por margen NETO, que es la metrica de decision
    # de verdad, y no solo por el puntaje previo del filtro: un aviso con
    # puntaje 9 y margen neto 13% es peor negocio que uno de 8 con 28%.
    def _clave(item):
        puntos, a = item
        neto = a.get("margen_neto_porcentaje")
        if not isinstance(neto, (int, float)):
            neto = -1.0
        return (neto, puntos)

    viables.sort(key=_clave, reverse=True)
    for puntos, a in viables:
        neto = a.get("margen_neto_porcentaje")
        _log(
            f"   [OK] VIABLE [{puntos}] margen neto "
            f"{neto if isinstance(neto, (int, float)) else '?'}%: "
            f"{a['title'][:52]}"
        )
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
    if config.DRY_RUN:
        return
    if resumen.get("sam_tope"):
        telegram_notify.enviar(
            chat_id,
            "🔋 <b>SAM.gov me dijo que me calle</b> (ᐢ..ᐢ)\n\n"
            "Llegue al tope de peticiones diarias de la API publica. Vuelve a "
            f"funcionar <b>{telegram_notify._esc(resumen['sam_tope'])}</b>.\n\n"
            "No es un error tuyo ni mio: es el limite del plan gratuito de "
            "SAM.gov. Kyomoto no perdio nada; lo que no vio queda pendiente "
            "para cuando vuelva.",
            html_mode=True,
        )
    if not resumen["errores"]:
        return
    cuerpo = "🚨 <b>El barrido termino con problemas</b>\n\n"
    for e in resumen["errores"][:5]:
        cuerpo += f"• <code>{telegram_notify._esc(e[:400])}</code>\n"
    if config.GEMINI_API_KEY and any("GEMINI" in e.upper() for e in resumen["errores"]):
        cuerpo += (
            "\n<b>Lo mas probable:</b> la cuota de Google se agoto. Manda "
            "<code>/cuota</code> para ver el detalle."
        )
    telegram_notify.enviar(chat_id, cuerpo, html_mode=True)
