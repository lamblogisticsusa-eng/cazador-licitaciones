# -*- coding: utf-8 -*-
"""
test_fase11.py - El cortacircuitos de fallos consecutivos de la IA.

El problema que resuelve: `quota.liberar()` (Fase 10) devolvio el cupo a los
avisos cuyo analisis fallo, que es lo correcto, pero con la clave de Gemini
rota eso convierte cada barrido en el mismo reintento inutil -12 veces al dia
el mismo lote, el mismo error- y `gastar_fallo()` no lo frenaba porque suma
`fallidas` y no `llamadas`.

Cuatro cosas que tienen que quedar ciertas:

  1. La guarda dispara tras N fallos consecutivos y NO toca `estado()`:
     `test_cuota.py` sigue midiendo lo mismo que siempre.
  2. Un solo analisis bueno resetea la racha al instante.
  3. Con la pausa puesta, el escaner NO llama a Gemini y lo dice en el
     reporte, distinguiendolo de un barrido normal sin oportunidades.
  4. Un barrido sin llamadas no concluye nada: ni suma ni resetea.

Doble modo: se puede correr con pytest o con `python test_fase11.py`.
No toca la red: SAM.gov y Gemini van falsos, igual que en test_fase10.py.
"""
import os
import sys
import tempfile
from contextlib import contextmanager

# Ruta FIJA, igual que test_cuota.py y test_embudo.py, y no mkdtemp(): cuando
# el fichero se corre como `python test_fase11.py`, pytest.main([__file__])
# lo importa una SEGUNDA vez y un mkdtemp nuevo daria otra ruta. `config` ya
# estaria leido con la primera, asi que al salir de _base_propia se
# restauraria un fichero que nunca se creo y sqlite crearia uno vacio:
# "no such table: vista_quota". Con nombre fijo las dos importaciones ven lo
# mismo.
DB = os.path.join(tempfile.gettempdir(), "kyo_test_fase11.db")
for _suf in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _suf)
    except OSError:
        pass
os.environ["DB_PATH"] = DB
os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("SAM_API_KEY", "SAM-de-prueba")
os.environ.setdefault("GEMINI_API_KEY", "x")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:FAKE")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
# Sin esto cada analisis falso duerme 8 s y el archivo tarda media hora.
os.environ["GEMINI_PAUSA_SEG"] = "0"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

import config  # noqa: E402
import gemini_analyzer  # noqa: E402
import quota  # noqa: E402
import scanner  # noqa: E402
import store  # noqa: E402

# Las claves exactas que `estado()` devolvia ANTES de la Fase 11. Si esta
# lista cambia, `test_cuota.py` deja de medir lo que cree que mide.
CLAVES_ESTADO = {
    "dia", "usadas", "fallidas", "presupuesto", "configurado", "por_modelo",
    "modelos", "restantes", "agotado",
}

LLAMADAS_GEMINI: list[str] = []


@contextmanager
def _base_propia():
    """Corre sobre SU propia base y la deja como estaba.

    Se cambia config.DB_PATH en el sitio y no solo en el entorno: en una
    sesion de pytest que ya importo config, la variable de entorno llega
    tarde y el barrido escribiria en la base de otro test.
    """
    anterior = config.DB_PATH
    config.DB_PATH = DB
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(DB + suf)
        except OSError:
            pass
    try:
        store.init_db()
        quota.init()
        LLAMADAS_GEMINI.clear()
        yield
    finally:
        config.DB_PATH = anterior


# ==========================================================================
# 1) LA GUARDA EN SI
# ==========================================================================
def test_bajo_el_umbral_no_pausa_nada():
    """Dos fallos seguidos con umbral 3 no tienen que parar nada."""
    with _base_propia():
        assert quota.ia_registrar(0, 1)["activa"] is False
        e = quota.ia_registrar(0, 1)
        assert e["consecutivos"] == 2, e
        assert e["activa"] is False, e
        assert e["umbral"] == config.IA_FALLOS_CONSECUTIVOS_MAX
        assert e["pausa_hasta"] == ""
        assert quota.ia_en_pausa() is False


