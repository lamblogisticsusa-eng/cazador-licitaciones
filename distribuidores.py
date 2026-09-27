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


def _limpiar(termino: str) -> str:
    """Deja solo lo que sirve para una busqueda."""
    t = re.sub(r"[^\w\s\-.,/&+]", " ", str(termino or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:80]


def google(consulta: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(consulta)}"


def busqueda_sitio(dominio: str, termino: str) -> str:
    return google(f'{termino} site:{dominio}')


def para_oportunidad(terminos: list[str], producto: str = "", pais_destino: str = "") -> dict:
    """
    Arma el bloque de distribuidores para la ficha.

    Devuelve:
        {
          "principal": (etiqueta, url),          # busqueda general, siempre 200
          "sitios":    [(etiqueta, url, nota)],  # site: a directorios de USA
          "terminos":  [...],                    # para copiar y pegar
          "fabricantes": [...],
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

    principal = ("🔎 Buscar en Google (siempre funciona)",
                 google(f'{especifico} wholesale distributor usa price'))

    sitios = []
    for nombre, dominio, nota in DIRECTORIOS[:5]:
        consulta = f'"{especifico}" site:{dominio}' if " " in especifico else f"{especifico} site:{dominio}"
        sitios.append((nombre, google(consulta), nota))

    fabricantes = [
        (nombre, busqueda_sitio(dominio, f'"{especifico}" manufacturer usa'))
        for nombre, dominio in FABRICANTES
    ]

    return {
        "principal": principal,
        "sitios": sitios,
        "fabricantes": fabricantes,
        "terminos": limpio or [especifico],
        "consultas": consultas,
    }


def texto_plano(terminos: list[str]) -> str:
    """Los terminos como texto, para copiar y pegar a mano."""
    return " · ".join(_limpiar(t) for t in (terminos or []) if _limpiar(t))
