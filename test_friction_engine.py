# -*- coding: utf-8 -*-
"""
test_friction_engine.py - El juicio de friccion, con reglas y sin modelo.

La Fase 9 existe porque un aviso que en el titulo parece una compra de
catalogo acaba pidiendo en el pliego SUBSAFE, inspeccion de Nivel I o
radiografia de soldadura. Son las licitaciones TRAMPA: requisitos de
CALIFICACION e INSPECTION que un intermediario sin planta propia no puede
satisfacer, y que solo se ven leyendo el texto entero.

Esta suite comprueba las cuatro cosas que hacen confiable a ese juicio:

  1. El CASO OBLIGATORIO: un pliego largo con SUBSAFE / Level I /
     MIL-STD-2073 / radiography / dissolve the small business set aside
     sale AUTO_REJECT y el RFQ queda bloqueado.
  2. Que las reglas son DATOS y no logica escondida: ids unicos,
     severidades del vocabulario, umbrales configurables.
  3. Que el motor no depende de red ni de Telegram: ni una import de
     sam_api, usaspending_client, requests ni telegram_notify.
  4. Que no rompe nada de lo que ya habia: un analisis SIN diagnostico
     sigue habilitando el RFQ, que es como estaban todas las fichas
     anteriores a la Fase 9.

Doble modo: se puede correr con pytest o con `python test_friction_engine.py`.
"""
import ast
import datetime as dt
import json
import os
import re
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kyomoto_friccion_")
os.environ["DB_PATH"] = os.path.join(_TMP, "licitaciones.db")
os.environ.setdefault("GEMINI_API_KEY", "x")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "123456789")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

import config  # noqa: E402
import friction_engine as fe  # noqa: E402
import pliego  # noqa: E402

HOY = dt.date(2026, 11, 16)


# --------------------------------------------------------------------------
# El pliego largo. Es LARGO de verdad (mas de 4.000 caracteres) porque el
# requisito es ese: el motor tiene que juzgar un pliego entero, no los
# 3.000 caracteres que ve el prompt.
# --------------------------------------------------------------------------
def _relleno(n=44) -> str:
    return "\n".join(
        "<p>4.%02d Records of shipment shall be maintained for %d days and "
        "made available to the Contracting Officer upon request.</p>"
        % (i, 365 + i) for i in range(1, n + 1)
    )


PLIEGO_LARGO = (
    "<html><head><style>p{margin:0}</style></head><body>"
    "<h1>SECTION 1.0  SCOPE</h1>"
    "<p>The Contractor shall furnish <b>100 EA</b> of rotary shaft seals, "
    "part number <b>N0010426QFF16</b>, conforming to <b>ASTM-B505</b>. "
    "Delivery FOB Destination, inspection at destination. FAR Part 12 "
    "applies. Commercial Packaging.</p>"
    "<h1>SECTION 2.0  ADMINISTRATIVE</h1>" + _relleno() +
    "<h1>SECTION 3.0  QUALITY</h1>"
    "<ul>"
    "<li>DI-MISC-80678 dimensional tolerances apply.</li>"
    "<li>SUBSAFE material is required for all wetted parts.</li>"
    "<li>Level I inspection shall be performed at the plant.</li>"
    "<li>Radiography of all butt welds shall be performed per MIL-STD-2073.</li>"
    "<li>Repair welding is not Permitted.</li>"
    "</ul>"
    "<p>The Contracting Officer will dissolve the small business set aside "
    "if no qualified small business responds by the due date.</p>"
    "<p>Delivery 30 days after award. Amendment 001 issued.</p>"
    "</body></html>"
)