def test_la_guarda_dispara_tras_n_fallos_consecutivos():
    """El caso pedido: N barridos seguidos sin ni un analisis bueno."""
    with _base_propia():
        umbral = config.IA_FALLOS_CONSECUTIVOS_MAX
        ultimo = None
        for i in range(umbral):
            ultimo = quota.ia_registrar(0, 1)
            if i < umbral - 1:
                assert ultimo["activa"] is False, (i, ultimo)
        assert ultimo is not None
        assert ultimo["consecutivos"] == umbral, ultimo
        assert ultimo["activa"] is True, ultimo
        # El cooldown tiene que existir y ser futuro, o la pausa seria eterna.
        assert ultimo["minutos_restantes"] > 0, ultimo
        assert ultimo["pausa_hasta"], ultimo
        assert quota.ia_en_pausa() is True
        # Y sigue acumulando si nadie lo arregla.
        assert quota.ia_registrar(0, 1)["consecutivos"] == umbral + 1


def test_un_exito_resetea_el_contador_al_instante():
    """Un solo analisis bueno tiene que bastar, sin ventanas ni decaimiento."""
    with _base_propia():
        for _ in range(config.IA_FALLOS_CONSECUTIVOS_MAX):
            quota.ia_registrar(0, 1)
        assert quota.ia_en_pausa() is True

        e = quota.ia_registrar(1, 0)
        assert e["consecutivos"] == 0, e
        assert e["activa"] is False, e
        assert e["pausa_hasta"] == "", "la pausa tambien tiene que borrarse"
        assert quota.ia_en_pausa() is False
        assert quota.ia_estado()["consecutivos"] == 0

        # Y a partir de cero vuelve a necesitar el umbral entero.
        assert quota.ia_registrar(0, 1)["consecutivos"] == 1


def test_un_barrido_sin_llamadas_no_concluye_nada():
    """Sin candidatos no hay veredicto: ni sumar ni resetear."""
    with _base_propia():
        quota.ia_registrar(0, 1)
        quota.ia_registrar(0, 1)
        e = quota.ia_registrar(0, 0)
        assert e["consecutivos"] == 2, "un barrido vacio no resetea"
        assert e["activa"] is False
        # Tampoco un exito de 0 con fallos de 0: mismo caso, otro orden.
        e = quota.ia_registrar(0, 0)
        assert e["consecutivos"] == 2, e


def test_el_umbral_es_configurable():
    """`IA_FALLOS_CONSECUTIVOS_MAX` manda, y respeta un piso de 1."""
    with _base_propia():
        previo = config.IA_FALLOS_CONSECUTIVOS_MAX
        try:
            config.IA_FALLOS_CONSECUTIVOS_MAX = 2
            assert quota.ia_registrar(0, 1)["activa"] is False
            assert quota.ia_registrar(0, 1)["activa"] is True
            # Reseteo y con umbral 1 basta UN fallo.
            quota.ia_reiniciar()
            config.IA_FALLOS_CONSECUTIVOS_MAX = 1
            assert quota.ia_registrar(0, 1)["activa"] is True
            # Un umbral absurdo (0 o negativo) no puede pausar para siempre.
            quota.ia_reiniciar()
            config.IA_FALLOS_CONSECUTIVOS_MAX = 0
            e = quota.ia_registrar(0, 1)
            assert e["umbral"] == 1 and e["activa"] is True, e
        finally:
            config.IA_FALLOS_CONSECUTIVOS_MAX = previo


def test_ia_reiniciar_devuelve_el_cortacircuitos_a_cero():
    """Existe para pruebas y para un operador; no inventa filas."""
    with _base_propia():
        assert quota.ia_reiniciar() is False, "no habia nada que borrar"
        for _ in range(config.IA_FALLOS_CONSECUTIVOS_MAX):
            quota.ia_registrar(0, 1)
        assert quota.ia_en_pausa() is True
        assert quota.ia_reiniciar() is True
        assert quota.ia_en_pausa() is False
        assert quota.ia_estado()["consecutivos"] == 0


