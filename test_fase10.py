# -*- coding: utf-8 -*-
"""
test_fase10.py - Lo que el embudo tenia que decir y no decia.

Tres grupos, y los tres salen de algo observado en produccion:

1) REGLAS DURAS. Level I, SUBSAFE, CONFIDENTIAL, DD Form 254 y la familia
   nuclear naval tienen que salir AUTO_REJECT con el RFQ bloqueado. Y la
   guarda contraria: una clausula "confidential information" al final de un
   contrato comercial NO puede rechazar nada, porque esa frase es
   boilerplate de casi cualquier contrato y rechazarla seria rechazar la
   mitad del catalogo.

2) SENALES COTS. Un pliego comercial simple tiene que puntar mas que el
   mismo sin ellas, y las senales buenas jamas borran un requisito critico.

3) TRAZABILIDAD. Cuando avisos pasan el filtro, `resumen["embudo"]` tiene
   que decir a donde se fueron. Y una reserva colgada -que es exactamente lo
   que producia el "Pasaron el filtro: 40 -> Analizados por IA: 0" en
   silencio- tiene que explicarse sola.

Doble modo: se puede correr con pytest o con `python test_fase10.py`.
No toca la red: SAM.gov y Gemini van falsos, igual que en test_embudo.py.
"""
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import date

_TMP = tempfile.mkdtemp(prefix="kyomoto_fase10_")
DB = os.path.join(_TMP, "licitaciones.db")
os.environ["DB_PATH"] = DB
os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("SAM_API_KEY", "SAM-de-prueba")
os.environ.setdefault("GEMINI_API_KEY", "x")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:FAKE")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
os.environ.setdefault("GEMINI_PAUSA_SEG", "0")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

import config  # noqa: E402
import friction_engine as fe  # noqa: E402
import quota  # noqa: E402
import scanner  # noqa: E402
import store  # noqa: E402

HOY = date(2026, 11, 26)

# ==========================================================================
# 1) REGLAS DURAS
# ==========================================================================

# Cada caso: (texto, id de la regla que tiene que disparar). Son frases de
# pliego de verdad, no cadenas de prueba: si una de estas no dispara en
# produccion tampoco lo hara un aviso real.
DURAS = [
    ("All wetted parts shall be SUBSAFE qualified per the manual.",
     "subsafe"),
    ("Level I inspection is required on 100 percent of the lot.",
     "nivel_i"),
    ("LI/SS requirements apply to this procurement.", "nivel_i"),
    ("DD Form 254 is attached and shall be complied with.", "dd254"),
    ("Classification: CONFIDENTIAL. Handle per the security plan.",
     "clasificado"),
    ("This solicitation is SECRET // NOFORN.", "noforn"),
    ("DSS-SOC program requirements are applicable.", "dss_soc"),
    ("TRIPER shall be satisfied for every item delivered.", "triper"),
    ("Scope includes Naval Nuclear Propulsion components.",
     "propulsion_nuclear"),
    ("A Facility Clearance is required to perform this contract.",
     "facility_clearance"),
    ("Per FAR 52.204-2, the offeror must hold a facility clearance.",
     "facility_clearance"),
]


@pytest.mark.parametrize("texto,regla", DURAS)
def test_las_clausulas_duras_salen_rechazadas(texto, regla):
    """Toda clausula bloqueante termina en AUTO_REJECT y sin RFQ."""
    d = fe.evaluar(texto, fe.Contexto(limite="2026-12-20", hoy=HOY))
    assert d.verdict == fe.AUTO_REJECT, (regla, d.verdict, d.score)
    assert d.score == 0, (regla, d.score)
    assert regla in d.matched_rules, (regla, d.matched_rules)
    assert d.rfq_habilitado is False
    assert d.criticas, "tenia que haber al menos una critica"