def _ficha() -> dict:
    """Una ficha minima que formatear_analisis acepta sin quejarse."""
    return {
        "notice_id": "F9-TEST", "viable": True, "motivo_descarte": "",
        "is_degraded": False, "sin_descripcion": False,
        "title": "Baterias 12V", "producto": "Baterias",
        "modelo_especifico": "", "unidad_medida": "EA", "cantidad_total": 100,
        "especificacion_tecnica_clave": "NSN 6140-01-234-5678",
        "lugar_entrega": "Norfolk, VA", "nsn": "6140-01-234-5678",
        "numeros_parte": ["AB-12345"], "fuente_aprobada": "",
        "empaque": "", "entregas": ["Norfolk, VA"], "notas_descarga": "",
        "cumple": [], "precio_unitario_costo": 500.0,
        "precio_unitario_mercado": 700.0, "precio_unitario_oferta": 745.0,
        "ganancia_por_unidad": 245.0, "valor_contrato_usd": 74500.0,
        "costo_total_usd": 50000.0, "ganancia_total_usd": 24500.0,
        "margen_bruto_porcentaje": 32.9, "costo_factoring_usd": 2607.5,
        "ganancia_neta_usd": 21892.5, "margen_neto_porcentaje": 24.3,
        "precio_oferta_sugerido_usd": 74500.0, "escenarios_oferta": [],
        "precios_historicos": {"sin_datos": True},
        "facilidad_comercializacion": "COTS", "motivo_facilidad": "",
        "estrategia_oferta": "Ofertar a 74500 USD",
        "razonamiento_oferta": "Ofertar 12% bajo el presupuesto.",
        "busquedas_distribuidores": ["baterias"],
        "query_google_proveedores": "baterias",
        "distribuidores_candidatos": [
            {"nombre": "Grainger", "tipo": "mayorista", "porque": "stock",
             "verificar": "Grainger"},
            {"nombre": "McMaster", "tipo": "casa", "porque": "stock",
             "verificar": "McMaster"},
            {"nombre": "Radwell", "tipo": "mayorista MRO", "porque": "stock",
             "verificar": "Radwell"},
        ],
        "margen_por_distribuidor": "", "nivel_riesgo": "medio",
        "preguntas_criticas": [], "observaciones": "",
        "naics": "335911", "psc": "6135", "solicitation": "W560",
        "set_aside": "Total Small Business", "limite": "2026-12-20",
        "posted": "2026-11-01", "agencia": "Department of the Army",
        "contacto": "a@b.mil", "ui_link": "https://x", "anexos": [],
    }


# ==========================================================================
# 1) EL CASO OBLIGATORIO
# ==========================================================================
def test_pliego_largo_critico_se_rechaza_y_bloquea_el_rfq():
    """
    Un pliego largo con SUBSAFE / Level I / MIL-STD-2073 / radiography /
    dissolve the small business set aside tiene que salir de rechazo y
    con el RFQ bloqueado. Este es el caso que define la Fase 9.
    """
    # Es un pliego LARGO de verdad: juzgar 3.000 caracteres no seria nada.
    assert len(PLIEGO_LARGO) > 4000, len(PLIEGO_LARGO)

    texto = pliego.de_html_a_texto(PLIEGO_LARGO)
    assert len(texto) > 3000
    for token in ("SUBSAFE", "Level I", "MIL-STD-2073", "Radiography",
                  "dissolve the small business set aside"):
        assert token.lower() in texto.lower(), token

    d = fe.evaluar(texto, fe.Contexto(limite="2026-12-20", hoy=HOY,
                                      agencia="Department of the Army",
                                      set_aside="Total Small Business",
                                      unidad="EA", monto=74500.0))

    # Rechazo, con score a cero: no hay negociacion posible.
    assert d.verdict == fe.AUTO_REJECT, d.verdict
    assert d.score == 0, d.score
    assert d.rfq_habilitado is False

    # Las clausulas criticas estan nombradas, una a una.
    unido = " | ".join(d.criticas).lower()
    for token in ("subsafe", "nivel i", "radiografía", "set aside"):
        assert token in unido, (token, d.criticas)

    # Y el gate del RFQ lo bloquea en el analisis, que es donde se decide.
    analisis = {"diagnostico": d.a_dict()}
    puede, motivo = fe.rfq_permitido(analisis)
    assert puede is False
    assert "Rechazo automático" in motivo
    # La aprobacion explicita sigue siendo posible: es la salida del gate.
    assert fe.rfq_permitido(dict(analisis, rfq_aprobado_manual=True))[0] is True


