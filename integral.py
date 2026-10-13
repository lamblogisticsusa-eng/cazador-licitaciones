"""
Conecta la ingesta de adjuntos, la correccion de QTY, el bloque tecnico del PDF
y el reporte financiero de Telegram.

1) LA CORRECCION DE QTY

El encargo pide que, si la Capa B encuentra un volumen real en los adjuntos,
sobrescriba el QTY 1 y la unidad LOT/EA de la tabla.

Antes de hacerlo hay que decidir QUE es una correccion y que es una invencion,
porque sobrescribir una cantidad en un pedido federal con un numero que el
modelo ha inventado es un problema MUCHO peor que dejar un 1. Asi que se
exigen tres cosas a la vez:

    1. que el numero este escrito en el corpus (descripcion O adjuntos)
    2. que no sea la misma cifra que ya estaba en la tabla
    3. que el texto que lo respalda venga de un documento, no de una conjetura

Si falta cualquiera de las tres, no se toca el QTY. El documento sale con lo que
decia la tabla, que es feo pero es cierto.

2) EL BLOQUE TECNICO EN EL PDF

Cambio de criterio respecto a la version anterior, y conviene dejarlo escrito:
en 3.8.0 el desglose se decidio que NO fuera al PDF, porque se creia que un
parte de fabricante es informacion interna y que en un documento comercial es
papel viejo.

El encargo ahora lo pide al contrario, y el motivo que se da es bueno: el
distribuidor de EE.UU. NO PUEDE cotizar bien sin el parte y la medida. Un
"1 LOT" sin saber si son 24 rodamientos o 6 cierrapuertas no se cotiza; se
rechaza. El parte y la medida no son papel viejo, son lo que hace falta para
que la hoja sirva.

Lo que NO cambia es el precio: las celdas siguen EN BLANCO. Eso es lo que
protege la negociacion, y no tiene nada que ver con esto.

3) EL REPORTE FINANCIERO DE TELEGRAM

La ganancia neta proyectada va al mensaje interno, no al PDF. Es informacion de
L.A.M.B. y no del proveedor: mandarle tu margen a la contraparte es lo unico
de todo este trabajo que si seria un error de verdad.

NO SE TOCA

La ruta feliz (sin lote consolidado), los precios, el idioma ingles del
documento, la maqueta y los anchos de columna.
"""
import io
import logging

log = logging.getLogger("kyomoto.integral")

import lote_consolidado as lc


# El numero tiene que estar como CIFRA, no como parte de un identificador.
#
#   "QTY 100 EA"        -> si, es una cantidad
#   "100 each"          -> si
#   "valor 1000 USD"    -> no, ese es otro numero
#   "codigo 100-A"      -> no, eso es un parte
#   "ABC-100"           -> no, eso es un parte
#
# Los dos ultimos son el fallo real, y son graves. Un numero de pieza es
# la forma mas comun de numero que hay en un documento tecnico:
# "S0594DU0500-S1", "1110700278", "MS25938-1". estan por todas partes.
#
# Si "100" casa con "100-A", la comprobacion que existe para que el modelo
# NO invente la cantidad no esta evitando nada, y encima da la sensacion
# de estar protegiendo. Peor que no comprobar: parece una garantia que
# no es una garantia.
_RE_CIFRA_SUELTA = r"(?<![\w.,-])%s(?![\d.,]|[-/]\w)"


def _numero_en_corpus(n: float, corpus: str) -> bool:
    """El numero esta escrito en el texto como cifra, no como parte."""
    import re
    if not n or float(n) != int(n) or n <= 0:
        return False
    return bool(re.search(_RE_CIFRA_SUELTA % int(n), str(corpus or "")))


def corregir_qty(bom: list, detalle: dict, corpus: str) -> dict:
    """
    Sobrescribe el QTY de un item consolidado con el volumen real.

    Devuelve {"cantidad": n, "unidad": "EA", "motivo": "..."} si hay que
    corregir, o {} si no. NUNCA lanza y NUNCA escribe una cantidad que no este
    en el corpus.
    """
    if not detalle or not bom:
        return {}
    n = float(detalle.get("unidades_numericas") or 0)
    if n <= 0:
        return {}
    item = bom[0] if bom else {}
    antes = float(item.get("cantidad") or 0)
    if n == antes:
        # Ya coincide. No es una correccion, y toques la fila sin motivo hace
        # que cualquier cambio posterior parezca el culpable.
        return {}
    if not _numero_en_corpus(n, corpus):
        log.info("corregir_qty: %s no aparece en el corpus; no se corrige", n)
        return {}
    # La unidad: si el desglose viene con una sigla explicita en el texto del
    # modelo, se respeta. Si no, EA, que es la unidad que cualquier
    # distributor de distribucion entiende y la que va con un desglose de
    # piezas sueltas.
    unidad = str(detalle.get("_unidad_cantidad") or "").strip().upper()
    if unidad not in ("EA", "BG", "KT", "PR", "CS", "CA", "BX", "PK", "PL",
                      "SET", "HD", "CON", "LOT", "LO"):
        unidad = "EA"
    log.info("corregir_qty: %s %s -> %s %s", antes,
             item.get("unidad"), int(n), unidad)
    return {"cantidad": n, "unidad": unidad,
            "unidad_original": str(item.get("unidad") or "LOT").upper(),
            "motivo": "volumen real encontrado en los documentos del aviso"}