def test_el_contexto_de_clasificacion_evita_el_falso_positivo():
    """'confidential information' es boilerplate: eso NO es un aviso clasificado."""
    boilerplate = ("The contractor shall protect all confidential "
                   "information disclosed under this agreement and shall "
                   "not release it to third parties.")
    d = fe.evaluar(boilerplate, fe.Contexto(limite="2026-12-20", hoy=HOY))
    assert "clasificado" not in d.matched_rules, d.matched_rules
    assert d.verdict == fe.CANDIDATE_RFQ, (d.verdict, d.score, d.matched_rules)
    # En cambio, la misma palabra con lenguaje de clasificacion si cuenta.
    con_ctx = ("The contractor shall protect all confidential information. "
               "Classification: CONFIDENTIAL.")
    d2 = fe.evaluar(con_ctx, fe.Contexto(limite="2026-12-20", hoy=HOY))
    assert "clasificado" in d2.matched_rules, d2.matched_rules
    assert d2.verdict == fe.AUTO_REJECT


def test_dcma_y_qar_bloquean_el_rfq_sin_rechazar():
    """Inspeccion superior en origen: friccion alta, no descalificacion."""
    for texto in ("DCMA source inspection required prior to shipment.",
                  "A Government Quality Assurance Representative shall "
                  "witness testing at the plant."):
        d = fe.evaluar(texto, fe.Contexto(limite="2026-12-20", hoy=HOY))
        assert d.verdict == fe.HIGH_FRICTION_REVIEW, (texto, d.verdict)
        assert d.rfq_habilitado is False, texto
        assert d.altas, texto
        puede, motivo = fe.rfq_permitido({"diagnostico": d.a_dict()})
        assert puede is False and motivo


def test_una_alta_de_dcma_no_se_escapa_por_no_ser_critica():
    """El score se recorta por debajo del umbral: nunca contradice al veredicto."""
    d = fe.evaluar("DCMA inspection required. 10 EA.",
                   fe.Contexto(limite="2026-12-20", hoy=HOY))
    assert d.score < config.SCORE_CANDIDATO_MIN, d.score


# ==========================================================================
# 2) SENALES COTS: priorizar lo comercial simple
# ==========================================================================
def _ctx(**kw) -> fe.Contexto:
    base = dict(limite="2026-12-20", hoy=HOY, unidad="EA")
    base.update(kw)
    return fe.Contexto(**base)


def test_un_pliego_cots_punta_mas_que_el_mismo_sin_senales():
    """Mismo producto, con y sin las senales comerciales: el COTS gana."""
    plano = fe.evaluar(
        "Supply of standard brackets, 50 EA, delivery within 30 days. "
        "Buy American Act applies.", _ctx())
    cots = fe.evaluar(
        "COTS commercial item per FAR Part 12. Commercial Packaging. "
        "FOB Destination, inspection at destination. Ship direct. "
        "Supply of standard brackets, 50 EA, delivery within 30 days. "
        "Buy American Act applies.", _ctx())

    # Las dos son compras, pero la comercial simple queda claramente arriba.
    assert cots.score > plano.score, (cots.score, plano.score)
    assert "cots" in cots.matched_rules, cots.matched_rules
    assert "cots" not in plano.matched_rules
    assert cots.verdict == fe.CANDIDATE_RFQ
    assert plano.verdict == fe.CANDIDATE_RFQ


def test_el_bonus_cots_tiene_tope():
    """El bonus no puede borrar una penalizacion: sigue mandando el tope."""
    con_todo = fe.evaluar(
        "COTS commercial item per FAR Part 12. Commercial Packaging. "
        "FOB Destination, inspection at destination. Ship direct. "
        "Buy American Act applies.", _ctx())
    # Un solo positivo: tambien llega al tope, porque el techo es del bonus
    # acumulado y no por regla. Lo que comprueba es que NUNCA pasa de 100.
    uno = fe.evaluar(
        "COTS commercial item. Buy American Act applies.", _ctx())
    assert con_todo.score <= 100, con_todo.score
    assert uno.score <= 100, uno.score
    assert con_todo.score >= uno.score, (con_todo.score, uno.score)


