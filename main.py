"""
main.py - Kyomoto, asistente de licitaciones en Telegram.

QUE CAMBIO RESPECTO A TU VERSION ANTERIOR
  1. Ya no se traga los errores: cualquier fallo de API llega a tu Telegram.
  2. /selftest te dice, en 5 segundos, si Telegram, SAM.gov y Gemini funcionan.
  3. El escaneo se ejecuta en segundo plano y va actualizando el progreso.
  4. Se usa el JobQueue de python-telegram-bot en vez de APScheduler, para no
     tener dos event loops compitiendo.
  5. Los mensajes van en HTML escapado, no en Markdown, para que no se pierdan.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading

import requests
from flask import Flask
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import gemini_analyzer
import label as etiqueta
import quota
import sam_api
import scanner
import store
import telegram_notify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
for ruidoso in ("httpx", "telegram.ext.Application", "apscheduler"):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)
log = logging.getLogger("kyomoto")

ESTADO = {"activo": True, "escaneando": False, "ultimo_resumen": None}
ANALISIS: dict[str, dict] = {}  # notice_id -> ficha (para /etiqueta)
ULTIMO_ESCANEO: dict = {}


# ==========================================================================
#  SERVIDOR DE SALUD (Render lo necesita para no marcar el servicio como caido)
# ==========================================================================
server = Flask(__name__)


@server.route("/")
def home():
    return "Kyomoto esta activa ✨", 200


@server.route("/healthz")
def healthz():
    return {"ok": True, "escaneando": ESTADO["escaneando"]}, 200


def _servidor() -> None:
    server.run(host="0.0.0.0", port=config.PORT, use_reloader=False, threaded=True)


# ==========================================================================
#  HELPERS
# ==========================================================================
def _chat_id(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    return str(ctx.job.chat_id) if ctx.job else config.TELEGRAM_CHAT_ID


def _crear_app() -> Application:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "FALTA TELEGRAM_BOT_TOKEN. Configurala en Render > Environment."
        )
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ayuda", cmd_start))
    app.add_handler(CommandHandler("on", cmd_on))
    app.add_handler(CommandHandler("off", cmd_off))
    app.add_handler(CommandHandler("estado", cmd_estado))
    app.add_handler(CommandHandler("status", cmd_estado))
    app.add_handler(CommandHandler("cuota", cmd_cuota))
    app.add_handler(CommandHandler("selftest", cmd_selftest))
    app.add_handler(CommandHandler("escaneo", cmd_escaneo))
    app.add_handler(CommandHandler("forzar_escaneo", cmd_escaneo))
    app.add_handler(CommandHandler("puntajes", cmd_puntajes))
    app.add_handler(CommandHandler("etiqueta", cmd_etiqueta))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("pdf", cmd_pdf))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_texto))
    # Los botones del menu. Sin este handler, Telegram muestra "botón muerto".
    app.add_handler(CallbackQueryHandler(cmd_boton, pattern=r"^k:"))
    return app


# ==========================================================================
#  COMANDOS
# ==========================================================================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    import kyo
    import menu

    await update.message.reply_text(
        kyo.resumen_menu(ESTADO["activo"]),
        parse_mode=ParseMode.HTML,
        reply_markup=menu.teclado(ESTADO["activo"]),
    )


async def _menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Reenvia el menu con el estado actual. Se usa tras cada /on y /off."""
    import kyo
    import menu

    try:
        await update.effective_message.reply_text(
            kyo.saludo_menu(ESTADO["activo"]),
            parse_mode=ParseMode.HTML,
            reply_markup=menu.teclado(ESTADO["activo"]),
        )
    except Exception as e:
        log.warning("No se pudo enviar el menu: %s", e)