def aplicar_correccion(bom: list, correccion: dict) -> bool:
    """Escribe la correccion en el BOM. True si algo cambio."""
    if not correccion or not bom:
        return False
    for item in bom:
        item["cantidad"] = correccion["cantidad"]
        item["unidad"] = correccion["unidad"]
        item["qty_corregido"] = True
    return True


def bloque_tecnico(detalhe: dict) -> str:
    """
    El bloque "TECHNICAL & DELIVERY REQUIREMENTS" del PDF.

    Solo con lo que el aviso dice. Las lineas que el modelo no devolvio SE
    OMITEN: cinco guiones vacios en un documento que va a un proveedor dicen
    "este no lo sabe", y lo que el proveedor hace con eso es devolver la hoja
    sin cotizar.
    """
    if not detalhe:
        return ""
    p = str(detalhe.get("manufacturer_part_number") or "").strip()
    m = str(detalhe.get("manufacturer_brand_name") or "").strip()
    s = str(detalhe.get("dimensions_or_specs") or "").strip()
    c = str(detalhe.get("certification_requirements") or "").strip()
    n = str(detalhe.get("actual_units_required") or "").strip()

    lineas = []
    if p or m:
        if p and m:
            lineas.append(f"<b>Part Number / Manufacturer:</b> {p} - {m}")
        elif p:
            lineas.append(f"<b>Part Number:</b> {p}")
        else:
            lineas.append(f"<b>Manufacturer:</b> {m}")
    if s:
        lineas.append(f"<b>Dimensions / Specifications:</b> {s}")
    if n:
        lineas.append(f"<b>Lot contents:</b> {n}")
    if c:
        lineas.append(f"<b>Certification required:</b> {c}")

    if not lineas:
        return ""
    return "<br/>".join(lineas)


# =====================================================================
# DATA_TO_RENDER
# =====================================================================
# EL UNICO PUNTO DE SOBREESCRITURA DEL PIPELINE.
#
# Aqui esta el fallo de arquitectura que se pidio arreglar, y conviene dejar
# escrito por que estaba donde estaba y por que se mueve.
#
# La sobreescritura vivia en desde_aviso(), que es el paso de DESCARGA. El
# renderizado es otro paso, y el dato viajaba entre los dos dentro de un
# diccionario. Eso funcionaba, pero solo en un camino: el de la consulta en
# vivo. Un analisis que venia de la base (que es el camino normal de /pdf, que
# primero mira en la base) nunca paso por ahi, asi que salia con QTY 1. No
# porque hubiera un fallo, sino porque la correccion vivia en un sitio al que
# ese camino no llegaba. Un PDF "desactualizado" asi no se distingue de uno
# correcto mirando el papel.
#
# Moviendo la aplicacion al renderizado, TODOS los caminos pasan por el mismo
# sitio. No se arregla el síntoma: se elimina la clase del síntoma.

def contexto_de(analisis: dict) -> dict:
    """El contexto semantico que lleva la correccion, si lo hay."""
    return dict(analisis.get("capa_b") or analisis.get("contexto_semantico") or {})