def test_las_senales_buenas_no_borran_un_requisito_critico():
    """El caso clasico: encabezado comercial, pliego de defensa."""
    d = fe.evaluar(
        "COTS commercial item per FAR Part 12. Commercial Packaging. "
        "FOB Destination, inspection at destination. Ship direct. "
        "DD Form 254 attached. Classification: SECRET.",
        _ctx(agencia="Department of Veterans Affairs", monto=50000.0,
             margen_neto=30.0, historial_favorable=True,
             set_aside="Total Small Business"))
    assert d.verdict == fe.AUTO_REJECT, (d.verdict, d.score)
    assert d.score == 0, d.score
    assert d.rfq_habilitado is False


def test_el_set_aside_sigue_sumando_en_un_candidato_limpio():
    """La senal positiva mas importante del negocio sigue funcionando."""
    sin_sa = fe.evaluar(
        "COTS commercial item per FAR Part 12. Commercial Packaging. "
        "FOB Destination. 50 EA.", _ctx(agencia="USDA"))
    con_sa = fe.evaluar(
        "COTS commercial item per FAR Part 12. Commercial Packaging. "
        "FOB Destination. 50 EA.",
        _ctx(agencia="USDA", set_aside="Total Small Business"))
    assert con_sa.score >= sin_sa.score
    assert con_sa.verdict == fe.CANDIDATE_RFQ


# ==========================================================================
# 3) TRAZABILIDAD DEL EMBUDO
# ==========================================================================
def test_el_desglose_cuenta_lo_que_dice_y_se_lee():
    """Un contador que no se puede leer no es observabilidad."""
    scanner.limpiar_descarte()
    try:
        scanner.sumar("naics", 3)
        scanner.sumar("puntaje")
        scanner.sumar("complejidad", 2)
        texto = scanner.desglose_embudo()
        assert "veto NAICS x3" in texto, texto
        assert "bajo el piso de puntaje x1" in texto, texto
        assert "complejidad/riesgo alto x2" in texto, texto
        # Solo los tramos con algo: un x0 no explica nada.
        assert "fecha vencida" not in texto, texto
        assert texto.startswith("Sin analizar: "), texto
    finally:
        scanner.limpiar_descarte()

    scanner.limpiar_descarte()
    assert scanner.desglose_embudo() == ""


def test_las_etiquetas_cubren_todos_los_tramos():
    """Ningun tramo puede quedarse sin nombre legible en el mensaje."""
    claves = [k for k, _ in scanner.ETIQUETAS_EMBUDO]
    assert len(claves) == len(set(claves)), "tramo repetido en las etiquetas"
    assert all(k.strip() and e.strip() for k, e in scanner.ETIQUETAS_EMBUDO)
    # Los que este modulo llena de verdad al recorrer los avisos.
    declarados = set(claves)
    for tramo in ("ya_vistos", "naics", "fecha", "sin_titulo", "puntaje",
                  "complejidad", "valor", "duplicados", "sin_texto",
                  "reserva"):
        assert tramo in declarados, tramo


def test_sumar_no_puede_tumbar_el_barrido():
    """El embudo es observabilidad: si no puede contar, se queda corto."""
    scanner.limpiar_descarte()
    try:
        scanner.sumar("naics")            # entero normal
        scanner.sumar("naics", 2)
        scanner.sumar("naics", "x")       # basura: no suma y no revienta
        assert scanner.EMBUDO["naics"] == 3, scanner.EMBUDO
    finally:
        scanner.limpiar_descarte()