# ==========================================================================
# 2) NO TOCA EL CONTRATO DE `estado()` NI DE `reservar()`/`liberar()`
# ==========================================================================
def test_estado_sigue_devolviendo_exactamente_las_mismas_claves():
    """El contrato de `estado()` no se mueve ni un centimetro."""
    with _base_propia():
        e = quota.estado()
        assert set(e) == CLAVES_ESTADO, set(e) ^ CLAVES_ESTADO
        assert e["usadas"] == 0
        assert e["restantes"] == e["presupuesto"]
        assert e["agotado"] is False


def test_los_fallos_de_ia_no_se_colan_en_el_presupuesto():
    """
    LA PRUEBA DE COMPATIBILIDAD.

    `test_cuota.py` afirma "Se detiene en TOPE, no en TOPE+6" y "Restantes
    en 0" contra `usadas`. Si la guarda nueva empezara a sumar en
    `uso_gemini`, esas aserciones se romperian.

    Ademas `fallidas` tiene que seguir en 0: los fallos de IA se cuentan en
    SU tabla, y no en la de la cuota. Son dos contadores distintos con dos
    motivos distintos.
    """
    with _base_propia():
        for _ in range(config.IA_FALLOS_CONSECUTIVOS_MAX + 5):
            quota.ia_registrar(0, 1)
        assert quota.ia_en_pausa() is True

        e = quota.estado()
        assert set(e) == CLAVES_ESTADO, set(e) ^ CLAVES_ESTADO
        assert e["usadas"] == 0, "la guarda no gasta presupuesto"
        assert e["fallidas"] == 0, "la guarda no toca el contador viejo"
        assert e["restantes"] == e["presupuesto"]
        assert e["agotado"] is False

        # Y la maquina de siempre sigue intacta debajo.
        quota.gastar(3)
        e = quota.estado()
        assert e["usadas"] == 3 and e["restantes"] == e["presupuesto"] - 3


def test_reservar_y_liberar_no_cambiaron():
    """La Fase 10 tenia que seguir funcionando igual."""
    with _base_propia():
        assert quota.reservar("nid-f11") is True
        assert quota.reservar("nid-f11") is False
        assert quota.liberar("nid-f11") is True
        assert quota.reservar("nid-f11") is True
        assert quota.liberar("nid-que-no-existe") is False
        # La racha no afecta a las reservas ni al revés.
        for _ in range(config.IA_FALLOS_CONSECUTIVOS_MAX):
            quota.ia_registrar(0, 1)
        assert quota.reservar("nid-f11b") is True


# ==========================================================================
# 3) INTEGRACION: escanear() con la red falsa
# ==========================================================================
def _titulo(i: int) -> str:
    return (f"PARTS KIT,SECTOR {i} physical goods replacement "
            "equipment assembly")


def _aviso(i: int) -> dict:
    return {
        "noticeId": f"nid{i:03d}",
        "title": _titulo(i),
        "naicsCode": "332999",
        "typeOfSetAside": "",
        "postedDate": "2026-11-01",
        "description": (
            f"<p>Adquisicion de {20 + i} KIT para {_titulo(i)}. "
            "Requiere entrega fisica en base. "
            f"Not to exceed USD {40_000 + i * 500:,}.00.</p>"
        ),
    }


def _bajar(candidatos):
    return {
        o["noticeId"]: (
            f"Adquisicion de 40 KIT para {_titulo(0)} con entrega fisica "
            f"directa. Not to exceed USD {40_000:,}.00. "
            "Se requiere distribuidor con stock en USA."
        )
        for o in candidatos
    }


def _barrer(dias=None, ptype="o,a", stats=None, indice_psc=False):
    """Fake de la red: 30 avisos buenos, sin tocar SAM.gov."""
    if isinstance(stats, dict):
        stats.update({"bloques_ok": 1, "bloques_error": 0, "errores": []})
    return (_aviso(i) for i in range(30))