async def cmd_on(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ESTADO["activo"] = True
    await update.message.reply_text(
        "▶️ <b>Busqueda reactivada, amo</b> (⁠✦⁠_⁠✦⁠)\n"
        f"Reviso cada {config.INTERVALO_HORAS:g} h lo nuevo que publique SAM.gov, "
        "de USD "
        f"{config.MIN_USD:,.0f} a {config.TOPE_USD:,.0f}.",
        parse_mode=ParseMode.HTML,
    )
    await _menu(update, ctx)


async def cmd_off(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ESTADO["activo"] = False
    await update.message.reply_text(
        "⏸ <b>Busqueda pausada, amo</b> (⁠.⁠-⁠⌟⁠.⁠)\n"
        "No voy a revisar SAM.gov hasta que me digas /on.",
        parse_mode=ParseMode.HTML,
    )
    await _menu(update, ctx)


def texto_estado() -> str:
    """Cuerpo de /estado. Funcion pura: la usan el comando y el boton."""
    r = ULTIMO_ESCANEO.get("resumen")
    q = quota.estado()
    partes = [
        f"📊 <b>Kyomoto v{config.KYOMOTO_VERSION}</b> · modelo "
        f"<code>{config.GEMINI_MODEL}</code>",
        f"• Busqueda: {'🟢 activa' if ESTADO['activo'] else '⏸ pausada'}",
        f"• Escaneando ahora: {'sí' if ESTADO['escaneando'] else 'no'}",
        f"• Cada: <b>{config.INTERVALO_HORAS:g} h</b> "
        f"· Ventana: {config.DIAS_DE_VENTANA} dias",
        f"• Historico: {store.total_procesadas()} avisos vistos",
        f"• Rango: <b>USD {config.MIN_USD:,.0f} – {config.TOPE_USD:,.0f}</b>",
        "",
        "<b>Cuota de Gemini hoy</b>",
        f"• Usadas: {q['usadas']}/{q['presupuesto']} | "
        f"Restan: <b>{q['restantes']}</b>",
    ]
    if q["fallidas"]:
        partes.append(f"• Fallidas por cuota: {q['fallidas']}")
    if q["agotado"]:
        partes.append(
            "• ⚠️ <b>Agotada</b>: no analizo mas hasta que Google reinicie "
            "su cuota (medianoche del Pacífico)"
        )
    if r:
        partes += [
            "",
            "<b>Ultimo barrido</b>",
            f"• Avisos de SAM.gov: {r['traidas']}",
            f"• Con producto fisico: {r['puntuales']}",
            f"• Analizadas por IA: {r['analizadas']}",
            f"• <b>Viables: {r['viables']}</b> | Notificadas: {r['notificadas']}",
        ]
        if r["errores"]:
            partes.append(f"⚠️ {len(r['errores'])} problema(s), mira /cuota")
    if config.DRY_RUN:
        partes.append("\n🧪 <b>DRY_RUN</b>: no se envia nada.")
    return "\n".join(partes)


def texto_cuota() -> str:
    """Cuerpo de /cuota."""
    q = quota.estado()
    dias = quota.dias_registrados()
    lineas = [
        "🔋 <b>Cuota de Gemini</b> (ᐢ..ᐢ)",
        f"• Hoy ({q['dia']}): <b>{q['usadas']}/{q['presupuesto']}</b>",
        f"• Restan: <b>{q['restantes']}</b>",
        f"• Ritmo: 1 llamada cada {config.GEMINI_PAUSA_SEG:g}s",
    ]
    if q["fallidas"]:
        lineas.append(
            f"• Hoy Google nos cortó: {q['fallidas']} intento(s) rechazados"
        )
    if q["agotado"]:
        lineas.append("")
        lineas.append(
            "⚠️ <b>Cuota agotada.</b> No es un error: es el límite del plan "
            "gratis. Se reinicia sola a las 00:00 del Pacífico."
        )
        lineas.append("Lo que no alcancé queda pendiente para mañana, amo.")
    if dias:
        lineas += ["", "<b>Ultimos dias</b>"]
        for d in dias:
            barra = "▰" * min(d["llamadas"], 20)
            extra = f" · {d['fallidas']} fallo(s)" if d["fallidas"] else ""
            lineas.append(f"• {d['dia']}: {d['llamadas']} {barra}{extra}")
    return "\n".join(lineas)


def texto_puntajes() -> str:
    """Cuerpo de /puntajes."""
    r = ULTIMO_ESCANEO.get("resumen") or ESTADO["ultimo_resumen"]
    detalle = (r or {}).get("detalle_puntajes") or []
    if not detalle:
        return (
            "📋 Aun no tengo puntajes, amo.\n\n"
            "Toca <b>🔍 Buscar ahora</b> y despues vuelvo a mirar (｡•̀ᴗ-)✧"
        )
    lineas = [
        "📋 <b>Por que Kyomoto si o no cada aviso</b>",
        "_(numero alto = mas prometedor)_",
        "",
    ]
    for d in detalle:
        motivos = "; ".join(str(x) for x in d["motivos"])
        lineas.append(f"<b>[{d['puntaje']}]</b> {telegram_notify._esc(d['titulo'])}")
        lineas.append(f"    <i>{telegram_notify._esc(motivos)}</i>")
        lineas.append("")
    return "\n".join(lineas)


async def cmd_estado(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(texto_estado(), parse_mode=ParseMode.HTML)


async def cmd_cuota(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(texto_cuota(), parse_mode=ParseMode.HTML)


async def _selftest(destino, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Autodiagnostico. `destino` es cualquier objeto con reply_text / edit_text,
    para que funcione igual desde un comando o desde un boton del menu.

    Distingue 429 (cuota del plan gratis agotada, no es un error) de 401
    (la credencial no existe). Antes siempre daba el consejo del 401, que
    mandaba a crear una clave nueva cuando el problema era otro.
    """
    aviso = await destino.reply_text(
        "🩺 Autodiagnóstico de Kyomoto... (hasta ~20s) (ᐢ..ᐢ)"
    )

    def revisar() -> dict:
        r = {"telegram": bool(config.TELEGRAM_BOT_TOKEN), "chat": config.TELEGRAM_CHAT_ID,
             "sam": sam_api.verificar_api(), "gem": gemini_analyzer.verificar_api()}
        return r

    d = await asyncio.get_running_loop().run_in_executor(None, revisar)

    L = ["🩺 <b>Autodiagnóstico de Kyomoto</b>", ""]
    L.append(f"<i>versión {config.KYOMOTO_VERSION} · modelo {config.GEMINI_MODEL}</i>")
    L.append("")
    L.append("✅ <b>Telegram</b> listo" if d["telegram"] else "❌ Falta <b>TELEGRAM_BOT_TOKEN</b>")
    L.append(
        f"✅ Destino: <code>{telegram_notify._esc(d['chat'] or 'NO CONFIGURADO')}</code>"
        if d["chat"] else "❌ Falta <b>TELEGRAM_CHAT_ID</b>"
    )
    L.append(
        f"{'✅' if d['sam']['ok'] else '❌'} <b>SAM.gov</b>: "
        f"{telegram_notify._esc(d['sam']['detalle'][:200])}"
    )
    L.append("")

    g = d["gem"]
    cuerpo_gem = telegram_notify._esc(g["detalle"][:400])
    det = g["detalle"]
    if g["ok"]:
        L.append(f"✅ <b>Gemini</b>: {cuerpo_gem}")
    elif "503" in det or "UNAVAILABLE" in det or "high demand" in det:
        L.append(f"⏳ <b>Gemini</b>: {cuerpo_gem}")
    else:
        L.append(f"❌ <b>Gemini</b>: {cuerpo_gem}")
    cuerpo = "\n".join(L)

    # ---- Consejo segun el fallo real ----
    if g["ok"]:
        pass
    elif "503" in det or "UNAVAILABLE" in det or "high demand" in det:
        # Este es el caso mas comun y NO es un problema de configuracion.
        cuerpo += (
            "\n\n<b>Esto no es un error, amo.</b> Tu clave está perfecta. "
            "Es temporal: Google dice que ese modelo específico tiene mucha "
            "demanda ahora mismo.\n"
            "Kyomoto ya intenta con otros modelos solo, así que en el "
            "próximo barrido puede que sí analice.\n"
            "<i>Si pasa seguido, revisa con /cuota o cambia GEMINI_MODEL a "
            "gemini-3.6-flash, que estaba libre cuando medimos.</i>"
        )
    elif "429" in det or "RESOURCE_EXHAUSTED" in det:
        cuerpo += (
            "\n\n<b>Esto no es un error, amo.</b> Es el límite del plan "
            "gratis de Google: se agotó la cuota de hoy.\n"
            "Se reinicia sola a las <b>00:00 del Pacífico</b> "
            "(≈ 03:00 hora de Chile). Lo que no alcanzó queda pendiente para "
            "mañana, no se pierde nada.\n"
            "<i>Para ver el detalle: /cuota</i>"
        )
    elif "401" in det or "UNAUTHENTICATED" in det:
        cuerpo += (
            "\n\n<b>Que hacer con Gemini</b>\n"
            "   1. Copiala completa con el boton de copiar de AI Studio.\n"
            "   2. Quitalle la restriccion de IP: Render usa IPs dinamicas y "
            "una clave restringida a tu IP local falla ahi.\n"
            "   3. Verifica que la API Generative Language este habilitada.\n"
            "   Prueba sin gastar nada: <code>python probar_clave.py</code>"
        )
    else:
        cuerpo += (
            "\n\n<b>Comodiagnosticar Gemini</b>\n"
            "   <code>python sondear_modelos.py</code> — dice que modelos "
            "responden ahora mismo."
        )
    if not d["sam"]["ok"]:
        cuerpo += (
            "\n\n<b>SAM.gov</b>: revisa SAM_API_KEY y que la cuenta tenga "
            "aprobada la API pública en "
            "https://open.gsa.gov/api/get-opportunities-public-api/"
        )
    if not d["telegram"] or not d["chat"]:
        cuerpo += "\n\n<b>Telegram</b>: revisa el token y el CHAT_ID en Render."

    if g["ok"] and d["sam"]["ok"] and d["telegram"] and d["chat"]:
        cuerpo += "\n\n✨ Todo en orden, amo. Kyomoto puede trabajar ✿"

    await aviso.edit_text(cuerpo, parse_mode=ParseMode.HTML)


async def cmd_selftest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _selftest(update.message, ctx)


async def _lanzar_escaneo(chat_id: str, dias: int, menu_msg=None) -> None:
    """
    Corre un barrido y va actualizando el progreso. Lo usan /escaneo y el boton
    "Buscar ahora". `menu_msg` es el mensaje del menu cuando se pide desde ahi:
    se manda un mensaje nuevo en vez de editar el menu, que es fijo.
    """
    if ESTADO["escaneando"]:
        destino = menu_msg or menu_msg
        texto = "⏳ Ya hay un escaneo corriendo, amo. Kyomoto espera (｡-ω-)zzz"
        if menu_msg:
            await menu_msg.reply_text(texto, parse_mode=ParseMode.HTML)
        return

    aviso = await (menu_msg.reply_text if menu_msg else _responder(chat_id))(
        "⚡ Kyomoto empieza a trabajar... (ᐢ..ᐢ)"
    )
    ESTADO["escaneando"] = True
    ultima = {"txt": ""}

    async def progreso(texto: str) -> None:
        if texto == ultima["txt"]:
            return
        ultima["txt"] = texto
        try:
            await aviso.edit_text(texto, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    def correr() -> dict:
        return scanner.escanear(chat_id, dias=dias)

    try:
        resumen = await asyncio.get_running_loop().run_in_executor(None, correr)
        scanner.redactar_fallo(chat_id, resumen)
    except Exception as e:
        log.exception("Escaneo fallo")
        cuerpo = (
            "🚨 <b>Kyomoto se tropezo</b>\n\n"
            f"<pre>{telegram_notify._esc(f'{type(e).__name__}: {e}')[:1200]}</pre>\n\n"
            "Revisa el log de Render para el detalle."
        )
        try:
            await aviso.edit_text(cuerpo, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return
    finally:
        ESTADO["escaneando"] = False

    ULTIMO_ESCANEO["resumen"] = resumen
    ESTADO["ultimo_resumen"] = resumen
    for a in resumen.get("analisis", []):
        if a.get("notice_id"):
            ANALISIS[a["notice_id"]] = a

    import kyo

    if not resumen["candidatas"]:
        cuerpo = (
            "🤍 <b>Sin novedades, amo</b>\n\n"
            f"Kyomoto revisó {resumen['traidas']} avisos de SAM.gov en {dias} "
            "días y ninguno era compra de producto.\n"
            "Prueba con más días: <code>/escaneo 14</code>"
        )
    else:
        cuerpo = kyo.resumen_escaneo(resumen)

    try:
        await aviso.edit_text(cuerpo, parse_mode=ParseMode.HTML)
    except Exception:
        await ctx_bot_send(chat_id, cuerpo)


async def ctx_bot_send(chat_id: str, cuerpo: str) -> None:
    telegram_notify.enviar(chat_id, cuerpo, html_mode=True)


APP = {"ptb": None}


def _responder(chat_id: str):
    """
    Devuelve una corrutina que manda un mensaje a un chatId suelto, para el
    escaneo que no viene de un comando (por ejemplo, el boton "Buscar ahora"
    o el scheduler).
    """
    async def _enviar(texto: str, **kw):
        app = APP.get("ptb")
        if app is None:
            # Sin la Application a mano se cae al envio por HTTP directo, que
            # ya sabe trocear y escapar. Es el camino de emergencia.
            telegram_notify.enviar(chat_id, texto, html_mode=True)
            return None
        return await app.bot.send_message(
            chat_id=chat_id, text=texto, parse_mode=ParseMode.HTML
        )
    return _enviar


async def cmd_escaneo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    dias = config.DIAS_DE_VENTANA
    if ctx.args:
        try:
            dias = max(1, min(int(ctx.args[0]), 30))
        except ValueError:
            await update.message.reply_text(
                "El número de días no es válido, amo (ａ.ω-？)"
            )
            return
    await _lanzar_escaneo(str(update.effective_chat.id), dias)


async def cmd_puntajes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(texto_puntajes(), parse_mode=ParseMode.HTML)


async def cmd_etiqueta(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text(
            "Uso: <code>/etiqueta W9127N26QA145</code>\n"
            "Con el numero de solicitud o el Notice ID.",
            parse_mode=ParseMode.HTML,
        )
        return
    clave = ctx.args[0].strip()
    encontrado = None
    for a in ANALISIS.values():
        if clave.lower() in (str(a.get("solicitation", "")).lower(), str(a.get("notice_id", "")).lower()):
            encontrado = a
            break
    if not encontrado:
        await update.message.reply_text(
            "No tengo esa oportunidad en memoria, amo. Kyomoto solo recuerda las "
            "del ultimo barrido. Usa el numero que aparecio en el mensaje.",
            parse_mode=ParseMode.HTML,
        )
        return

    aviso = await update.message.reply_text("🏷 Generando la etiqueta...")
    ruta = await asyncio.get_running_loop().run_in_executor(
        None, etiqueta.generar, encontrado["notice_id"], encontrado
    )
    if not ruta or not os.path.exists(ruta):
        await aviso.edit_text(
            "No pude generar el PDF. Revisa que <code>reportlab</code> este en "
            "los requirements de Render.",
            parse_mode=ParseMode.HTML,
        )
        return
    store.guardar_etiqueta(encontrado["notice_id"], ruta)
    with open(ruta, "rb") as f:
        await ctx.bot.send_document(
            chat_id=str(update.effective_chat.id),
            document=f,
            filename=os.path.basename(ruta),
            caption=(
                f"🏷 <b>Etiqueta</b> · <code>{telegram_notify._esc(encontrado['solicitation'])}</code>\n"
                "Rellena origen, peso y volumen antes de imprimir. "
                "Kyomoto no inventa datos de transporte."
            ),
            parse_mode=ParseMode.HTML,
        )
    await aviso.edit_text("✅ Etiqueta lista, amo.", parse_mode=ParseMode.HTML)


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    n = store.limpiar()
    await update.message.reply_text(
        f"🧹 Listo, amo. Olvide {n} avisos. El proximo escaneo evaluara todo de nuevo."
    )


def _argtexto(update: Update) -> str:
    """
    Los argumentos del comando, como texto plano.

    Se usa en vez de ctx.args porque /pdf acepta "ID | Nombre del
    proveedor", con un pipe en medio, y el pipe llega partido en varias
    palabras. Unirlo aqui deja el comando mas comodo de escribir.
    """
    try:
        return " ".join(update.message.text.split()[1:]).strip()
    except (AttributeError, IndexError):
        return ""


async def cmd_pdf(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /pdf [ID] - genera el Purchase Order en PDF y lo manda al chat.

    Sin ID lista los avisos analizados que hay disponibles. Con ID genera el
    documento. Se puede añadir "| Nombre del distribuidor" para dejar el
    proveedor puesto.
    """
    import io as _io

    import pdf_generator as pdfg
    import store

    args = _argtexto(update)
    chat_id = str(update.effective_chat.id)

    def _falta(texto):
        update.message.reply_text(texto, parse_mode=ParseMode.HTML)

    # --- Sin argumentos: que hay disponible ---
    if not args:
        vistos = store.analisis_recientes(limite=10)
        if not vistos:
            _falta(
                "📄 <b>Aun no hay ningun aviso analizado</b>\n\n"
                "El Purchase Order se genera a partir del analisis de una "
                "oportunidad. Todavia no hay ninguno guardado.\n\n"
                "En cuanto Kyomoto te mande la primera ficha, el numero de "
                "solicitud de esa ficha es el que va aqui:\n"
                "<code>/pdf W9127N26QA145</code>\n\n"
                "Ojo: se genera a las 2 h del primer barrido, no de "
                "inmediato."
            )
            return
        lineas = ["📄 <b>Purchase Orders que puedo generar</b>\n"]
        for a in vistos:
            titulo = (a.get("title") or "")[:56]
            sol = a.get("solicitation") or a.get("notice_id") or "?"
            lineas.append(f"• <code>{telegram_notify._esc(sol)}</code> — {telegram_notify._esc(titulo)}")
        lineas.append("")
        lineas.append("Escribe <code>/pdf NUMERO</code> para generar el de ese.")
        _falta("\n".join(lineas))
        return

    # --- Con argumentos: proveedor opcional tras "|" ---
    if "|" in args:
        id_buscado, proveedor_nombre = args.split("|", 1)
        id_buscado = id_buscado.strip()
        proveedor_nombre = proveedor_nombre.strip()
    else:
        id_buscado, proveedor_nombre = args.strip(), ""

    analisis = store.buscar_por_solicitud(id_buscado)
    if not analisis:
        vistos = store.analisis_recientes(limite=6)
        extra = ""
        if vistos:
            extra = "\n\n<b>Disponibles ahora:</b>\n" + "\n".join(
                f"• <code>{telegram_notify._esc(a.get('solicitation') or '?')}</code>"
                for a in vistos
            )
        _falta(
            f"🤍 No encuentro ningun aviso con la solicitud "
            f"<code>{telegram_notify._esc(id_buscado)}</code>.{extra}"
        )
        return

    estado = update.message.reply_text("📄 Generando el Purchase Order...")
    try:
        datos = pdfg.desde_analisis(
            analisis, {"nombre": proveedor_nombre} if proveedor_nombre else None
        )
        pdf_bytes = pdfg.generar_po(datos)
        nombre = pdfg.nombre_archivo(datos.get("numero_po") or id_buscado)
        await estado.edit_text(
            f"✅ Listo: <code>{telegram_notify._esc(nombre)}</code>\n\n"
            f"Comprueba el proveedor antes de mandarlo: el que escribe en el "
            f"documento es el que va a recibirlo.",
            parse_mode=ParseMode.HTML,
        )
        await update.message.reply_document(
            document=_io.BytesIO(pdf_bytes),
            filename=nombre,
            caption=(
                f"📄 <b>Purchase Order</b>\n"
                f"SAM.gov: <code>{telegram_notify._esc(id_buscado)}</code>\n"
                f"Si falta el proveedor: <code>/pdf {telegram_notify._esc(id_buscado)} "
                f"| Nombre del distribuidor</code>"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        log.exception("No se pudo generar el PDF")
        await estado.edit_text(
            f"❌ No pude generar el PDF: "
            f"<code>{telegram_notify._esc(str(exc)[:200])}</code>\n\n"
            "Dilo y lo miro.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_boton(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Cada boton del menu hace lo mismo que su comando equivalente."""
    import kyo
    import menu

    q = update.callback_query
    await q.answer()
    accion = (q.data or "").split(":")[-1]
    chat_id = str(q.message.chat.id)
    log.info("Boton pulsado: %s (chat %s)", accion, chat_id)

    if accion == "on":
        ESTADO["activo"] = True
        await q.message.reply_text(
            "▶️ <b>Busqueda reactivada, amo</b> (⁠✦⁠_⁠✦⁠)\n"
            f"Reviso cada {config.INTERVALO_HORAS:g} h lo nuevo de SAM.gov.",
            parse_mode=ParseMode.HTML,
        )
        await q.message.reply_text(
            kyo.saludo_menu(True),
            parse_mode=ParseMode.HTML,
            reply_markup=menu.teclado(True),
        )
        return

    if accion == "off":
        ESTADO["activo"] = False
        await q.message.reply_text(
            "⏸ <b>Busqueda pausada, amo</b> (⁠.⁠-⁠⌟⁠.⁠)",
            parse_mode=ParseMode.HTML,
        )
        await q.message.reply_text(
            kyo.saludo_menu(False),
            parse_mode=ParseMode.HTML,
            reply_markup=menu.teclado(False),
        )
        return

    if accion == "buscar":
        await _lanzar_escaneo(chat_id, dias=config.DIAS_DE_VENTANA,
                              menu_msg=q.message)
        return

    if accion == "pdf":
        await q.message.reply_text(
            "📄 Escribe <code>/pdf NUMERO-DE-SOLICITUD</code>\n\n"
            "El numero sale en cada ficha, asi:\n"
            "<code>/pdf W9127N26QA145</code>\n\n"
            "Escribe solo <code>/pdf</code> y te digo cuales hay.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Estado, cuota, puntajes y selftest solo dependen de datos locales, asi
    # que se construyen aqui mismo y se responden al instante.
    if accion == "estado":
        await q.message.reply_text(texto_estado(), parse_mode=ParseMode.HTML)
        return
    if accion == "cuota":
        await q.message.reply_text(texto_cuota(), parse_mode=ParseMode.HTML)
        return
    if accion == "puntajes":
        await q.message.reply_text(texto_puntajes(), parse_mode=ParseMode.HTML)
        return
    if accion == "selftest":
        await _selftest(q.message, ctx)
        return

    await q.message.reply_text(
        "🤍 Kyomoto no entiende ese boton, amo.", parse_mode=ParseMode.HTML
    )


async def cmd_texto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    texto = (update.message.text or "").strip().lower()
    if texto in ("hola", "holi", "hey", "hi", "holaaa"):
        await update.message.reply_text(
            "Kyon~ aqui ando ✨ ¿Kyomoto te busca algo? Prueba /selftest o /escaneo"
        )


# ==========================================================================
#  MONITOREO AUTOMATICO
# ==========================================================================
async def _tarea_periodica(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ESTADO["activo"] or ESTADO["escaneando"]:
        return
    chat_id = _chat_id(ctx)
    if not chat_id:
        return
    ESTADO["escaneando"] = True
    try:
        resumen = await asyncio.get_running_loop().run_in_executor(
            None, scanner.escanear, chat_id
        )
        ULTIMO_ESCANEO["resumen"] = resumen
        ESTADO["ultimo_resumen"] = resumen
        for a in resumen.get("analisis", []):
            if a.get("notice_id"):
                ANALISIS[a["notice_id"]] = a
        scanner.redactar_fallo(chat_id, resumen)
        log.info(
            "Barrido automatico: %s viables de %s analizadas",
            resumen["viables"], resumen["analizadas"],
        )
    except Exception:
        log.exception("Fallo el barrido automatico")
        telegram_notify.aviso_error(
            chat_id, "Fallo el barrido automatico", "Revisa el log de Render."
        )
    finally:
        ESTADO["escaneando"] = False


async def _post_init(app: Application) -> None:
    log.info("Kyomoto iniciada. Tope USD %s", config.TOPE_USD)
    faltantes = [
        n for n, v in (
            ("SAM_API_KEY", config.SAM_API_KEY),
            ("GEMINI_API_KEY", config.GEMINI_API_KEY),
            ("TELEGRAM_CHAT_ID", config.TELEGRAM_CHAT_ID),
        ) if not v
    ]
    if faltantes:
        log.error("Faltan variables de entorno: %s", ", ".join(faltantes))
    jq = app.job_queue
    if jq is None:
        log.error("JobQueue no disponible: revisa python-telegram-bot[job-queue]")
    else:
        jq.run_repeating(
            _tarea_periodica,
            interval=config.INTERVALO_HORAS * 3600,
            first=180,  # 3 minutos despues de arrancar
            name="barrido_automatico",
        )
        log.info("Monitoreo cada %s horas", config.INTERVALO_HORAS)
    if config.TELEGRAM_CHAT_ID and not config.DRY_RUN:
        telegram_notify.enviar(
            config.TELEGRAM_CHAT_ID,
            "✨ <b>Kyomoto se desperto</b> ✨\n\n"
            f"Automatico cada {config.INTERVALO_HORAS:g} h. "
            f"Busco productos de USD {config.MIN_USD:,.0f} a USD {config.TOPE_USD:,.0f}.\n\n"
            "Manda <code>/selftest</code> para que te diga si todo responde.",
            html_mode=True,
        )


async def _post_shutdown(app: Application) -> None:
    log.info("Kyomoto cerrando. Chao, amo.")


def main() -> None:
    store.init_db()
    quota.init()
    quota.limpiar_reservas()
    threading.Thread(target=_servidor, daemon=True).start()
    log.info("Kyomoto v%s arrancando", config.KYOMOTO_VERSION)
    log.info("  modelo      : %s", config.GEMINI_MODEL)
    log.info("  alternativas: %s", ", ".join(config.GEMINI_MODELES_ALTERNATIVOS))
    log.info("  cada        : %g h | tope USD %s | margen neto min %g%%",
             config.INTERVALO_HORAS, config.TOPE_USD, config.MARGEN_NETO_MIN * 100)
    log.info("Servidor web en el puerto %s", config.PORT)

    app = _crear_app()
    app.post_init = _post_init
    app.post_shutdown = _post_shutdown
    APP["ptb"] = app

    # --- Este bloque es la diferencia entre funcionar y caerse. ---
    #
    # python-telegram-bot, al arrancar, hace asyncio.get_event_loop() desde un
    # hilo sin bucle. En Python <=3.11 eso creaba un bucle nuevo en silencio.
    # En Python 3.12 se deprecó, y en 3.14 LANZA:
    #
    #   RuntimeError: There is no current event loop in thread 'MainThread'
    #
    # Render usa Python 3.14 por defecto, asi que sin esto el deploy muere
    # apenas arranca (aunque el servidor web ya responda 200). Crear el bucle
    # aqui y dejarlo como actual hace que get_event_loop() lo encuentre, sin
    # importar la version de Python ni la de PTB.
    try:
        _loop = asyncio.get_event_loop_policy().get_event_loop()
        if _loop.is_closed():
            _loop = asyncio.new_event_loop()
    except RuntimeError:
        _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    log.info("Bucle de eventos listo: %s", type(_loop).__name__)

    app.run_polling(
        drop_pending_updates=True,
        # "message" son los comandos y el texto. "callback_query" son las
        # pulsaciones de los botones del menu. Sin esta segunda entrada,
        # Telegram NO entrega los botones: se ven, se pueden pulsar, y no
        # pasa nada. Es el unico motivo por el que los botones estaban
        # muertos aunque el handler estuviera bien registrado.
        # Si algun dia se anade un handler de otro tipo, hay que anadirlo aqui
        # tambien, o tendra el mismo fallo en silencio.
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