def test_la_ficha_no_redacta_el_rfq_si_es_rechazo():
    import telegram_notify as tn
    d = fe.evaluar(pliego.de_html_a_texto(PLIEGO_LARGO),
                   fe.Contexto(limite="2026-12-20", hoy=HOY))
    h = tn.formatear_analisis(dict(_ficha(), diagnostico=d.a_dict()))

    # El RFQ sale en pausa y no listo para copiar.
    assert "RFQ Listo para Enviar" not in h
    assert "RFQ en pausa" in h

    # El diagnostico va PRIMERO, antes de las cifras, y sin <pre>.
    assert h.index("🩺") < h.index("💰 <b>Resumen financiero</b>")
    bloque_diag = h[h.index("🩺"):h.index("💰 <b>Resumen financiero</b>")]
    assert "<pre>" not in bloque_diag
    # Y sin " USD": la ficha cuenta cuatro y no se puede anadir un quinto.
    assert " USD" not in bloque_diag
    # Con los rotulos que pide el requisito.
    for rotulo in ("Veredicto", "Score", "Días al cierre", "Resumen rápido",
                   "Cláusulas críticas detectadas"):
        assert rotulo in bloque_diag, rotulo


def test_un_candidato_si_redacta_el_rfq():
    import telegram_notify as tn
    d = fe.evaluar("FAR Part 12 commercial item. Commercial Packaging. "
                   "FOB Destination, inspection at destination. 100 EA.",
                   fe.Contexto(limite="2026-12-20", hoy=HOY,
                               agencia="Department of Veterans Affairs",
                               unidad="EA", monto=74500.0,
                               margen_neto=24.3, historial_favorable=True))
    assert d.verdict == fe.CANDIDATE_RFQ, (d.verdict, d.score, d.matched_rules)
    h = tn.formatear_analisis(dict(_ficha(), diagnostico=d.a_dict()))
    assert "RFQ Listo para Enviar" in h
    # Las columnas no se mueven: el PRIMER <pre> sigue siendo la tabla
    # financiera y el de escenarios, el segundo.
    pres = re.findall(r"<pre>(.*?)</pre>", h, re.S)
    assert "Presupuesto govt." in pres[0]
    assert "Escenario" in pres[1]
    # El RFQ es el ultimo bloque y el enlace, el que va justo detras.
    assert "Subject: Wholesale RFQ" in pres[-1]
    assert h.find("🔗") < h.find("✉️")
    assert h.count(" USD") == 4, h.count(" USD")


# ==========================================================================
# 2) Las reglas son datos
# ==========================================================================
def test_las_reglas_son_estructuras_de_datos():
    assert all(isinstance(r, fe.Regla) for r in fe.REGLAS)
    severidades = {r.severidad for r in fe.REGLAS}
    assert severidades <= {fe.CRITICA, fe.ALTA, fe.MEDIA, fe.POSITIVA}
    ids = [r.id for r in fe.REGLAS]
    assert len(ids) == len(set(ids)), "ids duplicados: %s" % (
        sorted({i for i in ids if ids.count(i) > 1}))
    # Cada regla trae su etiqueta: una regla sin motivo no se puede discutir.
    assert all(r.etiqueta.strip() for r in fe.REGLAS)
    # Y los pesos son los declarados, no repartidos por el codigo.
    assert fe._PESOS[fe.CRITICA] == -100
    assert fe._PESOS[fe.ALTA] == -25
    assert fe._PESOS[fe.MEDIA] == -10
    assert fe._PESOS[fe.POSITIVA] == 8


def test_el_umbral_es_configurable(monkeypatch):
    texto = "Buy American Act applies to this procurement."
    assert fe.evaluar(texto, None).score == 90
    monkeypatch.setattr(config, "SCORE_CANDIDATO_MIN", 95)
    assert fe.evaluar(texto, None).verdict == fe.HIGH_FRICTION_REVIEW
    monkeypatch.setattr(config, "SCORE_CANDIDATO_MIN", 85)
    assert fe.evaluar(texto, None).verdict == fe.CANDIDATE_RFQ