def _analizar_bueno(opp, desc, lugar, continuar=None, degradado=False):
    LLAMADAS_GEMINI.append(opp["noticeId"])
    return {
        "notice_id": opp["noticeId"],
        "viable": True,
        "titulo": opp.get("title", ""),
        "title": opp.get("title", ""),
        "producto": "kit de repuestos",
        "margen_neto_porcentaje": 22.0,
        "motivo_descarte": "",
    }


def _analizar_roto(opp, desc, lugar, continuar=None, degradado=False):
    LLAMADAS_GEMINI.append(opp["noticeId"])
    raise gemini_analyzer.GeminiError("503: modelo saturado")


@contextmanager
def _red_falsa(analizar=_analizar_bueno):
    orig = (scanner.sam_api.barrer, scanner._bajar_descripciones,
            scanner.gemini_analyzer.analizar)
    scanner.sam_api.barrer = _barrer
    scanner._bajar_descripciones = _bajar
    scanner.gemini_analyzer.analizar = analizar
    try:
        yield
    finally:
        (scanner.sam_api.barrer, scanner._bajar_descripciones,
         scanner.gemini_analyzer.analizar) = orig


def test_con_la_ia_en_pausa_no_se_llama_a_gemini():
    """
    EL OBJETIVO DE LA FASE: no reintentar a ciegas.

    Con la pausa puesta, el escaner tiene que frenar ANTES de reservar cupos,
    no llamar a Gemini ni una vez, y decirlo en el reporte.
    """
    with _base_propia(), _red_falsa():
        for _ in range(config.IA_FALLOS_CONSECUTIVOS_MAX):
            quota.ia_registrar(0, 1)
        assert quota.ia_en_pausa() is True

        res = scanner.escanear("1")

        # Todo esto va DENTRO del contexto: `store` y `quota` leen
        # `config.DB_PATH`, que _base_propia solo tiene apuntando a SU base
        # mientras el bloque esta abierto.
        #
        # No se llamo ni una vez.
        assert LLAMADAS_GEMINI == [], f"llamo {len(LLAMADAS_GEMINI)} veces"
        # Y no se reservo cupo: si se reservara sin someterse, el barrido
        # siguiente llegaria con todo ya gastado. Ese era el bug de la Fase 10.
        with quota._conexion() as c:
            reservas = c.execute(
                "SELECT COUNT(*) FROM vista_quota").fetchone()[0]
        assert reservas == 0, f"se reservaron {reservas} avisos sin analizar"
        assert store.ids_procesados() == set(), "nada se marco como visto"

        # Los numeros del embudo, explicados.
        assert res["puntuales"] > 0, "el filtro si dejo pasar avisos"
        assert res["analizadas"] == 0, res["analizadas"]
        emb = res.get("embudo") or {}
        assert emb.get("ia_pausa", 0) > 0, emb
        assert "IA en pausa por fallos x" in (res.get("embudo_texto") or ""), \
            res.get("embudo_texto")

        # Y el reporte lo dice como un fallo, no como un dia sin nada.
        assert res["errores"], "el silencio era exactamente lo que no queriamos"
        assert any("PAUSA" in e for e in res["errores"]), res["errores"]
        assert any("NO se marcaron como vistos" in e for e in res["errores"]), \
            res["errores"]
        assert res["ia_estado"]["activa"] is True
        # La racha NO se borra por haber pausado: la pausa no es un exito.
        assert (res["ia_estado"]["consecutivos"]
                >= config.IA_FALLOS_CONSECUTIVOS_MAX)


def test_un_barrido_bueno_resetea_la_racha_por_el_escaner():
    """El reset tiene que venir del camino REAL, no solo de la unitaria."""
    with _base_propia(), _red_falsa():
        # Racha viva pero por debajo del umbral: deja pasar el gate.
        quota.ia_registrar(0, 1)
        quota.ia_registrar(0, 1)
        assert quota.ia_estado()["consecutivos"] == 2
        assert quota.ia_en_pausa() is False

        res = scanner.escanear("1")

        assert LLAMADAS_GEMINI, "tenia que analizar de verdad"
        assert res["analizadas"] > 0, res["analizadas"]
        assert res["ia_estado"]["consecutivos"] == 0, res["ia_estado"]
        assert res["ia_estado"]["activa"] is False, res["ia_estado"]
        # Y la base, no solo el dict que devolvio el barrido.
        assert quota.ia_estado()["consecutivos"] == 0


