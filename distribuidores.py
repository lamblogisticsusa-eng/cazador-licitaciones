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
          "sitios":    [(etiqueta, url, nota)],  # site: a directorios de USA
          "terminos":  [...],                    # para copiar y pegar
          "fabricantes": [...],
          "consulta_gemini": str,                # el termino que se uso, o ""
        }
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

    ciudad = ""
    if pais_destino and "/" in pais_destino:
        partes = [p.strip() for p in pais_destino.split("/") if p.strip()]
        if len(partes) >= 2 and partes[-1].upper() in ("USA", "US", "UNITED STATES"):
            ciudad = partes[1]
        elif len(partes) >= 1:
            ciudad = partes[0]

    consultas = [f'"{especifico}" wholesale distributor usa',
                 f'{especifico} supplier "United States" price']
    if ciudad:
        consultas.append(f'{especifico} distributor {ciudad}')

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

    sitios = []
    for nombre, dominio, nota in DIRECTORIOS[:5]:
        consulta = f'"{especifico}" site:{dominio}' if " " in especifico else f"{especifico} site:{dominio}"
        sitios.append((nombre, google(consulta), nota))

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
    }


def texto_plano(terminos: list[str]) -> str:
    """Los terminos como texto, para copiar y pegar a mano."""
    return " · ".join(_limpiar(t) for t in (terminos or []) if _limpiar(t))