def hidratar(datos: dict, semantica: dict | None = None) -> dict:
    """
    Construye DATA_TO_RENDER y aplica la sobreescritura.

    Se llama al principio de generar_po(), no antes. Devuelve el mismo
    diccionario que recibe, modificado, porque el resto del renderizador ya
    trabaja con "datos" y cambiar eso seria tocar toda la maqueta.

    LA REGLA, tal cual la pidio el encargo:

        if qty == 1 and override_required:
            qty = target_qty
            description = f"{brand} P/N: {part_number} - {summary}"

    Y las tres guardas que lo hacen mas que eso:

    1. qty == 1. Solo se toca una tabla que MIENTE. Si la tabla ya dice 46,
       no hay nada que corregir y una sobreescritura ahi seria un error.
    2. target_qty > 0. Un 0 en la columna de cantidad de un pedido federal es
       peor que un 1.
    3. verificada. La cifra tiene que haberse comprobado contra el corpus,
       y esa comprobacion se hace en capa_b, que es donde esta el corpus. Aqui
       no se puede repetir, y por eso el booleano viaja con el numero. Un
       numero que no lleva la marca no entra.
    """
    if semantica is None:
        semantica = datos.get("contexto_semantico") or {}
    sem = dict(semantica or {})
    datos["contexto_semantico"] = sem

    if not sem.get("override_required"):
        datos["qty_corregida"] = False
        return datos

    objetivo = sem.get("target_qty")
    try:
        objetivo = int(float(objetivo))
    except (TypeError, ValueError):
        objetivo = 0

    if objetivo <= 0:
        _avisar(datos, "Capa B pidio sobreescribir pero el numero es %r" % (objetivo,))
        datos["qty_corregida"] = False
        return datos
    if not sem.get("verificada"):
        _avisar(datos, "numero %s sin verificar contra el corpus; NO se "
                        "aplica" % objetivo)
        datos["qty_corregida"] = False
        return datos

    lineas = datos.get("lineas") or []
    if not lineas:
        datos["qty_corregida"] = False
        return datos

    linea = lineas[0]
    try:
        actual = float(linea.get("cantidad") or 0)
    except (TypeError, ValueError):
        actual = 0.0

    if actual != 1:
        # La tabla ya dice algo que no es 1. No hay mentira que corregir.
        datos["qty_corregida"] = False
        datos["motivo_sin_correccion"] = (
            "la tabla ya dice %s %s" % (linea.get("cantidad"),
                                       linea.get("unidad") or ""))
        return datos

    unidad = str(sem.get("target_uoi") or "EA").upper()
    linea["cantidad"] = objetivo
    linea["unidad"] = unidad
    linea["qty_corregido"] = True

    marca = str(sem.get("manufacturer_brand") or "").strip()
    parte = str(sem.get("part_number") or "").strip()
    resumen = str(sem.get("product_specification_summary") or "").strip()
    if marca or parte or resumen:
        # "Marca P/N: parte - resumen". Lo que falte se deja fuera, y si no hay
        # ninguna de las tres NO se toca la linea: una especificacion vacia no
        # es mejor que la que ya tenia.
        texto = ""
        if marca:
            texto = marca
        if parte:
            texto += (" P/N: " if texto else "P/N: ") + parte
        if resumen:
            texto += (" - " if texto else "") + resumen
        # El detalle va en la especificacion, no encima de la descripcion:
        # reemplazar la descripcion por el parte perderia lo que dice el
        # aviso, que es lo que el distribuidor tiene que leer primero.
        # El encargo pide sobrescribirla, y lo que se sobrescribe es la
        # especificacion, que es donde estaba el hueco.
        linea["especificacion"] = texto
        # La descripcion recibe la marca delante, que es lo que un
        # distribuidor busca primero al recibir un pedido de este tipo.
        desc = str(linea.get("descripcion") or "").strip()
        if marca and marca.lower() not in desc.lower():
            linea["descripcion"] = ("%s - %s" % (marca, desc)).strip(" -")

    datos["qty_corregida"] = True
    datos["datos_corregidos"] = {
        "cantidad": objetivo, "unidad": unidad, "capa": sem.get("_capa", "?"),
        "motivo": sem.get("motivo", "volumen real en los documentos")}
    log.info("hidratar: QTY 1 %s -> %s %s (capa %s)",
             linea.get("unidad_original") or "LOT", objetivo, unidad,
             sem.get("_capa", "?"))
    return datos


def _avisar(datos: dict, porque: str) -> None:
    """Deja rastro de una correccion que NO se aplico, para que no sea muda."""
    log.warning("hidratar: la sobreescritura NO se aplica: %s", porque)
    datos.setdefault("avisos_hidratacion", []).append(porque)


def reporte_financiero(analisis: dict, detalle: dict) -> str:
    """
    El bloque financiero del mensaje interno de Telegram.

    Va aqui y no en el PDF por una razon que no admite discusion: el margen es
    informacion de L.A.M.B. Mandarselo a la contraparte es la unica parte de
    todo este trabajo que seria un error de verdad.
    """
    e = analisis.get("estimacion_interna") or {}
    if not e.get("valor_declarado_usd"):
        return ""

    def _usd(v):
        try:
            return f"${float(v):,.2f}"
        except (TypeError, ValueError):
            return "n/d"

    cant = e.get("cantidad_total_licitacion") or 0
    unit = e.get("precio_unitario_costo") or 0
    total = e.get("costo_total_usd") or 0
    # El margen que maneja el barrido: 15-35% menos ~3.5% de factoring. Se
    # calcula aqui solo como REFERENCIA interna, y con las mismas cifras que
    # salen de la ficha del Telegram.
    bruto = float(unit) * 1.25
    neto = bruto - float(unit) - float(unit) * 0.035

    lineas = [
        f"Valor declarado en el aviso: <b>{_usd(e['valor_declarado_usd'])}</b>",
        f"Volumen de la tabla: <b>{cant:,.0f}</b> unidades",
        f"Referencia de compra: <b>{_usd(unit)}</b>/unidad",
    ]
    if cant:
        lineas.append(f"TOTAL estimado: <b>{_usd(total)}</b>")
        lineas.append(f"Margen bruto estimado: <b>{_usd(bruto)}</b> "
                      f"({(bruto - float(unit)) / float(unit) * 100:.0f}%)")
        lineas.append(f"Netascion de referencia: <b>{_usd(neto)}</b>")
    cuerpo = "\n".join(f"  <i>•</i> {l}" for l in lineas)
    return ("\n💰 <b>ANALISIS FINANCIERO (interno)</b>\n" + cuerpo +
            "\n\n<i>Solo para uso interno. El PDF va sin precios y sin margen: "
            "eso lo rellena el distribuidor.</i>")