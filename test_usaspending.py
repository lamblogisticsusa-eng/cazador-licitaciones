# -*- coding: utf-8 -*-
"""
test_usaspending.py - Fase 8: inteligencia de precios historicos.

Todo corre SIN RED. La API de USAspending se sustituye en la funcion de
transporte (`usaspending_client._post`), igual que en test_pdf_fetcher se
sustituye `_pedir`: una suite que depende de que un servicio del gobierno
conteste no es una suite, es una espera.

Lo que se comprueba, en orden:

 1) EL CRITERIO. El NSN se convierte en su Federal Supply Class, que es
    lo unico que esta base admite. Aqui vive la decision mas importante
    de la fase: buscar por NSN directo no funciona (medido) y buscar por
    "psc" en vez de "psc_codes" devuelve otra clase en silencio.
 2) LA FORMA. El dict vacio tiene todas las claves y los montos en None,
    nunca en 0.
 3) LAS RESPUESTAS. Cada endpoint, con fixtures que replican la forma
    real que devolvio la API el 13-nov-2026.
 4) LAS TRAMPAS. Que NINGUNA peticion lleve la clave "psc", que todas
    lleven award_type_codes y time_period, y que sean exactamente cinco.
 5) LOS FALLOS. Timeout, 500, JSON roto, httpx ausente: todo devuelve el
    dict vacio y NADA lanza.
 6) LA CACHE. Se pregunta una vez por clase; el acierto y el fracaso se
    guardan con la misma TTL.
 7) EL PROMPT. La seccion entra, y con ella la prohibicion de copiar un
    monto de contrato a precio_unitario_mercado.
 8) EL ANALISIS. analizar() la trae de vuelta y no cambia de firma.
 9) LA FICHA. El bloque se pinta, alineado, rotulado como lo que es, y
    no se pinta cuando no hay datos.
10) COMPATIBILIDAD. La firma del spec, requirements.txt y las firmas
    publicas que ya estaban.
"""
import inspect
import os
import sys
import tempfile