def test_un_barrido_roto_va_acumulando_hasta_armar_la_pausa():
    """Cada barrido con la API caida suma uno; al umbral, pausa."""
    with _base_propia(), _red_falsa(analizar=_analizar_roto):
        umbral = config.IA_FALLOS_CONSECUTIVOS_MAX
        for i in range(umbral):
            res = scanner.escanear("1")
            esperado = i + 1
            assert res["ia_estado"]["consecutivos"] == esperado, (
                i, res["ia_estado"])
            if esperado < umbral:
                assert res["ia_estado"]["activa"] is False, res["ia_estado"]
            # Cada barrido fallo en todo el lote, pero la cuota NO se gasto:
            # `gastar()` solo corre en el exito.
            assert quota.estado()["usadas"] == 0
        assert res["ia_estado"]["activa"] is True, res["ia_estado"]
        assert res["errores"], "tenia que decirse"


def test_el_mensaje_de_fin_de_barrido_distingue_pausa_de_vacio():
    """
    El requisito de transparencia: "Barrido sin oportunidades" con el
    cierre "No es un fallo" seria mentira con la API caida.
    """
    enviados: list[str] = []
    original_enviar = scanner.telegram_notify.enviar
    original_dry = config.DRY_RUN
    original_umbral = config.IA_FALLOS_CONSECUTIVOS_MAX
    scanner.telegram_notify.enviar = (
        lambda chat_id, cuerpo, html_mode=False, **k: enviados.append(cuerpo)
        or True
    )
    config.DRY_RUN = False
    config.IA_FALLOS_CONSECUTIVOS_MAX = 1
    try:
        with _base_propia(), _red_falsa(analizar=_analizar_roto):
            res = scanner.escanear("1")
    finally:
        scanner.telegram_notify.enviar = original_enviar
        config.DRY_RUN = original_dry
        config.IA_FALLOS_CONSECUTIVOS_MAX = original_umbral

    assert res["ia_estado"]["activa"] is True, res["ia_estado"]
    assert len(enviados) == 1, f"se mandaron {len(enviados)} mensajes"
    cuerpo = enviados[0]

    # El rotulo cambia...
    assert "Análisis IA en pausa" in cuerpo, cuerpo
    assert "Barrido sin oportunidades" not in cuerpo, cuerpo
    # ...y el cierre deja de mentir.
    assert "No es un fallo" not in cuerpo, cuerpo
    assert "Esto NO es un barrido vacio" in cuerpo, cuerpo
    # Con los numeros que hacen falta para diagnosticar.
    assert "Pasaron el filtro" in cuerpo
    assert "Analizados por IA" in cuerpo

    # Y el HTML sigue balanceado, que es lo que rompio el 28-sep.
    assert cuerpo.count("<b>") == cuerpo.count("</b>"), cuerpo
    assert cuerpo.count("<i>") == cuerpo.count("</i>"), cuerpo
    assert "&nbsp;" not in cuerpo, cuerpo


def test_sin_pausa_el_mensaje_es_el_de_siempre():
    """La rama normal no se mueve ni un caracter."""
    enviados: list[str] = []
    original_enviar = scanner.telegram_notify.enviar
    original_dry = config.DRY_RUN
    original_umbral = config.IA_FALLOS_CONSECUTIVOS_MAX
    scanner.telegram_notify.enviar = (
        lambda chat_id, cuerpo, html_mode=False, **k: enviados.append(cuerpo)
        or True
    )
    config.DRY_RUN = False
    # Umbral muy alto: los fallos se acumulan pero no pausan.
    config.IA_FALLOS_CONSECUTIVOS_MAX = 99
    try:
        with _base_propia(), _red_falsa(analizar=_analizar_roto):
            res = scanner.escanear("1")
    finally:
        scanner.telegram_notify.enviar = original_enviar
        config.DRY_RUN = original_dry
        config.IA_FALLOS_CONSECUTIVOS_MAX = original_umbral

    assert res["ia_estado"]["activa"] is False, res["ia_estado"]
    assert len(enviados) == 1, f"se mandaron {len(enviados)} mensajes"
    cuerpo = enviados[0]
    assert "Barrido sin oportunidades" in cuerpo, cuerpo
    assert "No es un fallo" in cuerpo, cuerpo
    assert "Análisis IA en pausa" not in cuerpo, cuerpo
    assert cuerpo.count("<b>") == cuerpo.count("</b>"), cuerpo
    assert cuerpo.count("<i>") == cuerpo.count("</i>"), cuerpo


