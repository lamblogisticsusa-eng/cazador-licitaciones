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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_texto))
    return app


# ==========================================================================
#  COMANDOS
# ==========================================================================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Kyon~ Kyomoto reportandose ✨\n\n"
        "Soy tu asistente de licitaciones en SAM.gov. Busco solo oportunidades "
        "de compra de PRODUCTOS que se puedan despachar a destino, entre "
        f"USD {config.MIN_USD:,.0f} y USD {config.TOPE_USD:,.0f}.\n\n"
        "<b>Comandos</b>\n"
        "/selftest  - Revisa que las 3 APIs Respondan\n"
        "/escaneo [dias]  - Barrido manual (por defecto "
        f"{config.DIAS_DE_VENTANA} dias)\n"
        "/puntajes - Por que Kyomoto si o no cada aviso\n"
        "/etiqueta <num> - Etiqueta PDF de una oportunidad\n"
        "/estado    - Ultimo barrido\n"
        "/off /on   - Pausar o reactivar el monitoreo\n"
        "/reset     - Olvidar todo el historial\n\n"
        f"<i>Automatico cada {config.INTERVALO_HORAS:g} horas.</i>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_on(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ESTADO["activo"] = True
    await update.message.reply_text("▶️ Monitoreo reactivado, amo (⁠✦⁠_⁠✦⁠)")