def test_el_score_nunca_contradice_al_veredicto():
    """Si el veredicto no es candidato, el numero no puede decir 99."""
    d = fe.evaluar("Packaging per MIL-STD-2073. 10 EA.",
                   fe.Contexto(limite="2026-12-20", hoy=HOY))
    assert d.verdict == fe.HIGH_FRICTION_REVIEW
    assert d.score < config.SCORE_CANDIDATO_MIN, d.score
    assert fe.evaluar("", None).score == 0


def test_las_senales_no_borran_la_penalizacion_critica():
    """Senales buenas y un requisito critico no se compensan jamas."""
    d = fe.evaluar("FAR Part 12. Commercial Packaging. FOB Destination, "
                   "inspection at destination. SUBSAFE required.",
                   fe.Contexto(limite="2026-12-20", hoy=HOY,
                               agencia="Department of Veterans Affairs",
                               unidad="EA", monto=1000.0, margen_neto=30.0,
                               historial_favorable=True))
    assert d.verdict == fe.AUTO_REJECT
    assert d.score == 0


# ==========================================================================
# 3) Filtros y guardas
# ==========================================================================
def test_la_guarda_del_rt_evita_falsos_positivos():
    # "RT" en temperatura ambiente no es radiografia.
    assert fe.evaluar("Keep the carton at room temperature", None).verdict \
        == fe.CANDIDATE_RFQ
    # "RT" hablando de soldadura si lo es.
    assert fe.evaluar("RT of welds shall be performed per the procedure",
                      None).verdict == fe.AUTO_REJECT


def test_la_guarda_del_cac_evita_falsos_positivos():
    assert fe.evaluar("Vendor CAC listed in table 2", None).verdict \
        == fe.CANDIDATE_RFQ
    assert fe.evaluar("Access requires a CAC card to log in", None).verdict \
        == fe.HIGH_FRICTION_REVIEW


def test_una_alta_bloquea_el_rfq_sin_ser_rechazo():
    d = fe.evaluar("Packaging per MIL-STD-2073. 10 EA.",
                   fe.Contexto(limite="2026-12-20", hoy=HOY))
    assert d.verdict == fe.HIGH_FRICTION_REVIEW
    assert d.rfq_habilitado is False
    assert d.altas, "tenia que detectar la friccion alta"
    puede, motivo = fe.rfq_permitido({"diagnostico": d.a_dict()})
    assert puede is False and motivo


def test_sin_texto_no_se_habilita_el_rfq():
    d = fe.evaluar("", None)
    assert d.verdict == fe.HIGH_FRICTION_REVIEW
    assert d.score == 0
    assert d.rfq_habilitado is False
    assert "evaluar" in d.motivo_rfq
    assert any("Sin texto" in a for a in d.avisos)


# ==========================================================================
# 4) Avisos (no descalifican, pero se dicen)
# ==========================================================================
def test_avisos_de_cierre_pronto_y_monto_fuera_de_foco():
    d = fe.evaluar("Valvulas de acero fundido, 10 EA, entrega rapida.",
                   fe.Contexto(limite="2026-11-18", hoy=HOY, monto=400000.0))
    assert d.days_to_deadline == 2, d.days_to_deadline
    assert len(d.avisos) == 2, d.avisos
    assert any("2 días" in a for a in d.avisos)
    assert any("400,000" in a for a in d.avisos)
    # Es un aviso y no una descalificacion.
    assert d.verdict == fe.CANDIDATE_RFQ
    assert d.score < 100


def test_una_fecha_incomparable_no_reventa():
    d = fe.evaluar("texto", fe.Contexto(limite="N/A"))
    assert d.days_to_deadline is None
    d2 = fe.evaluar("texto", fe.Contexto(limite=None))
    assert d2.days_to_deadline is None


# ==========================================================================
# 5) Compatibilidad y serializacion
# ==========================================================================
def test_sin_diagnostico_el_rfq_no_se_toca():
    """Fichas anteriores a la Fase 9: no se juzga lo que no se pudo evaluar."""
    assert fe.rfq_permitido({})[0] is True
    assert fe.rfq_permitido({"diagnostico": {}})[0] is True
    assert fe.rfq_permitido({"diagnostico": {"verdict": ""}})[0] is True
    assert fe.rfq_permitido({"diagnostico": {"verdict": "COSA RARA"}})[0] is True
    assert fe.rfq_permitido(None)[0] is True