# --------------------------------------------------------------- entorno
DB = os.path.join(tempfile.gettempdir(), "kyo_test_usaspending.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + _s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "x"
os.environ["TELEGRAM_BOT_TOKEN"] = "x"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"
os.environ["DRY_RUN"] = "1"
# Este archivo ES el que prueba la consulta, asi que la vuelve a encender:
# run_suite la apaga para toda la suite como red de seguridad, y una
# asignacion aqui (no setdefault) la gana.
os.environ["PRECIOS_HISTORICOS"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import store  # noqa: E402
import usaspending_client as us  # noqa: E402
import gemini_analyzer as ga  # noqa: E402
import telegram_notify as tn  # noqa: E402

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


# ------------------------------------------------------ fixtures reales
# Formas medidas contra la API publica el 13-nov-2026. Si un dia cambian,
# este es el archivo que hay que mirar primero: los nombres de campo de
# aqui salieron de leer las respuestas, no de la documentacion.

R_CUENTA = {
    "results": {"contracts": 11573, "direct_payments": 0, "grants": 0,
                "idvs": 0, "loans": 0, "other": 0},
    "spending_level": "awards",
    "messages": [],
}

R_CLASE = {
    "category": "psc",
    "spending_level": "transactions",
    "limit": 5,
    "page_metadata": {"page": 1, "next": None, "previous": None,
                      "hasNext": False, "hasPrevious": False},
    "results": [{"id": None, "code": "1650",
                 "name": "AIRCRAFT HYDRAULIC, VACUUM, AND DE-ICING "
                         "SYSTEM COMPONENTS",
                 "amount": 1479603063.54, "total_outlays": None}],
    "messages": [],
}


def _fila(nid, nombre, monto, fecha):
    return {"internal_id": 1, "Award ID": nid, "Recipient Name": nombre,
            "Award Amount": monto,
            "Awarding Agency": "Department of Defense",
            "Description": "IGF::OT::IGF", "Start Date": "2024-01-01",
            "End Date": "2027-12-31", "Base Obligation Date": fecha,
            "Last Modified Date": fecha, "psc_code": "1650",
            "psc_description": "Aircraft Hydraulic, Vacuum, and De-Icing "
                               "System Components",
            "Recipient UEI": "NEXGNXGF5E38",
            "agency_slug": "department-of-defense"}


# Los diez mayores, ya ordenados de mayor a menor: asi sale la API.
R_MAYOR = {"results": [
    _fila("N0001920F0256", "LOCKHEED MARTIN CORP", 144286209.68,
          "2026-06-16"),
    _fila("SPRHA123F0100", "EATON CORPORATION", 74265936.96, "2026-03-24"),
    _fila("SPRHA124F0072", "STRYTEN ENERGY LLC", 63974255.38, "2025-07-28"),
    _fila("SPRHA124F0073", "LOCKHEED MARTIN CORP", 51000000.00,
          "2025-07-28"),
    _fila("SPRHA124F0074", "EATON CORPORATION", 41000000.00, "2025-07-28"),
], "limit": 10, "page_metadata": {"page": 1, "hasNext": True},
    "spending_level": "awards", "messages": []}

R_MENOR = {"results": [
    _fila("SPRTA126F1476", "PARKER HANNIFIN CORP", 9476.0, "2026-03-10"),
], "limit": 1, "page_metadata": {"page": 1, "hasNext": False},
    "spending_level": "awards", "messages": []}

# La muestra reciente: 100 premios con montos en torno a 151.500, que
# quedan claramente por encima de la media de la ventana (127.849) y por
# tanto dan una tendencia ALZA determinista.
R_FECHA = {"results": [
    _fila("SPRTA126F%04d" % i,
          ["EATON CORPORATION", "LOCKHEED MARTIN CORP", "STRYTEN ENERGY LLC",
           "PARKER HANNIFIN CORP"][i % 4],
          151000.0 + i * 10, "2026-11-%02d" % (i % 28 + 1))
    for i in range(100)
], "limit": 100, "page_metadata": {"page": 1, "hasNext": False},
    "spending_level": "awards", "messages": []}

R_VACIA = {"results": [], "limit": 1,
           "page_metadata": {"page": 1, "hasNext": False},
           "spending_level": "awards", "messages": []}

R_CUENTA_CERO = {"results": {"contracts": 0}, "spending_level": "awards",
                 "messages": []}
R_CLASE_CERO = {"category": "psc", "results": [], "messages": []}

RUTA_CUENTA = "/api/v2/search/spending_by_award_count/"
RUTA_CLASE = "/api/v2/search/spending_by_category/psc/"
RUTA_PREMIOS = "/api/v2/search/spending_by_award/"

# Lo que contesta el transporte por defecto. Se cambia entero o por
# pieza: los tests de fallo vacian esto, los de datos lo rellenan.
SERVICIOS = {"cuenta": R_CUENTA, "clase": R_CLASE, "mayor": R_MAYOR,
             "menor": R_MENOR, "fecha": R_FECHA}
PETICIONES = []
MODO = {"estado": "ok"}   # ok | vacio | error | lanza


async def _post_falso(ruta, cuerpo):
    """El transporte. Cada peticion queda registrada para poder mirarla."""
    PETICIONES.append((ruta, cuerpo))
    if MODO["estado"] == "lanza":
        raise RuntimeError("sin red en los tests")
    if MODO["estado"] in ("error", "vacio"):
        return {}
    if ruta == RUTA_CUENTA:
        return SERVICIOS["cuenta"]
    if ruta == RUTA_CLASE:
        return SERVICIOS["clase"]
    # Entre los premios, el desorden es lo que decide a cual de las tres
    # variantes de esta misma ruta se esta preguntando: no hay forma de
    # distinguirlas por la URL.
    if cuerpo.get("sort") == "Base Obligation Date":
        return SERVICIOS["fecha"]
    if cuerpo.get("order") == "asc":
        return SERVICIOS["menor"]
    return SERVICIOS["mayor"]


_POST_REAL = us._post
us._post = _post_falso


def _limpiar_cache():
    try:
        store.init_db()
        with store._lock, store._conexion() as c:
            c.execute("DELETE FROM cache_estructura "
                      "WHERE notice_id LIKE 'precios:%'")
    except Exception:
        pass


def _consulta(nsn="", psc="", limpiar=True):
    if limpiar:
        _limpiar_cache()
    PETICIONES.clear()
    return us.correr(lambda: us.consultar_precios_historicos(nsn, psc))


print("=" * 76)
print("1) EL CRITERIO: de un NSN a la clase con la que se puede buscar")
print("=" * 76)
check("Los 4 primeros digitos del NSN son su FSC",
      us.fsc_de_nsn("1650-01-672-3405") == "1650")
check("Un NSN sin guiones tambien se lee",
      us.fsc_de_nsn("1650016723405") == "1650")
check("13 digitos que no son un NSN no dan clase",
      us.fsc_de_nsn("12345678901234") == "")
check("Un texto largo no se hace pasar por NSN",
      us.fsc_de_nsn("Lote de 12 piezas") == "")
check("El NSN manda sobre el PSC del aviso",
      us.criterio("1650-01-672-3405", "6135")[1] == "1650")
check("Y se dice de donde sale el codigo",
      "clase del NSN" in us.criterio("1650-01-672-3405", "6135")[2])
check("Sin NSN se usa el del aviso",
      us.criterio("", "6135")[1] == "6135"
      and "del aviso" in us.criterio("", "6135")[2])
check("Sin ninguno no se busca en todo",
      us.criterio("", "") == ("", "", ""))
check("Un PSC con basura se limpia",
      us.limpiar_psc(" 61 35! ") == "6135")
check("Un PSC que no es un codigo se rechaza",
      us.limpiar_psc("una frase larga cualquiera") == "")
check("La ventana tiene las dos fechas en ISO",
      len(us.ventana(36)[0]) == 10 and len(us.ventana(36)[1]) == 10)
_d, _h = us.ventana(36)
check("La ventana va de atras hacia adelante", _d < _h, f"-> {_d} {_h}")

print()
print("=" * 76)
print("2) LA FORMA: el dict vacio tiene todas las claves y nada en cero")
print("=" * 76)
_v = us._vacio(motivo="prueba")
check("Marca sin_datos", _v["sin_datos"] is True)
check("Conserva el motivo", _v["motivo"] == "prueba")
check("Los montos van en None, no en 0",
      _v["monto_medio_usd"] is None and _v["monto_min_usd"] is None
      and _v["monto_max_usd"] is None)
check("Cero dolares adjudicados seria un dato falso",
      _v["premios"] == 0)
_claves = {"nsn", "psc", "criterio", "psc_nombre", "ventana_meses",
           "desde", "hasta", "premios", "monto_total_usd",
           "monto_medio_usd", "monto_min_usd", "monto_max_usd",
           "ultimo", "contratistas", "muestra", "tendencia",
           "ultimo_vs_media_pct", "fuente", "sin_datos", "motivo"}
check("La forma es completa", _claves <= set(_v),
      f"-> faltan {_claves - set(_v)}")
check("Dice de donde viene", _v["fuente"] == "USAspending.gov")

print()
print("=" * 76)
print("3) LAS RESPUESTAS: cinco peticiones, una sola media")
print("=" * 76)
datos = _consulta(nsn="1650-01-672-3405")
check("Trae el numero de premios", datos["premios"] == 11573,
      f"-> {datos['premios']}")
check("Trae el total de obligaciones",
      datos["monto_total_usd"] == 1479603063.54,
      f"-> {datos['monto_total_usd']}")
check("La media es total entre premios",
      abs(datos["monto_medio_usd"] - 1479603063.54 / 11573) < 0.01,
      f"-> {datos['monto_medio_usd']}")
check("El maximo es el mayor de la lista",
      datos["monto_max_usd"] == 144286209.68,
      f"-> {datos['monto_max_usd']}")
check("El minimo sale de la consulta ascendente",
      datos["monto_min_usd"] == 9476.0, f"-> {datos['monto_min_usd']}")
check("El criterio se dice en el propio dict",
      datos["criterio"] == "FSC 1650 (clase del NSN)")
check("El nombre de la clase viene del agrupado",
      datos["psc_nombre"].startswith("AIRCRAFT HYDRAULIC"))
check("La ventana queda registrada",
      datos["ventana_meses"] == 36 and datos["desde"] < datos["hasta"])
check("No se marca sin_datos", datos["sin_datos"] is False)
print(f"  media {datos['monto_medio_usd']:,.2f}  "
      f"rango {datos['monto_min_usd']:,.0f} - {datos['monto_max_usd']:,.0f}")

_nombres = [c["nombre"] for c in datos["contratistas"]]
check("Los contratistas son unicos", len(_nombres) == len(set(_nombres)),
      f"-> {_nombres}")
check("Salen los que mas se llevan",
      _nombres[:3] == ["LOCKHEED MARTIN CORP", "EATON CORPORATION",
                       "STRYTEN ENERGY LLC"], f"-> {_nombres}")
check("Y ninguno viene inventado", all(_nombres))

check("El ultimo premio sale de la consulta por fecha",
      datos["ultimo"].get("premio", "").startswith("SPRTA126F"),
      f"-> {datos['ultimo']}")
check("El ultimo premio trae monto, fecha y contratista",
      datos["ultimo"].get("monto_usd") is not None
      and len(str(datos["ultimo"].get("fecha"))) == 10
      and bool(datos["ultimo"].get("contratista")))
check("La muestra tiene 100 premios", datos["muestra"] == 100,
      f"-> {datos['muestra']}")
check("La tendencia es ALZA con la muestra por encima de la media",
      datos["tendencia"] == "ALZA", f"-> {datos['tendencia']}")
check("Y el porcentaje se calcula, no se pinta",
      datos["ultimo_vs_media_pct"] is not None
      and datos["ultimo_vs_media_pct"] > 10,
      f"-> {datos['ultimo_vs_media_pct']}")

print()
print("=" * 76)
print("4) LAS TRAMPAS DE LA API REAL")
print("=" * 76)
# La clave "psc" se IGNORA en silencio: medido el 13-nov-2026, con
# psc=['6135'] la API devolvio 200 y premios con psc_code Q201, M181 y
# M1JZ. Si alguna vez se colara, la ficha ensenaria cifras de otra clase.
check("Se hicieron exactamente cinco peticiones",
      len(PETICIONES) == 5, f"-> {len(PETICIONES)}")
# El filtro viaja SIEMPRE anidado en "filters": es la forma real de los
# tres endpoints. Mirar en la raiz del cuerpo daria None en las cinco y
# haria pasar un check vacio por uno bueno.
_cuerpos = [c for _r, c in PETICIONES]
check("Ninguna lleva la clave 'psc' (se ignora en silencio)",
      all("psc" not in c["filters"] for c in _cuerpos))
check("Todas llevan 'psc_codes' con la clase",
      all(c["filters"].get("psc_codes") == ["1650"] for c in _cuerpos),
      f"-> {[c['filters'].get('psc_codes') for c in _cuerpos]}")
check("Todas limitan el tipo a contratos (A, B, C, D)",
      all(c["filters"].get("award_type_codes") == ["A", "B", "C", "D"]
          for c in _cuerpos))
check("Todas llevan ventana de fechas",
      all(isinstance(c["filters"].get("time_period"), list)
          and c["filters"]["time_period"][0].get("start_date")
          and c["filters"]["time_period"][0].get("end_date")
          for c in _cuerpos))
check("Y todos los filtros van bajo 'filters', no en la raiz",
      all("filters" in c for c in _cuerpos))
_rutas = [r for r, _c in PETICIONES]
check("Se usan los tres endpoints del spec",
      _rutas.count(RUTA_CUENTA) == 1 and _rutas.count(RUTA_CLASE) == 1
      and _rutas.count(RUTA_PREMIOS) == 3, f"-> {_rutas}")
check("El maximo es descendente y el minimo ascendente",
      any(c.get("sort") == "Award Amount" and c.get("order") == "desc"
          for _r, c in PETICIONES)
      and any(c.get("sort") == "Award Amount" and c.get("order") == "asc"
              for _r, c in PETICIONES))
check("La recencia se ordena por Base Obligation Date, nunca por "
      "Award Date (siempre null)",
      any(c.get("sort") == "Base Obligation Date"
          for _r, c in PETICIONES)
      and all(c.get("sort") != "Award Date" for _r, c in PETICIONES))
check("La base sale de config y se puede mover por entorno",
      us._url("/api/v2/search/").startswith(config.USASPENDING_URL))

print()
print("=" * 76)
print("5) LOS FALLOS: nada de esto puede tumbar un analisis")
print("=" * 76)
for modo, etiqueta in (("error", "la API devuelve {}"),
                       ("lanza", "el transporte revienta"),
                       ("vacio", "la API no trae resultados")):
    MODO["estado"] = modo
    try:
        r = _consulta(nsn="1650-01-672-3405")
        check(f"Con {etiqueta} no lanza", True)
        check(f"Con {etiqueta} devuelve la forma vacia",
              r.get("sin_datos") is True and "motivo" in r,
              f"-> {str(r)[:80]}")
        check(f"Con {etiqueta} los montos siguen en None",
              r.get("monto_medio_usd") is None)
    except Exception as e:
        check(f"Con {etiqueta} no lanza", False,
              f"-> {type(e).__name__}: {e}")
MODO["estado"] = "ok"

PETICIONES.clear()
_vacio = _consulta("", "")
check("Sin NSN ni PSC no se hace ni una peticion",
      len(PETICIONES) == 0, f"-> {len(PETICIONES)}")
check("Y se dice por que", "sin codigo" in _vacio["motivo"],
      f"-> {_vacio['motivo']}")

# La busqueda por keywords no sirve para NSN: medido. Se documenta aqui
# para que nadie la reintente creyendo que falta probarla.
check("La documentacion explica por que no se busca por keywords",
      "keywords" in us.__doc__ and "0 resultados" in us.__doc__)
check("Y explica la trampa de la clave 'psc'",
      "psc_codes" in us.__doc__ and "IGNORA EN SILENCIO" in us.__doc__)
check("Y que son montos de contrato, no precios unitarios",
      "MONTOS DE PREMIO, no precios unitarios" in us.__doc__)

print()
print("=" * 76)
print("6) LA CACHE: una pregunta por clase")
print("=" * 76)
_limpiar_cache()
PETICIONES.clear()
primera = us.precios_del_aviso({"classificationCode": "6135"}, "baterias")
n1 = len(PETICIONES)
PETICIONES.clear()
segunda = us.precios_del_aviso({"classificationCode": "6135"}, "baterias")
n2 = len(PETICIONES)
check("La primera vez se pregunta", n1 == 5, f"-> {n1}")
check("La segunda sale de cache", n2 == 0, f"-> {n2}")
check("Y trae los mismos datos", segunda.get("premios") == 11573,
      f"-> {segunda.get('premios')}")
check("Y el NSN que llegue ahora se pinta encima",
      us.precios_del_aviso({"classificationCode": "6135"},
                           "NSN 1650-01-672-3405").get("nsn")
      == "1650-01-672-3405")

# El fracaso tambien se cachea: sin esto, una clase sin premios se
# pregunta otras cinco veces en cada barrido.
_limpiar_cache()
SERVICIOS.update({"cuenta": R_CUENTA_CERO, "clase": R_CLASE_CERO,
                  "mayor": R_VACIA, "menor": R_VACIA, "fecha": R_VACIA})
PETICIONES.clear()
us.precios_del_aviso({"classificationCode": "9999"}, "nada")
n1 = len(PETICIONES)
PETICIONES.clear()
otra = us.precios_del_aviso({"classificationCode": "9999"}, "nada")
n2 = len(PETICIONES)
check("Una clase sin premios se pregunta una vez", n1 == 5, f"-> {n1}")
check("Y el fracaso queda cacheado", n2 == 0, f"-> {n2}")
check("Con su motivo", "sin premios" in str(otra.get("motivo")),
      f"-> {otra.get('motivo')}")
SERVICIOS.update({"cuenta": R_CUENTA, "clase": R_CLASE, "mayor": R_MAYOR,
                  "menor": R_MENOR, "fecha": R_FECHA})
_limpiar_cache()

# La consulta apagada no pide nada, pero la clave sigue en el analisis.
config.PRECIOS_HISTORICOS = False
PETICIONES.clear()
apagada = us.precios_del_aviso({"classificationCode": "6135"}, "x")
check("Apagada no se hace ninguna peticion", len(PETICIONES) == 0)
check("Y se conserva la forma completa",
      apagada.get("sin_datos") is True
      and apagada.get("motivo") == "consulta desactivada")
config.PRECIOS_HISTORICOS = True

print()
print("=" * 76)
print("7) EL PROMPT: la seccion entra, y con ella la prohibicion")
print("=" * 76)
OPP = {"title": "T", "solicitationNumber": "N1"}
p3 = ga._construir_prompt(OPP, "desc", "USA")
check("Sin precios no se monta la seccion",
      "INTELIGENCIA DE PRECIOS HISTORICOS" not in p3)
check("Tres y cuatro argumentos dan el mismo texto",
      ga._construir_prompt(OPP, "desc", "USA", "") == p3)
check("Un precios vacio tampoco monta la seccion",
      "INTELIGENCIA DE PRECIOS HISTORICOS"
      not in ga._construir_prompt(OPP, "desc", "USA", "", {}))
check("Ni un dict sin datos",
      "INTELIGENCIA DE PRECIOS HISTORICOS"
      not in ga._construir_prompt(OPP, "desc", "USA", "",
                                  {"sin_datos": True}))
p5 = ga._construir_prompt(OPP, "desc", "USA", "", datos)
check("Con datos si se monta",
      "INTELIGENCIA DE PRECIOS HISTORICOS (USAspending.gov)" in p5)
check("Trae la media", "127,850" in p5)
check("Trae el rango", "9,476" in p5 and "144,286,210" in p5)
check("Trae el ultimo premio con su fecha y su contratista",
      "Ultimo premio" in p5 and datos["ultimo"]["fecha"][:4] in p5
      and datos["ultimo"]["contratista"] in p5)
check("Trae la tendencia", "ALZA" in p5)
# Lo mas importante del bloque: que el modelo NO convierta un monto de
# contrato en un precio unitario.
check("Prohibe copiarlo a precio_unitario_mercado",
      "NO copies" in p5 and "precio_unitario_mercado" in p5)
check("Dice que son montos de contrato", "MONTOS DE" in p5)
check("Va DESPUES de la descripcion oficial",
      p5.rfind("DESCRIPCION OFICIAL") < p5.find("INTELIGENCIA DE PRECIOS"))
check("Y no se pinta con datos ausentes",
      "INTELIGENCIA DE PRECIOS"
      not in ga._construir_prompt(OPP, "desc", "USA", "",
                                  us._vacio(motivo="x")))

# Un historial corrupto no puede tumbar el analisis: el prompt se
# construye aunque la cache o la API devuelvan basura dentro. Sin esto,
# un campo con un string donde deberia haber un numero se tragaria el
# escaneo entero por una linea de texto.
_ROTO = {"sin_datos": False, "tendencia": "ALZA",
         "ultimo_vs_media_pct": "no es un numero",
         "ultimo": "tampoco es un dict",
         "contratistas": "ni una lista",
         "psc_nombre": "X", "ventana_meses": 36, "premios": 5}
try:
    _p_roto = ga._construir_prompt(OPP, "desc", "USA", "", _ROTO)
    check("Un historial corrupto no tumba el prompt", True)
    check("Y la seccion aun asi se pinta",
          "INTELIGENCIA DE PRECIOS" in _p_roto)
    check("Y el campo malformado se pinta como N/D, no como cero",
          "N/D" in _p_roto)
except Exception as _e:  # pragma: no cover - es exactamente lo que no
    check("Un historial corrupto no tumba el prompt", False,   # debe pasar
          f"-> {type(_e).__name__}: {_e}")

print()
print("=" * 76)
print("8) EL ANALISIS: la trae de vuelta sin cambiar de firma")
print("=" * 76)
firma = inspect.signature(ga.analizar)
check("analizar sigue con la misma firma",
      list(firma.parameters)[:5]
      == ["opp", "descripcion", "lugar", "continuar", "degradado"])
check("_construir_prompt acepta 3, 4 y 5 argumentos",
      len(inspect.signature(ga._construir_prompt).parameters) == 5)
check("consultar_precios_historicos es async con la firma del spec",
      inspect.iscoroutinefunction(us.consultar_precios_historicos)
      and list(inspect.signature(us.consultar_precios_historicos).parameters)
      == ["nsn", "psc"])
check("psc es opcional en la firma del spec",
      inspect.signature(us.consultar_precios_historicos).parameters[
          "psc"].default is None)

# Un cliente falso, igual que en test_economia: la llamada a Google se
# sustituye y el resto del analizar corre entero.
GEMINI = '''{
  "viable": true, "motivo_descarte": "",
  "producto": "Bateria de aeronave 24VDC",
  "modelo_especifico": "", "unidad_medida": "EA", "cantidad_total": 10,
  "especificacion_tecnica_clave": "NSN 1650-01-672-3405",
  "lugar_entrega": "Albuquerque, NM",
  "nsn": "1650-01-672-3405", "numeros_parte": ["S0594DU0500-S1"],
  "valor_contrato_usd": 85000, "precio_unitario_costo": 5200,
  "precio_unitario_mercado": 7450, "precio_unitario_oferta": 7450,
  "precio_oferta_sugerido_usd": 74500, "cumple": "CUMPLE",
  "estrategia_oferta": "Ofertar a 74500.00 USD (ok).",
  "busquedas_distribuidores": [], "distribuidores_candidatos": [],
  "nivel_riesgo": "bajo", "preguntas_criticas": [], "observaciones": ""
}'''

PROMPTS = []


class _Respuesta:
    def __init__(self, texto):
        self.text = texto


class _Modelos:
    def __init__(self):
        self.json = GEMINI

    def generate_content(self, model=None, contents=None, config=None):
        PROMPTS.append(contents if isinstance(contents, str)
                       else str(contents))
        return _Respuesta(self.json)


class _Cliente:
    def __init__(self):
        self.models = _Modelos()


AVISO = {"noticeId": "usc-8f3a", "title": "Bateria aeronave",
         "solicitationNumber": "1234AB25R0001",
         "classificationCode": "6135", "naicsCode": "334111",
         "responseDeadLine": "2026-12-01", "postedDate": "2026-11-01",
         "uiLink": "https://sam.gov/x"}


def _analizar(opp, desc="descripcion oficial"):
    cliente = _Cliente()
    original = ga._get_cliente
    ga._get_cliente = lambda: cliente
    try:
        return ga.analizar(opp, desc, "Albuquerque, NM")
    finally:
        ga._get_cliente = original


_limpiar_cache()
PROMPTS.clear()
salida = _analizar(AVISO)
check("El analisis llega con la clave",
      isinstance(salida.get("precios_historicos"), dict),
      f"-> {type(salida.get('precios_historicos')).__name__}")
check("Y con datos de mercado",
      salida["precios_historicos"].get("sin_datos") is False)
check("El prompt que recibio el modelo la traia",
      any("INTELIGENCIA DE PRECIOS HISTORICOS" in p for p in PROMPTS))
check("Y con la prohibicion de precio unitario",
      any("NO copies" in p for p in PROMPTS))
check("El codigo con el que busco quedo registrado",
      salida["precios_historicos"].get("psc") == "6135"
      or salida["precios_historicos"].get("nsn") == "1650-01-672-3405",
      f"-> {salida['precios_historicos']}")

# Con la consulta apagada la clave sigue ahi, pero vacia: un campo que
# desaparece segun la config es un campo que nadie puede consumir.
config.PRECIOS_HISTORICOS = False
PROMPTS.clear()
apagado = _analizar(AVISO)
config.PRECIOS_HISTORICOS = True
check("Apagada la clave sigue en el analisis",
      isinstance(apagado.get("precios_historicos"), dict))
check("Pero marcada como sin datos",
      apagado["precios_historicos"].get("sin_datos") is True)
check("Y el prompt no monta la seccion",
      not any("INTELIGENCIA DE PRECIOS HISTORICOS" in p for p in PROMPTS))

print()
print("=" * 76)
print("9) LA FICHA DE TELEGRAM")
print("=" * 76)
FICHA = {
    "title": "Bateria aeronave 24VDC",
    "agencia": "Department of the Air Force",
    "solicitation": "1234AB25R0001", "limite": "2026-12-01",
    "set_aside": "Total Small Business", "producto": "Bateria",
    "valor_contrato_usd": 85000.0, "costo_total_usd": 52000.0,
    "precio_oferta_sugerido_usd": 74500.0, "ganancia_neta_usd": 19892.50,
    "margen_neto_porcentaje": 26.7, "nsn": "1650-01-672-3405",
    "numeros_parte": ["S0594DU0500-S1"], "cumple": "CUMPLE",
    "precios_historicos": datos,
    "escenarios_oferta": [{"pct": 100, "precio": 85000,
                           "ganancia_neta": 20000, "margen": 23.5}],
    "distribuidores_candidatos": [],
    "ui_link": "https://sam.gov/x", "contacto": "",
    "observaciones": "", "estrategia_oferta": "",
    "razonamiento_oferta": "Ofertar a 74500.00 USD (ok).",
}
h = tn.formatear_analisis(FICHA)
check("Se pinta el bloque",
      "📈 <b>Inteligencia de Mercado (USAspending)</b>" in h)
_i = h.find("📈")
_bloque = h[_i:h.find("</pre>", _i) + 6]
print(_bloque)
check("Trae la media", "$127,850" in _bloque)
check("Trae el rango", "$9,476 - $144,286,210" in _bloque)
check("Trae el ultimo premio con su fecha",
      datos["ultimo"]["fecha"][:4] in _bloque)
check("Trae la tendencia", "ALZA" in _bloque)
check("Trae los contratistas", "LOCKHEED MARTIN CORP" in _bloque)
check("Compara contra el presupuesto del aviso",
      "Vs presupuesto" in _bloque and "la media" in _bloque)
check("La nota dice que NO son precios unitarios",
      "no precios unitarios" in h)
check("El bloque va en su propio <pre> y la ficha queda balanceada",
      h.count("<pre>") == h.count("</pre>"))

# [2:-1] salta la cabecera y la etiqueta <pre>: solo las filas de datos.
_lineas = [l for l in _bloque.split("\n")[2:-1] if l.strip()]
check("Hay filas de datos que medir", len(_lineas) >= 5,
      f"-> {len(_lineas)}")
check("Todas las etiquetas miden lo mismo",
      {len(l[:17]) for l in _lineas} == {17}
      and all(l[:17].strip() for l in _lineas),
      f"-> {[len(l[:17]) for l in _lineas]}")
check("Ninguna linea se pasa de 80 caracteres",
      all(len(l) <= 80 for l in _lineas),
      f"-> {max(len(l) for l in _lineas)}")

# Sin datos no hay bloque: una tabla llena de N/E estorba y ademas cuesta
# un trozo de 4096 que la ficha ya no tiene.
_F2 = dict(FICHA)
_F2["precios_historicos"] = {"sin_datos": True, "motivo": "x"}
check("Sin datos no se pinta", "📈" not in tn.formatear_analisis(_F2))
_F3 = dict(FICHA)
del _F3["precios_historicos"]
check("Sin la clave (analisis viejo) tampoco se pinta",
      "📈" not in tn.formatear_analisis(_F3))
_F4 = dict(FICHA)
_F4["precios_historicos"] = {}
check("Con un dict vacio tampoco", "📈" not in tn.formatear_analisis(_F4))
_F5 = dict(FICHA)
_F5["precios_historicos"] = "basura"
check("Con la clave rota no se pinta y no revienta",
      "📈" not in tn.formatear_analisis(_F5))

# Igual que en el prompt: campos malformados dentro del dict no pueden
# tumbar la ficha entera. Se descarta ese dato y el resto se pinta.
_F7 = dict(FICHA)
_F7["precios_historicos"] = dict(
    datos, ultimo_vs_media_pct="no es un numero", premios="basura",
    monto_min_usd=None, monto_max_usd="tampoco", ultimo="ni eso",
    contratistas="ni eso")
try:
    _h7 = tn.formatear_analisis(_F7)
    check("Un historial corrupto no tumba la ficha", True)
    check("Y la comparacion con el presupuesto aun asi sale",
          "Vs presupuesto" in _h7)
except Exception as _e:  # pragma: no cover
    check("Un historial corrupto no tumba la ficha", False,
          f"-> {type(_e).__name__}: {_e}")

_trozos = tn._trocear(h)
check("Cada trozo cabe en Telegram",
      all(len(t) < 4096 for t in _trozos),
      f"-> max {max(len(t) for t in _trozos)}")
check("El RFQ viaja entero en un trozo",
      all("Subject:" not in t or "Albuquerque, NM 87110" in t
          for t in _trozos))
check("El mensaje no trae @ (sin correos fabricados)", "@" not in h)
check("El mensaje no trae EIN",
      not any(x in h for x in ("EIN", "Employer Identification")))

print()
print("=" * 76)
print("10) COMPATIBILIDAD")
print("=" * 76)
_req = open("requirements.txt", encoding="utf-8").read()
check("requirements.txt sigue sin httpx", "httpx" not in _req)
_src = open("usaspending_client.py", encoding="utf-8").read()
check("httpx se importa de forma perezosa",
      "import httpx" not in _src.split("def ", 1)[0])
check("La API no exige clave y el modulo no inventa cabeceras de auth",
      "Authorization" not in _src and "api_key" not in _src)
_cfg = open("config.py", encoding="utf-8").read()
check("La base es configurable por entorno",
      "USASPENDING_URL" in _cfg)
check("La consulta se puede apagar por entorno",
      "PRECIOS_HISTORICOS" in _cfg)
check("La cache es por la clase y no por el aviso",
      '"precios:"' in _src)

# El analisis viejo, sin la clave, sigue formateandose igual.
_F6 = dict(FICHA)
del _F6["precios_historicos"]
_h6 = tn.formatear_analisis(_F6)
check("Una ficha vieja sigue completa",
      all(x in _h6 for x in ("✨", "📦", "🏛️", "🔢", "🏷️",
                             "⚠️ Nivel de Complejidad", "📝", "💰", "📊",
                             "🎯", "🏭", "🔗", "💰 <b>Resumen financiero</b>",
                             "🎯 <b>Estrategia de oferta</b>")))

us._post = _POST_REAL

print()
print("=" * 76)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s) en el modulo de precios historicos")
    sys.exit(1)
print("RESULTADO: USAspending consulta, se cachea, entra en el prompt y "
      "sale en la ficha, en verde")
sys.exit(0)
