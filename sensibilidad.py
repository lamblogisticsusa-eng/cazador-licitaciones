"""
sensibilidad.py - Escenarios tacticos de oferta sobre el presupuesto.

POR QUE EXISTE
==============
Decidir a cuanto ofertar no es UNA cifra: son varias, y la que se elige
cambia lo que queda limpio al final. El techo del gobierno (100%), un 3% por
debajo (97%) y un 5% por debajo (95%) son tres apuestas distintas, y cada una
deja un margen distinto DESCONTADO el factoring, que es el coste que tiene
que usar el usuario porque no tiene capital propio.

Sin esta tabla habia que hacer la cuenta a mano en el momento de decidir, con
el aviso abierto y el reloj corriendo. Aqui sale calculada, con la misma
regla en los tres renglones.

LAS TRES REGLAS DE LA CUENTA (y ninguna es estetica)
=====================================================
1. El precio de cada escenario es el presupuesto multiplicado por el
   porcentaje. Es el techo con el que se juega, no un numero inventado.

2. El factoring se descuenta del PRECIO DE CADA ESCENARIO, no del
   presupuesto. config.FACTORING_PCT (3.5% por defecto, configurable por
   entorno FACTORING_PCT) se lee en el momento de calcular: si el operador
   cambia el tipo, la tabla cambia con el, por eso se le llama dinamico.

3. El margen se mide contra el PRECIO OFERTADO, nunca contra el
   presupuesto. Medirlo contra el presupuesto infla el margen y lleva a
   ofertar por debajo de tu propia ganancia: lo que entra a la cuenta es lo
   que se oferto, y esa es la division que importa.

Degradacion: si faltan los datos (presupuesto o costo), se devuelve una
lista VACIA y quien la pinta no pinta nada. Una tabla con huecos en una
ficha de decision peor que no tener tabla.
"""
from __future__ import annotations

import config

# Los tres escenarios tacticos: techo, -3% y -5%.
PORCENTAJES = (1.00, 0.97, 0.95)


def escenarios(presupuesto, costo_total,
               porcentajes=PORCENTAJES, factoring_pct=None) -> list[dict]:
    """
    Los escenarios de oferta, ya con el factoring descontado.

    Devuelve, por cada porcentaje, {"pct", "precio", "factoring_usd",
    "ganancia_neta", "margen"}. La ganancia puede salir negativa (escenario
    por debajo del costo): eso tambien es informacion, y ocultarla seria
    presentar una tabla que solo muestra lo bueno.

    Devuelve [] si no hay datos suficientes para calcular.
    """
    try:
        presupuesto = float(presupuesto)
        costo_total = float(costo_total)
    except (TypeError, ValueError):
        return []
    if presupuesto <= 0 or costo_total < 0:
        return []
    if factoring_pct is None:
        # Se lee aqui y no al importar: el tipo puede venir del entorno y
        # cambiar sin recargar el modulo.
        factoring_pct = config.FACTORING_PCT

    salida = []
    for pct in porcentajes:
        precio = presupuesto * float(pct)
        if precio <= 0:
            continue
        factoring = precio * factoring_pct
        ganancia = precio - costo_total - factoring
        salida.append({
            "pct": round(float(pct) * 100, 2),
            "precio": precio,
            "factoring_usd": factoring,
            "ganancia_neta": ganancia,
            # Contra el precio ofertado, no contra el presupuesto.
            "margen": (ganancia / precio) * 100,
        })
    return salida