def test_la_aprobacion_explicita_manda_sobre_el_veredicto(monkeypatch):
    bloqueado = {"diagnostico": fe.evaluar("SUBSAFE required", None).a_dict()}
    assert fe.rfq_permitido(bloqueado)[0] is False
    assert fe.rfq_permitido(
        dict(bloqueado, rfq_aprobado_manual=True))[0] is True
    monkeypatch.setattr(config, "RFQ_APROBAR_TODO", True)
    assert fe.rfq_permitido(bloqueado)[0] is True


def test_el_diagnostico_es_json_seguro():
    d = fe.evaluar(pliego.de_html_a_texto(PLIEGO_LARGO),
                   fe.Contexto(limite="2026-12-20", hoy=HOY, monto=74500.0))
    cargado = fe.Diagnostico.desde_dict(
        json.loads(json.dumps(d.a_dict())))
    assert cargado is not None
    assert cargado.verdict == d.verdict
    assert cargado.score == d.score
    assert cargado.matched_rules == d.matched_rules
    assert cargado.criticas == d.criticas
    assert cargado.altas == d.altas
    assert cargado.viability_summary == d.viability_summary
    assert cargado.rfq_habilitado == d.rfq_habilitado
    # Y el resumen ejecutivo tiene los cinco puntos que pide el requisito.
    assert len(cargado.key_findings) == 5
    unido = " ".join(cargado.key_findings)
    for parte in ("Qué se pide", "Exigencia", "Plazos", "Enmiendas",
                  "Veredicto"):
        assert parte in unido, parte
    assert cargado.viability_summary


def test_evaluar_pegado_normaliza_y_juzga_igual():
    """La entrada interna para bloques pegados usa el MISMO motor."""
    assert fe.evaluar_pegado("<p>SUBSAFE material required</p>").verdict \
        == fe.AUTO_REJECT
    limpio = fe.evaluar_pegado(
        "FAR Part 12. FOB Destination. Commercial Packaging.")
    assert limpio.verdict == fe.CANDIDATE_RFQ
    assert fe.evaluar_pegado("").verdict == fe.HIGH_FRICTION_REVIEW


# ==========================================================================
# 6) Arquitectura: sin red, sin Telegram, sin modelo
# ==========================================================================
def test_el_motor_no_importa_red_ni_telegram_ni_analizador():
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "friction_engine.py")
    with open(ruta, encoding="utf-8") as f:
        arbol = ast.parse(f.read())
    importados = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            importados |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            importados.add(n.module.split(".")[0])
    prohibidos = {"sam_api", "usaspending_client", "telegram_notify",
                  "gemini_analyzer", "requests", "httpx", "scanner",
                  "complejidad", "pdf_fetcher", "adjuntos"}
    assert not (importados & prohibidos), importados & prohibidos
    # Los umbrales si son configurables, y de ahi viene todo lo demas.
    assert "config" in importados


def test_el_veredicto_es_un_contrato_de_tres_valores():
    assert fe.VEREDICTOS == (fe.AUTO_REJECT, fe.HIGH_FRICTION_REVIEW,
                             fe.CANDIDATE_RFQ)
    assert set(fe.ETIQUETAS) == set(fe.VEREDICTOS)
    # Los veredictos son los literales que pide el requisito.
    assert fe.AUTO_REJECT == "AUTO_REJECT"
    assert fe.HIGH_FRICTION_REVIEW == "HIGH_FRICTION_REVIEW"
    assert fe.CANDIDATE_RFQ == "CANDIDATE_RFQ"


if __name__ == "__main__":
    codigo = pytest.main(["-q", "--no-header", __file__])
    print()
    print("RESULTADO: " + ("el motor de friccion juzga, bloquea y avisa, "
                          "en verde"
                          if codigo == 0 else "FALLA - ver arriba"))
    sys.exit(int(codigo))