# ==========================================================================
# 4) VISIBILIDAD EN LA TARJETA (kyo)
# ==========================================================================
def test_la_tarjeta_de_barrido_muestra_la_pausa():
    """`kyo.resumen_escaneo` es lo que el usuario lee al terminar."""
    import kyo
    base = {"traidas": 3413, "puntuales": 40, "analizadas": 0, "viables": 0}

    # Sin la clave (resumenes armados a mano, tests viejos): ni una linea.
    vieja = kyo.resumen_escaneo(base)
    assert "pausa" not in vieja.lower(), vieja
    assert vieja.count("<b>") == vieja.count("</b>")

    # Pausa apagada: tampoco.
    apagada = dict(base, ia_estado={
        "consecutivos": 3, "umbral": 3, "activa": False,
        "minutos_restantes": 0, "pausa_hasta": ""})
    assert "pausa" not in kyo.resumen_escaneo(apagada).lower()

    # Pausa encendida: con racha y con cooldown.
    prendida = dict(base, ia_estado={
        "consecutivos": 3, "umbral": 3, "activa": True,
        "minutos_restantes": 147, "pausa_hasta": "2999-01-01T00:00:00+00:00"})
    tarjeta = kyo.resumen_escaneo(prendida)
    assert "Análisis IA en pausa" in tarjeta, tarjeta
    assert "3 barridos seguidos" in tarjeta, tarjeta
    assert "147 min" in tarjeta, tarjeta
    # HTML balanceado y sin entidades que Telegram rechaza.
    assert tarjeta.count("<b>") == tarjeta.count("</b>"), tarjeta
    assert tarjeta.count("<i>") == tarjeta.count("</i>"), tarjeta
    assert "&nbsp;" not in tarjeta, tarjeta
    # Va con los numeros, no escondida al final.
    assert tarjeta.find("Analizados por IA") < tarjeta.find("en pausa")


def test_ia_estado_no_se_le_mal_por_una_pausa_vieja():
    """
    `pausa_hasta` de ayer sigue en la tabla (nada la borra sola) pero ya no
    pausa: se compara por FECHA, no por presencia. Si no, un proceso vivo que
    cruce la medianoche se quedaria con el boton pulsado para siempre, que es
    el mismo defecto que tuvo `vista_quota` en la Fase 10.
    """
    from datetime import datetime, timezone, timedelta
    with _base_propia():
        quota.ia_registrar(0, 1)
        with quota._conexion() as c:
            c.execute(
                "UPDATE ia_cortacircuitos SET pausa_hasta = ? WHERE id = 1",
                ((datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),)
            )
        e = quota.ia_estado()
        assert e["activa"] is False, e
        assert e["minutos_restantes"] == 0, e
        assert quota.ia_en_pausa() is False

        # Y una futura si pausa.
        with quota._conexion() as c:
            c.execute(
                "UPDATE ia_cortacircuitos SET pausa_hasta = ? WHERE id = 1",
                ((datetime.now(timezone.utc) + timedelta(minutes=30))
                 .isoformat(),)
            )
        e = quota.ia_estado()
        assert e["activa"] is True, e
        assert 0 < e["minutos_restantes"] <= 30, e


if __name__ == "__main__":
    codigo = pytest.main(["-q", "--no-header", __file__])
    print()
    print("RESULTADO: " + ("la IA deja de reintentar a ciegas y lo dice, "
                           "en verde" if codigo == 0 else "FALLA"))
    sys.exit(codigo)