async def cmd_off(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ESTADO["activo"] = False
    await update.message.reply_text("⏸️ Monitoreo pausado, amo (⁠.⁠-⁠⌟⁠.⁠)")


async def cmd_estado(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    r = ULTIMO_ESCANEO.get("resumen")
    q = quota.estado()
    partes = [
        "📊 <b>Estado de Kyomoto</b>",
        f"• Monitoreo: {'🟢 activo' if ESTADO['activo'] else '🔴 pausado'}",
        f"• Escaneando ahora: {'sí' if ESTADO['escaneando'] else 'no'}",
        f"• Historico guardado: {store.total_procesadas()} avisos",
        f"• Ventana de busqueda: {config.DIAS_DE_VENTANA} dias",
        f"• Tope: USD {config.TOPE_USD:,.0f} | Minimo: USD {config.MIN_USD:,.0f}",
        "",
        "<b>Cuota de Gemini hoy</b> (reinicia a medianoche del Pacifico)",
        f"• Usadas: {q['usadas']}/{q['presupuesto']} | Restan: {q['restantes']}",
    ]
    if q["fallidas"]:
        partes.append(f"• Fallidas por cuota: {q['fallidas']}")
    if q["agotado"]:
        partes.append("• ⚠️ Agotada: Kyomoto no analizara mas hasta mañana")
    if r:
        partes += [
            "",
            "<b>Ultimo barrido</b>",
            f"• Traidas de SAM.gov: {r['traidas']}",
            f"• Potencialmente bienes: {r['candidatas']}",
            f"• Pasaron el filtro: {r['puntuales']}",
            f"• Analizadas por Gemini: {r['analizadas']}",
            f"• Viables: {r['viables']}  |  Notificadas: {r['notificadas']}",
        ]
        if r["errores"]:
            partes += ["", f"⚠️ {len(r['errores'])} problema(s) en el ultimo barrido"]
    partes.append("" if not config.DRY_RUN else "")
    if config.DRY_RUN:
        partes.append("🧪 <b>DRY_RUN activo</b>: no se envia nada.")
    await update.message.reply_text("\n".join(partes), parse_mode=ParseMode.HTML)


async def cmd_cuota(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = quota.estado()
    dias = quota.dias_registrados()
    lineas = [
        "🔋 <b>Cuota de Gemini</b>",
        f"• Hoy ({q['dia']}): <b>{q['usadas']}/{q['presupuesto']}</b> usadas",
        f"• Restan: <b>{q['restantes']}</b>",
        f"• Ritmo: 1 llamada cada {config.GEMINI_PAUSA_SEG:g}s, "
        f"{config.GEMINI_WORKERS} a la vez",
    ]
    if q["fallidas"]:
        lineas.append(f"• Hoy fallaron por cuota de Google: {q['fallidas']}")
    if dias:
        lineas += ["", "<b>Ultimos dias</b>"]
        for d in dias:
            barra = "▰" * min(d["llamadas"], 20)
            lineas.append(f"• {d['dia']}: {d['llamadas']} {barra}")
    lineas += [
        "",
        "<i>Google reinicia su cuota a la medianoche del Pacifico (17:00 hora de "
        "Chile en invierno). Lo que no se alcanza hoy se reintenta manana.</i>",
    ]
    await update.message.reply_text("\n".join(lineas), parse_mode=ParseMode.HTML)


async def cmd_selftest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Lo primero que debes correr. Dice en que parte esta el problema."""
    aviso = await update.message.reply_text("🩺 Autodiagnostico de Kyomoto... (puede tardar ~20s)")

    def revisar() -> tuple[str, list[str]]:
        lineas, fallas = [], []
        if config.TELEGRAM_BOT_TOKEN:
            lineas.append("✅ <b>Telegram token</b> presente")
        else:
            lineas.append("❌ <b>TELEGRAM_BOT_TOKEN</b> vacio")
            fallas.append("Telegram")
        if config.TELEGRAM_CHAT_ID:
            lineas.append(f"✅ <b>Chat destino</b>: <code>{telegram_notify._esc(config.TELEGRAM_CHAT_ID)}</code>")
        else:
            lineas.append("❌ <b>TELEGRAM_CHAT_ID</b> vacio")
            fallas.append("Chat destino")

        sam = sam_api.verificar_api()
        if sam["ok"]:
            lineas.append(f"✅ <b>SAM.gov</b>: {telegram_notify._esc(sam['detalle'])}")
        else:
            lineas.append(f"❌ <b>SAM.gov</b>: {telegram_notify._esc(sam['detalle'])}")
            fallas.append("SAM.gov")

        gem = gemini_analyzer.verificar_api()
        if gem["ok"]:
            lineas.append(f"✅ <b>Gemini</b>: {telegram_notify._esc(gem['detalle'])}")
        else:
            lineas.append(f"❌ <b>Gemini</b>: {telegram_notify._esc(gem['detalle'])}")
            fallas.append("Gemini")
        return "\n".join(lineas), fallas

    lineas, fallas = await asyncio.get_running_loop().run_in_executor(None, revisar)

    cuerpo = f"🩺 <b>Autodiagnostico</b>\n\n{lineas}\n"
    if fallas:
        cuerpo += "\n<b>Que hacer</b>\n"
        if "Gemini" in fallas:
            cuerpo += (
                "• <b>Gemini</b>: Google devolvio 401, no reconoce la clave. "
                "Ojo: desde mayo 2026 las claves de AI Studio ya NO empiezan "
                "con AIza, asi que el prefijo no dice nada. Revisa en este orden:\n"
                "   1. La clave se copio incompleta.\n"
                "   2. Tiene restriccion de IP: Render usa IPs dinamicas y una "
                "clave restringida a tu IP local falla ahi.\n"
                "   3. Falta habilitar la API Generative Language en el proyecto.\n"
                "   4. Es una clave de otro producto de Google.\n"
                "   Crea una auth key nueva sin restriccion en "
                "https://aistudio.google.com/apikey\n"
            )
        if "SAM.gov" in fallas:
            cuerpo += (
                "• <b>SAM.gov</b>: revisa SAM_API_KEY y que la cuenta tenga la "
                "API pública aprobada en https://open.gsa.gov/api/get-opportunities-public-api/\n"
            )
        if "Telegram" in fallas or "Chat destino" in fallas:
            cuerpo += "• <b>Telegram</b>: revisa el token y el CHAT_ID en Render.\n"
    else:
        cuerpo += "\n✨ Todo en orden. Kyomoto puede trabajar."

    await aviso.edit_text(cuerpo, parse_mode=ParseMode.HTML)


async def cmd_escaneo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if ESTADO["escaneando"]:
        await update.message.reply_text("⏳ Ya hay un escaneo corriendo, amo. Kyomoto espera.")
        return

    dias = config.DIAS_DE_VENTANA
    args = ctx.args
    if args:
        try:
            dias = max(1, min(int(args[0]), 30))
        except ValueError:
            await update.message.reply_text("El numero de dias no es valido, amo.")
            return

    chat_id = str(update.effective_chat.id)
    aviso = await update.message.reply_text("⚡ Kyomoto empieza a trabajar...")
    ESTADO["escaneando"] = True
    ultima = {"txt": ""}

    async def progreso(texto: str) -> None:
        if texto == ultima["txt"]:
            return
        ultima["txt"] = texto
        try:
            await aviso.edit_text(texto, parse_mode=ParseMode.HTML)
        except Exception:
            pass  # el mensaje es muy viejo o el texto no cambio

    def correr() -> dict:
        return scanner.escanear(chat_id, dias=dias)

    try:
        resumen = await asyncio.get_running_loop().run_in_executor(None, correr)
        scanner.redactar_fallo(chat_id, resumen)
    except Exception as e:
        ESTADO["escaneando"] = False
        log.exception("Escaneo fallo")
        cuerpo = (
            "🚨 <b>Kyomoto se tropezo</b>\n\n"
            f"<pre>{telegram_notify._esc(f'{type(e).__name__}: {e}')[:1500]}</pre>\n\n"
            "Revisa el log de Render para el detalle completo."
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

    if not resumen["candidatas"]:
        cuerpo = (
            "🤍 <b>Sin novedades, amo</b>\n\n"
            f"Revise {resumen['traidas']} avisos de SAM.gov en {dias} dias y "
            "ninguno era compra de producto.\n\n"
            "Prueba con mas dias: <code>/escaneo 14</code>"
        )
    else:
        cuerpo = (
            f"✨ <b>Escaneo terminado, amo</b> ✨\n\n"
            f"• Avisos revisados: {resumen['traidas']}\n"
            f"• Candidato a producto: {resumen['candidatas']}\n"
            f"• Pasaron el filtro: {resumen['puntuales']}\n"
            f"• Analizados por Kyomoto: {resumen['analizadas']}\n"
            f"• <b>Viables: {resumen['viables']}</b>\n"
            f"• Notificados: {resumen['notificadas']}\n"
        )
        if not config.DRY_RUN and resumen["viables"] == 0 and resumen["analizadas"] > 0:
            cuerpo += "\n<i>Los analisis de arriba los puedes ver con /puntajes</i>"
    try:
        await aviso.edit_text(cuerpo, parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text(cuerpo, parse_mode=ParseMode.HTML)


async def cmd_puntajes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    r = ULTIMO_ESCANEO.get("resumen") or ESTADO["ultimo_resumen"]
    detalle = (r or {}).get("detalle_puntajes") or []
    if not detalle:
        await update.message.reply_text(
            "Aun no hay puntajes. Corre <code>/escaneo</code> primero, amo."
        )
        return
    lineas = ["🧮 <b>Por que Kyomoto filtro esto</b>", ""]
    for d in detalle:
        motivos = "; ".join(str(x) for x in d["motivos"])
        lineas.append(f"<b>[{d['puntaje']}]</b> {telegram_notify._esc(d['titulo'])}")
        lineas.append(f"<i>{telegram_notify._esc(motivos)}</i>")
        lineas.append("")
    await update.message.reply_text(
        "\n".join(lineas)[:4000], parse_mode=ParseMode.HTML
    )


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
    log.info("Servidor web en el puerto %s", config.PORT)

    app = _crear_app()
    app.post_init = _post_init
    app.post_shutdown = _post_shutdown
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message"],
    )


if __name__ == "__main__":
    main()