# ==========================================================================
# 4) INTEGRACION: escanear() con la red falsa
# ==========================================================================
@contextmanager
def _base_propia():
    """Corre un barrido sobre SU propia base y la deja como estaba.

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
        yield
    finally:
        config.DB_PATH = anterior


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


def _analizar(opp, desc, lugar, continuar=None, degradado=False):
    return {
        "notice_id": opp["noticeId"],
        "viable": True,
        "titulo": opp.get("title", ""),
        "title": opp.get("title", ""),
        "producto": "kit de repuestos",
        "margen_neto_porcentaje": 22.0,
        "motivo_descarte": "",
    }


def _barrer(dias=None, ptype="o,a", stats=None, indice_psc=False):
    """Fake de la red: 30 avisos buenos, sin tocar SAM.gov."""
    if isinstance(stats, dict):
        stats.update({"bloques_ok": 1, "bloques_error": 0, "errores": []})
    return (_aviso(i) for i in range(30))


@contextmanager
def _red_falsa():
    orig = (scanner.sam_api.barrer, scanner._bajar_descripciones,
            scanner.gemini_analyzer.analizar)
    scanner.sam_api.barrer = _barrer
    scanner._bajar_descripciones = _bajar
    scanner.gemini_analyzer.analizar = _analizar
    try:
        yield
    finally:
        (scanner.sam_api.barrer, scanner._bajar_descripciones,
         scanner.gemini_analyzer.analizar) = orig


def test_el_embudo_cuenta_cada_tramo_de_un_barrido():
    """El reporte tiene que poder leerse solo, sin cruzar dos diccionarios."""
    with _base_propia(), _red_falsa():
        res = scanner.escanear("1")

    emb = res.get("embudo") or {}
    # Los tres numeros principales estan, y son coherentes entre si.
    assert emb.get("traidas") == res["traidas"] == 30, emb
    assert emb.get("puntuales") == res["puntuales"], emb
    assert emb.get("analizadas") == res["analizadas"], emb
    assert res["puntuales"] > 0, "el filtro tenia que dejar pasar avisos"
    assert res["analizadas"] > 0, "un barrido normal analiza de verdad"
    # La linea en una sola linea, para la tarjeta de fin de barrido. Esta
    # VACIA cuando no se descarto nada, a proposito: un "veto NAICS x0" es
    # puro ruido, y con barridos cada 2 h son 12 lineas al dia de basura.
    assert "embudo_texto" in res, sorted(res)
    tramos = sum(emb.get(k, 0) for k in (
        "ya_vistos", "naics", "fecha", "sin_titulo", "puntaje",
        "complejidad", "valor", "duplicados", "sin_texto", "reserva"))
    texto = res.get("embudo_texto") or ""
    assert ("Sin analizar: " in texto) == bool(tramos), (tramos, texto)
    # Y la identidad del embudo: lo que se rechaza mas lo que pasa tiene que
    # dar lo traido. `sin_texto` NO entra: esos avisos SÍ estan en
    # `puntuades` (pasaron el puntaje sin descripcion), y contarlo otra vez
    # seria doble.
    rechazos = sum(emb.get(k, 0) for k in
                   ("ya_vistos", "naics", "fecha", "sin_titulo", "puntaje",
                    "complejidad", "valor", "duplicados"))
    assert rechazos + emb.get("puntuales", 0) == emb.get("traidas", 0), (
        rechazos, emb)


def test_una_reserva_colgada_no_produce_un_cero_silencioso():
    """
    EL CASO QUE SE REPORTO EN PRODUCCION.

    Barrido anterior: los mejores avisos se reservaron, Gemini fallo, y la
    reserva no se devolvio. Siguiente barrido: esos mismos avisos vuelven a
    pasar el filtro (siguen sin estar en licitaciones_procesadas),
    `quota.reservar()` los rechaza a todos, `futuros` queda vacio y
    `resumen["analizadas"]` daba 0 SIN NINGUNA LINEA QUE LO EXPLICARA.

    Ahora el cero se explica: sale en `errores` y en el desglose.
    """
    with _base_propia(), _red_falsa():
        # Asi quedaba el estado tras un barrido cuyo analisis fallo: la
        # reserva hecha, pero el aviso nunca marcado como procesado.
        for i in range(30):
            quota.reservar(f"nid{i:03d}")
        assert store.ids_procesados() == set()

        res = scanner.escanear("1")

    # El sintoma se reproduce...
    assert res["puntuales"] > 0, res
    assert res["analizadas"] == 0, res["analizadas"]
    # ...pero ya no es una caja negra.
    assert res["errores"], "el cero tenia que venir explicado"
    assert any("cup" in e and "reservado" in e for e in res["errores"]), \
        res["errores"]
    emb = res.get("embudo") or {}
    assert emb.get("reserva", 0) > 0, emb
    assert emb.get("puntuales", 0) == res["puntuales"]
    assert "cupos ya reservados hoy" in (res.get("embudo_texto") or "")


def test_liberar_la_reserva_devuelve_el_aviso_al_siguiente_barrido():
    """`liberar()` es la otra mitad del contrato que `reservar()` abre."""
    with _base_propia():
        assert quota.reservar("nid-liberar") is True
        assert quota.reservar("nid-liberar") is False, "mismo dia: ya conto"
        assert quota.liberar("nid-liberar") is True
        assert quota.reservar("nid-liberar") is True, \
            "liberado, tiene que volver a poder entrar"
        # Y un id que nunca existio: liberar no inventa filas.
        assert quota.liberar("nid-que-no-existe") is False


def test_una_reserva_de_ayer_no_bloquea_el_aviso_de_hoy():
    """
    `vista_quota` es `notice_id TEXT PRIMARY KEY`: sin `dia` en la clave,
    el viejo `INSERT OR IGNORE` ignoraba cualquier fila anterior, fuera del
    dia que fuera, y `limpiar_reservas()` solo corre al arrancar. Un proceso
    vivo cruzando la medianoche del Pacifico bloqueaba para siempre.
    """
    with _base_propia():
        assert quota.reservar("nid-ayer") is True
        # Se hace pasar por una reserva del dia anterior.
        with quota._conexion() as c:
            c.execute("UPDATE vista_quota SET dia = ? WHERE notice_id = ?",
                      ("2020-01-01", "nid-ayer"))
        assert quota.reservar("nid-ayer") is True, \
            "una reserva vieja no puede bloquear el de hoy"
        with quota._conexion() as c:
            dia = c.execute(
                "SELECT dia FROM vista_quota WHERE notice_id = ?",
                ("nid-ayer",)).fetchone()[0]
        assert dia == quota.hoy_pacifico(), dia


def test_el_mensaje_de_barrido_vacio_lleva_el_desglose():
    """La tarjeta de kyo.imprime el desglose solo cuando lo trae."""
    import kyo
    sin = {"traidas": 3413, "puntuales": 40, "analizadas": 0, "viables": 0}
    tarjeta_vieja = kyo.resumen_escaneo(sin)
    # Un resumen armado a mano (como los tests antiguos) no cambia ni una linea.
    assert "Sin analizar" not in tarjeta_vieja
    assert tarjeta_vieja.count("<b>") == tarjeta_vieja.count("</b>")

    con = dict(sin, embudo_texto="Sin analizar: cupos ya reservados hoy x4")
    tarjeta = kyo.resumen_escaneo(con)
    assert "Sin analizar: cupos ya reservados hoy x4" in tarjeta
    assert tarjeta.count("<b>") == tarjeta.count("</b>")
    # Y el orden se mantiene: el desglose va con los numeros, no al final.
    assert tarjeta.find("Analizados por IA") < tarjeta.find("Sin analizar")


if __name__ == "__main__":
    codigo = pytest.main(["-q", "--no-header", __file__])
    print()
    print("RESULTADO: " + ("el embudo se explica y las reglas duras "
                           "bloquean, en verde" if codigo == 0 else "FALLA"))
    sys.exit(codigo)
