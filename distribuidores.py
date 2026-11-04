"""
distribuidores.py - Enlaces para encontrar distribuidores en USA.

Por que no devolvemos URLs de producto inventadas: ninguna API disponible
aqui (SAM.gov no las da, Gemini no navega) puede consultar el catalogo real de
un distribuidor. Fabricar un enlace con aspecto correcto seria mandarte a una
pagina 404 en el momento de necesitarla.

Lo que si se puede hacer, y es util de verdad: construir enlaces de busqueda
que Google resuelve en el momento, y dirigirlos con el operador `site:` a los
directorios de distribuidores estadounidenses. Esos enlaces:

  - siempre funcionan (verificado con HTTP 200)
  - se actualizan solos: manana muestran el catalogo de manana
  - con site: restringen el resultado a ese distribuidor
  - devuelven paginas de producto reales, no la portada

Comprobado: los sitios de distribuidores bloquean peticiones automaticas con
403, asi que no se puede verificar uno por uno. Google si responde 200, por eso
es el vehiculo.

Fase 3: a los enlaces se les anadio ORDEN GEOGRAFICO. Un enlace es util, pero
un enlace al distribuidor que esta en la otra punta del pais lo es menos: de
donde sale el material decide el coste del flete FOB Destination y los dias
de transito. El modulo lee el Ship-To (ciudad, estado y region del Censo) y
con el reordena los enlaces por proximidad. Ordena, no filtra: nadie se
descarta por region, y sin destino el orden es el de siempre.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

# Directorios y distribuidores de USA. Todos operan en/desde Estados Unidos.
# Con site: el resultado se limita a ese dominio.
DIRECTORIOS = [
    ("ThomasNet", "thomasnet.com",
     "el mayor directorio industrial de USA, con proveedores verificados"),
    ("Supplyhouse", "supplyhouse.com",
     "mayorista e industrial, ex Industrial Associates"),
    ("Global Sources US", "globalsources.com",
     "marketplace mayorista con vendedores en USA"),
    ("Faire", "faire.com",
     "marketplace de mayoristas, bueno para consumibles"),
    ("Zoro", "zoro.com",
     "distribuidor con catalogo y envio desde USA"),
    ("Grainger", "grainger.com",
     "distribuidor industrial con stock y entrega rapida"),
    ("Wesco", "wesco.com",
     "distribuidor electrical e industrial"),
    ("Radwell", "radwell.com",
     "distribuidor de automatas y control industrial"),
    ("MRO Electric", "mroelectric.com",
     "componentes electricos y mantenimiento"),
    ("Platt", "platt.com",
     "suministros industriales y MRO"),
]

# Sitios donde buscar fabricante/distribuidor Norteamericano.
FABRICANTES = [
    ("Made-in-USA index", "made-in-usa.com"),
    ("USA Industry Search", "usaindustrysearch.com"),
]


# =====================================================================
# GEO-MATCHING: EL SHIP-TO ORDENA, NUNCA FILTRA
# =====================================================================
# De donde va el material salen el coste del flete FOB Destination y los dias
# de transito, asi que el orden de los distribuidores tiene que mirar el
# destino. Regla de este bloque, y no es negociable: ORDENAR SIEMPRE,
# DESCARTAR NUNCA. Un distribuidor de otra region sigue siendo valido (a veces
# es el unico que tiene el articulo); lo que no se puede es fingir que la
# region del Ship-To no importa.

# Los 50 estados + DC, en las dos formas que trae SAM.gov: codigo ("PA") y
# nombre ("Pennsylvania"). filters.lugar_de_entrega() publica el nombre y
# placeOfPerformance.state trae a veces el codigo, y distinguirlos a ciegas es
# lo que hace falta para no acabar inventando un estado que no esta.
_ESTADOS_LISTA = [
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
    ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"),
    ("DC", "District of Columbia"), ("DE", "Delaware"), ("FL", "Florida"),
    ("GA", "Georgia"), ("HI", "Hawaii"), ("IA", "Iowa"), ("ID", "Idaho"),
    ("IL", "Illinois"), ("IN", "Indiana"), ("KS", "Kansas"), ("KY", "Kentucky"),
    ("LA", "Louisiana"), ("MA", "Massachusetts"), ("MD", "Maryland"),
    ("ME", "Maine"), ("MI", "Michigan"), ("MN", "Minnesota"),
    ("MO", "Missouri"), ("MS", "Mississippi"), ("MT", "Montana"),
    ("NC", "North Carolina"), ("ND", "North Dakota"), ("NE", "Nebraska"),
    ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"),
    ("NV", "Nevada"), ("NY", "New York"), ("OH", "Ohio"), ("OK", "Oklahoma"),
    ("OR", "Oregon"), ("PA", "Pennsylvania"), ("RI", "Rhode Island"),
    ("SC", "South Carolina"), ("SD", "South Dakota"), ("TN", "Tennessee"),
    ("TX", "Texas"), ("UT", "Utah"), ("VA", "Virginia"),
    ("VT", "Vermont"), ("WA", "Washington"), ("WI", "Wisconsin"),
    ("WV", "West Virginia"), ("WY", "Wyoming"),
]

# Indice bidireccional, y ambos lados apuntan al CODIGO: es lo que enlaza con
# _CODIGO_REGION. El valor canonico es el codigo ("PA") porque es lo que
# enlaza con las regiones; el que se imprime siempre es el token tal cual
# llego, para no cambiar al usuario la forma que trajo el aviso.
_ESTADOS_USA = {c.lower(): c for c, _ in _ESTADOS_LISTA}
_ESTADOS_USA.update({n.lower(): c for c, n in _ESTADOS_LISTA})

# Region del Censo por codigo. Es la division oficial de4 (Northeast, Midwest,
# South, West): una clasificacion publica y estable, no una opinion nuestra.
_REGIONES_CENSO = {
    "Northeast": set("CT ME MA NH RI VT NJ NY PA".split()),
    "Midwest": set("IL IN MI OH WI IA KS MN MO NE ND SD".split()),
    "South": set("DE FL GA MD NC SC VA WV DC AL KY MS TN AR LA OK TX".split()),
    "West": set("AZ CO ID MT NV NM UT WY AK CA HI OR WA".split()),
}
_CODIGO_REGION = {c: r for r, cs in _REGIONES_CENSO.items() for c in cs}

# Lo que NO es un destino: el pais, en cualquier idioma, y los marcadores de
# "sin dato" que deja el analisis. Se quitan antes de mirar ciudad y estado
# para que un "Norfolk / VA / USA" no se lea como tres datos de entrega.
_NO_DESTINO = frozenset({
    "usa", "u.s.a.", "us", "u.s.", "united states",
    "united states of america", "estados unidos", "america",
    "no especificado", "not specified", "n/a", "na", "none", "tbd",
})

# El ZIP va pegado al estado en algunos avisos ("Illinois 61299"). Se quita
# aqui porque un "Illinois 61299" no es un estado y no entraria en el indice.
_RE_ZIP_PEGADO = re.compile(r"\s+\d{5}(?:-\d{4})?$")


def region_de_estado(estado: str) -> str:
    """La region del Censo del estado, o "" si no es un estado conocido."""
    if not estado:
        return ""
    canonico = _ESTADOS_USA.get(str(estado).strip().lower())
    if not canonico:
        return ""
    return _CODIGO_REGION.get(canonico.upper(), "")


def destino_de(lugar) -> dict:
    """
    El Ship-To como {ciudad, estado, region}, sin inventar nada.

    Acepta los dos formatos que llegan a este modulo:
        "Ciudad / Estado / PAIS"   el del barrido (filters.lugar_de_entrega)
        "Ciudad, Estado, PAIS"     el del aviso en vivo (desde_aviso)
    y tambien un dict de direccion de pdf_generator (claves ciudad/estado).

    Si no se reconoce un estado, no se inventa: la region queda vacia y el
    orden de los distribuidores se conserva el de siempre. Un "UNITED STATES"
    solo o un "No especificado" devuelven las tres claves vacias, que es la
    honesta: no hay destino, y el orden no puede depender de uno inexistente.
    """
    ciudad = estado = ""
    if isinstance(lugar, dict):
        ciudad = str(lugar.get("ciudad") or "").strip()
        estado = str(lugar.get("estado") or "").strip()
    else:
        texto = str(lugar or "").strip()
        if not texto or "{" in texto:
            return {"ciudad": "", "estado": "", "region": ""}
        trozos = []
        for trozo in re.split(r"[/,]", texto):
            t = _RE_ZIP_PEGADO.sub("", trozo).strip()
            if not t or t.lower().rstrip(".") in _NO_DESTINO:
                continue
            trozos.append(t)
        if len(trozos) >= 2:
            # Ciudad y estado, en ese orden (asi los escribe SAM.gov). Con mas
            # de dos se quedan los DOS ULTIMOS, que es donde estan: lo que va
            # delante suele ser el nombre de la instalacion
            # ("Naval Station Norfolk, Virginia").
            ciudad, estado = trozos[-2], trozos[-1]
        elif len(trozos) == 1:
            # Un solo dato: puede ser estado ("Illinois") o ciudad.
            if _ESTADOS_USA.get(trozos[0].lower()):
                estado = trozos[0]
            else:
                ciudad = trozos[0]
    ciudad = _RE_ZIP_PEGADO.sub("", ciudad).strip()
    estado = _RE_ZIP_PEGADO.sub("", estado).strip()
    if estado and not _ESTADOS_USA.get(estado.lower()):
        # Lo que parecia estado no lo es: no se fuerza. Un estado inventado
        # clasificaria el envio en la region equivocada.
        estado = ""
    return {"ciudad": ciudad, "estado": estado,
            "region": region_de_estado(estado)}


def proximidad(region_destino: str, region_base) -> int:
    """
    Que tan cerca esta un distribuidor del Ship-To. Tres rangos:
        1 = su base esta en la MISMA region que el destino (flete mas corto)
        2 = sin base conocida: conserva el orden que traia
        3 = base en otra region
    El 2 en medio no es un fallo: es lo honesto cuando no se sabe de donde
    envia, y ademas evita que un desconocido se hunda hasta el final solo
    por ser desconocido.
    """
    if not region_destino or not region_base:
        return 2
    return 1 if region_base == region_destino else 3


# Sede publica conocida de cada directorio de la lista, usada SOLO para
# ordenar. No afirma cobertura, ni disponibilidad, ni plazos: afirma de donde
# opera la empresa, que es lo que se puede comprobar. Los marketplaces y
# directorios puros no envian ellos: presentan proveedores de todo el pais,
# y por eso no tienen base.
_BASE_CONOCIDA = {
    "grainger.com": "Midwest",
    "zoro.com": "Midwest",
    "platt.com": "West",
    "wesco.com": "Northeast",
    "radwell.com": "Northeast",
    "mroelectric.com": "South",
}


def _region_de_candidato(nombre: str) -> str:
    """
    La region base de un candidato de Gemini, SOLO si su nombre coincide con
    un distribuidor de la lista. Un nombre que no esta ahi no se le atribuye
    base ninguna: no hay forma de saberlo, y inventarla seria exactamente lo
    que este modulo se niega a hacer.
    """
    bajo = str(nombre or "").lower()
    for dominio, region in _BASE_CONOCIDA.items():
        if dominio.split(".")[0] in bajo:
            return region
    return ""


# Palabras que piden informacion en vez de dar productos. Si el usuario
# busca "how to buy gate valve cheap" en lugar de "6 inch cast steel gate
# valve wholesale distributor", la pagina que sale es de tutoriales, no de
# proveedores.
# OJO: "wholesale" y "distributor" NO van aqui a proposito. Son las que
# hacen que los resultados sean de mayoristas, que es donde esta el margen.
_INSTRUCCION = re.compile(
    r"\b(how to|where to|can i|buy|cheap|cheapest|near me|precio|price|prices|"
    r"cost|costs|review|reviews|for sale|cheap near)\b",
    re.IGNORECASE,
)


def _limpiar(termino: str) -> str:
    """
    Deja solo lo que sirve para una busqueda.

    Quita comillas y demas signos raros, y comas por estetica. Lo que de
    verdad no puede quedar son las comillas dobles: rompen el HTML de la
    ficha y hacen que Telegram rechace el mensaje entero con un 400.
    """
    t = re.sub(r"[^\w\s\-/&+]", " ", str(termino or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:80]


def _sin_instruccion(termino: str) -> str:
    """Quita las palabras que piden informacion en vez de dar productos."""
    t = _INSTRUCCION.sub(" ", str(termino or ""))
    return re.sub(r"\s+", " ", t).strip()


def google(consulta: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(consulta)}"


def busqueda_sitio(dominio: str, termino: str) -> str:
    return google(f'{termino} site:{dominio}')


def _candidato(c: dict) -> dict | None:
    """
    Un distribuidor propuesto, con su enlace de verificacion.

    Devuelve None si no trae nombre: sin nombre no hay nada que buscar, y una
    ficha con un bullet vacio se ve peor que una ficha con dos.

    El enlace SIEMPRE se construye con el nombre, aunque el modelo haya
    mandado su propio campo "verificar". Motivo: una busqueda de Google
    alrededor del nombre comercial es la unica forma de confirmar que la
    empresa existe, que vende ese producto y cual es su contacto real. El
    texto que dio el modelo se usa, pero enriquecido con el nombre, para que
    la busqueda no se disperse.
    """
    # El nombre se limpia de etiquetas ANTES de usarse, por dos motivos:
    # Segundo, el nombre se imprime en la ficha, y la ficha lo escapa con
    # _esc() al imprimirse, pero si el HTML ya viaja en la URL no lo cubre el
    # escape de la pantalla. Se quita aqui una vez y no hay que acordarse en
    # cada sitio.
    nombre = re.sub(r"<[^>]+>", " ", str(c.get("nombre") or ""))
    nombre = re.sub(r"\s+", " ", nombre).strip()[:80]
    if len(nombre) < 2:
        return None

    # El nombre se pone entre comillas para que Google no lo descomponga, y se
    # le anade lo que el modelo dijo del tipo. Las comillas de Google son
    # justamente lo que _limpiar() prohibe en los terminos, asi que aqui se
    # ponen despues, nunca antes.
    #
    # OJO con la duplicacion: el modelo suele escribir el nombre dentro de su
    # propio campo "verificar" ("McMaster-Carr EPDM seal contact"). Si se
    # antepone el nombre otra vez, la busqueda queda
    #   "McMaster-Carr" McMaster-Carr EPDM seal contact
    # que no rompe, pero se ve como un error y gasta palabras clave. Se quita
    # el nombre del texto del modelo antes de anteponerlo.
    propio = str(c.get("verificar") or "").strip()
    if len(nombre) > 4:
        propio = re.sub(re.escape(nombre), " ", propio, flags=re.IGNORECASE)
    propio = re.sub(r"\s+", " ", propio).strip(" -,")
    if propio and len(propio) < 90:
        consulta = f'"{nombre}" {propio}'
    else:
        consulta = f'"{nombre}" distributor wholesale usa contact'
    return {
        "nombre": nombre,
        "tipo": str(c.get("tipo") or "").strip()[:80],
        "porque": str(c.get("porque") or "").strip()[:200],
        "url": google(consulta),
    }


def para_oportunidad(terminos: list[str], producto: str = "",
                     pais_destino: str = "",
                     query_google_proveedores: str = "",
                     candidatos: list[dict] | None = None) -> dict:
    """
    Arma el bloque de distribuidores para la ficha.

    query_google_proveedores es el termino optimizado que pidio Gemini. Si
    viene, el enlace principal lo usa tal cual, porque sale de la
    especificacion completa y no solo del nombre del producto. Si no viene,
    se arma la consulta como antes. En los dos casos hay enlace.

    Devuelve:
        {
          "principal": (etiqueta, url),          # busqueda general, siempre 200
          "candidatos": [...],                   # Gemini, max 3
          "sitios":    [(etiqueta, url, nota)],  # site: a directorios de USA
          "terminos":  [...],                    # para copiar y pegar
          "fabricantes": [...],
          "consultas": [...],
          "consulta_gemini": str,                # el termino que se uso, o ""

          # Fase 3 (geo-matching): las dos claves nuevas, sin mover ninguna
          # de las de arriba.
          "destino": {"ciudad", "estado", "region"},   # el Ship-To, o vacio
          "ordenado_por_cercania": bool,       # True solo si hay region
        }

    El orden de "sitios" y "candidatos" lo pone la region del Ship-To cuando
    la hay; si no la hay, es el de siempre, literal.
    """
    limpio = [_limpiar(t) for t in (terminos or []) if _limpiar(t)]
    producto_limpio = _limpiar(producto)
    # Si el producto sirve mejor que los terminos, se usa de base.
    base = producto_limpio or (limpio[0] if limpio else "industrial supplies")

    # El primer termino de Gemini suele ser el mas especifico: "marine fire
    # pump valves distributor usa" -> nos interesa el sustantivo, sin las
    # palabras de la instruction.
    especifico = limpio[0] if limpio else base
    especifico = re.sub(
        r"\b(distributor|supplier|wholesale|usa|united states|buy|price|cost)\b",
        "", especifico, flags=re.IGNORECASE,
    ).strip() or producto_limpio or base

    # El Ship-To, leido en sus dos formatos. De aqui sale el orden de abajo:
    # si hay region de destino, los mas cercanos suben; si no, no se ordena
    # nada y queda el de siempre.
    destino = destino_de(pais_destino)
    _sitio = ", ".join(x for x in (destino["ciudad"], destino["estado"]) if x)

    consultas = [f'"{especifico}" wholesale distributor usa',
                 f'{especifico} supplier "United States" price']
    if _sitio:
        # La ciudad y el estado juntos: con la ciudad sola Google devuelve
        # "distribuidores de Norfolk" y hay decenas de Norfolk.
        consultas.append(f'{especifico} distributor {_sitio}')

    # El termino de Gemini tiene prioridad sobre la consulta armada aqui.
    # Se limpia con el MISMO criterio que el resto, porque si no el enlace se
    # rompe: una coma o una comilla dentro del termino hacen que Google no
    # entienda la URL. Y el "price" se añade si no viene ya, para que el
    # usuario vea precios de mayoreo y no solo catalogos.
    consulta_gemini = _sin_instruccion(_limpiar(query_google_proveedores))
    if consulta_gemini:
        consulta_principal = consulta_gemini
        if not re.search(r"\b(price|precio|cost)\b", consulta_gemini, re.I):
            consulta_principal += " price"
    else:
        consulta_principal = f"{especifico} wholesale distributor usa price"

    principal = ("🔎 Buscar en Google (siempre funciona)",
                 google(consulta_principal))

    # Los diez se ordenan por proximidad y se quedan LOS CINCO PRIMEROS,
    # que es lo que cabe en la ficha. No es un corte arbitrario: con destino
    # desconocido todos empatan y salen los cinco de siempre (el orden previo
    # a la Fase 3, literal); con destino conocido, los que tienen base en la
    # region del Ship-To entran y los de otra region ceden su hueco. Es
    # aqui donde el geo-matching optimiza el flete FOB Destination: en la
    # lista que el usuario realmente abre.
    sitios_bruto = []
    for nombre, dominio, nota in DIRECTORIOS:
        consulta = f'"{especifico}" site:{dominio}' if " " in especifico else f"{especifico} site:{dominio}"
        sitios_bruto.append((proximidad(destino["region"],
                                        _BASE_CONOCIDA.get(dominio)),
                             nombre, google(consulta), nota))
    # Sort ESTABLE: con destino desconocido (o bases empatadas) el orden es
    # el de siempre. La region solo manda cuando HAY region; nunca mueve a
    # nadie hacia atras por ser de otra region, solo coloca a los de la
    # primera.
    sitios_bruto.sort(key=lambda x: x[0])
    sitios = [(n, u, nota) for _, n, u, nota in sitios_bruto[:5]]

    fabricantes = [
        (nombre, busqueda_sitio(dominio, f'"{especifico}" manufacturer usa'))
        for nombre, dominio in FABRICANTES
    ]

    # Los candidatos nombrados por Gemini, con su enlace de verificacion. Se
    # descartan los que no traigan nombre, y se limita a 3 porque son los que
    # caben en la ficha sin que deje de leerse de un vistazo.
    validos = []
    for c in (candidatos or []):
        limpio = _candidato(c) if isinstance(c, dict) else None
        if limpio:
            validos.append(limpio)

    # Clasificacion por cercania al Ship-To. Se marca la region base de
    # cada uno (vacia si no se conoce) y se ordena SOLO cuando hay region
    # de destino: sin destino, el orden es el que trajo Gemini, que es el
    # de antes de la Fase 3.
    for c in validos:
        c["region_base"] = _region_de_candidato(c["nombre"])
    if destino["region"]:
        validos.sort(key=lambda c: proximidad(destino["region"],
                                              c["region_base"] or None))

    return {
        "principal": principal,
        "candidatos": validos[:3],
        "sitios": sitios,
        "fabricantes": fabricantes,
        "terminos": limpio or [especifico],
        "consultas": consultas,
        # Se devuelve para que el log y las pruebas puedan ver si el termino
        # optimizado llego o no. Sin esto, un aviso con el campo vacio y otro
        # con el campo puesto darian un enlace identico y no se distinguirian.
        "consulta_gemini": consulta_gemini,
        # Fase 3: el Ship-To analizado y si el orden de arriba salio de la
        # region. La ficha usa los dos para decir de donde viene el orden,
        # y las pruebas para comprobarlo sin abrir el navegador.
        "destino": destino,
        "ordenado_por_cercania": bool(destino["region"]),
    }


def texto_plano(terminos: list[str]) -> str:
    """Los terminos como texto, para copiar y pegar a mano."""
    return " · ".join(_limpiar(t) for t in (terminos or []) if _limpiar(t))
