# -*- coding: utf-8 -*-
"""
test_fase4.py - Fase 4: autocalibracion y feedback autonomo.

QUE SE COMPRUEBA

1) PAQUETE (autocalibracion.empaquetar_barrido): el resumen del barrido se
   convierte en titulos + PSC/NAICS + log de descartes + cola de trabados,
   sin leer el scanner ni la base de datos, y sin lanzar aunque el resumen
   venga vacio o None.
2) JSON ESTRICTO: la IA tiene que devolver exactamente dos claves; todo lo
   demas se ignora y cada valor pasa por lista blanca, rango y alfabeto
   antes de tocar nada.
3) LA IA NO PUEDE ROMPER EL MOTOR: claves inventadas no existen, pesos
   fuera de rango se recortan, tipos malos se descartan, los UMBRALES no
   son alcanzables y un veto sigue siendo -100 con la calibracion puesta.
4) PERSISTENCIA Y DINAMISMO: se escribe en el JSON, config.peso() y
   config.excepciones_dinamicas() lo leen en caliente, refrescar() abre el
   ciclo, y LAS CONSTANTES ESTATICAS NO SE REESCRIBEN jamas.
5) BYPASS: un "REMAN ALTERNATOR KIT" deja de costar -20 cuando el token
   esta aprendido, con limites de palabra ("kit" no matchea "kitchen").
6) CICLO ASINCRONO: ciclo_post_barrido hace las tres fases sin lanza, y
   main.lanzar_autocalibracion crea una tarea que termina y se suelta sola.
7) RETROCOMPATIBILIDAD: firmas intactas, sin calibracion el comportamiento
   es el de siempre, y ni un secreto en el modulo nuevo.

Este test NO toca la red: la IA va monkeypatcheada en la seccion 6.
"""
import asyncio
import inspect
import json
import os
import sys
import tempfile

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print("  OK    %s" % nombre)
    else:
        FALLA += 1
        print("  FALLA %s %s" % (nombre, extra))


DB = os.path.join(tempfile.gettempdir(), "kyo_test_fase4.db")
CAL = os.path.join(tempfile.gettempdir(), "kyo_test_fase4.json")
for _r in (DB, CAL):
    for _s in ("", "-wal", "-shm", ".tmp"):
        try:
            os.remove(_r + _s)
        except OSError:
            pass

os.environ["DB_PATH"] = DB
os.environ["AUTOCALIBRACION_FICHERO"] = CAL
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "x"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"

import autocalibracion as ac
import config
import filters
import scanner

# El aviso de "ciclo interrumpido" es el comportamiento CORRECTO (una
# excepcion se convierte en resultado), pero ensucia la salida con un
# logueo antes del titulo de la seccion. Se silencia aqui: la seccion 6
# comprueba igualmente que el resultado vuelve con ok=False.
import logging
logging.getLogger("autocalibracion").setLevel(logging.CRITICAL)


def _sin_calibracion():
    """Borra el estado aprendido y recarga la cache a vacio."""
    for _s in ("", ".tmp"):
        try:
            os.remove(CAL + _s)
        except OSError:
            pass
    ac.cargar(force=True)


def _aprender(tokens, pesos):
    """Aplica un payload como lo haria la IA, sin pasar por la IA."""
    return ac.aplicar({"dynamic_whitelist_tokens": list(tokens),
                       "adjusted_complexity_weights": dict(pesos)})


# =====================================================================
# 1) EL PAQUETE: TITULOS, PSC/NAICS, DESCARTES Y COLA
# =====================================================================
print("=" * 76)
print("1) PAQUETE DE BARRIDO PARA EL PIPELINE SECUNDARIO")
print("=" * 76)

RESUMEN = {
    "traidas": 40, "candidatas": 18, "puntuales": 9, "analizadas": 4,
    "viables": 1, "notificadas": 1, "cuota": {},
    "errores": ["Gemini 503 en el aviso 1234567890"],
    "detalle_puntajes": [
        {"puntaje": 12,
         "titulo": "REMAN ALTERNATOR AND BRACKET KIT",
         "motivos": ["2 termino(s) de producto en el titulo"],
         "psc": "2910", "naics": "336"},
        {"puntaje": 4,
         "titulo": "MODIFICATION KIT FOR PUMP SKID",
         "motivos": ["-20 por termino(s) de servicio"],
         "psc": "4010", "naics": "237"},
    ],
    "descartes": [
        {"notice_id": "1234567890", "titulo": "AIRCRAFT COATINGS",
         "psc": "8010", "naics": "325",
         "motivo": "high_complexity_defense: AIRCRAFT COATINGS"},
    ],
    "descarte_motivo": {"high_complexity_defense": 3},
    "analisis": [],
}

paq = ac.empaquetar_barrido(RESUMEN)

check("el paquete se puede serializar a JSON", bool(json.dumps(paq)))
check("lleva fecha", bool(paq.get("fecha")))
check("trae los conteos del barrido",
      paq["conteo"] == {"traidas": 40, "candidatas": 18, "puntuales": 9,
                        "analizadas": 4, "viables": 1}, paq["conteo"])
check("los avisos traen titulo", len(paq["avisos"]) == 2
      and paq["avisos"][0]["titulo"].startswith("REMAN"))
check("los avisos traen PSC y NAICS",
      paq["avisos"][0]["psc"] == "2910" and paq["avisos"][0]["naics"] == "336"
      and paq["avisos"][1]["psc"] == "4010",
      paq["avisos"])
check("los avisos traen puntaje y motivos",
      paq["avisos"][0]["puntaje"] == 12
      and "producto en el titulo" in paq["avisos"][0]["motivos"][0])
check("los descartes traen titulo, codigos y motivo",
      paq["descartes"][0]["titulo"] == "AIRCRAFT COATINGS"
      and paq["descartes"][0]["psc"] == "8010"
      and paq["descartes"][0]["naics"] == "325"
      and "high_complexity_defense" in paq["descartes"][0]["motivo"],
      paq["descartes"])
check("el conteo de descartes por motivo viaja",
      paq["conteo_descartes"]["high_complexity_defense"] == 3)
check("la cola dice cuantas se quedaron sin analizar",
      paq["cola"]["sin_analizar"] == 5, paq["cola"])
check("la cola trae los errores", len(paq["cola"]["errores"]) == 1
      and "503" in paq["cola"]["errores"][0])

print()
print("  Un resumen vacio o None no puede tumbar el empaquetado:")
vacio = ac.empaquetar_barrido({})
check("resumen vacio no tiene avisos ni descartes",
      vacio["avisos"] == [] and vacio["descartes"] == [])
check("resumen None tampoco lanza", isinstance(ac.empaquetar_barrido(None), dict))
check("resumen de mentira no lanza", isinstance(ac.empaquetar_barrido(42), dict))
check("el paquete vacio no es util", not ac._util(vacio))
check("el paquete de prueba si es util", ac._util(paq))

# =====================================================================
# 2) RESPUESTA ESTRICTA DE LA IA: DOS CLAVES Y NADA MAS
# =====================================================================
print()
print("=" * 76)
print("2) JSON ESTRICTO: EXACTAMENTE DOS ARREGLOS")
print("=" * 76)

bueno = {"dynamic_whitelist_tokens": ["REMAN", "Mod Kit"],
         "adjusted_complexity_weights": {"PENALIZACION_MANTENIMIENTO": -15},
         "ruido_que_se_ignora": "no deberia romper nada"}
v = ac.validar(bueno)
check("acepta las dos claves", bool(v), v)
check("los tokens salen en minusculas",
      v["dynamic_whitelist_tokens"] == ["reman", "mod kit"],
      v.get("dynamic_whitelist_tokens"))
check("el peso sale como entero y con su valor",
      v["adjusted_complexity_weights"] == {"PENALIZACION_MANTENIMIENTO": -15}
      and isinstance(v["adjusted_complexity_weights"]["PENALIZACION_MANTENIMIENTO"], int))
check("la clave de mas no se cuela en el resultado", "ruido_que_se_ignora" not in v)

print()
print("  Sin las DOS claves no hay contrato:")
check("falta la de pesos -> rechazado",
      ac.validar({"dynamic_whitelist_tokens": ["reman"]}) == {})
check("falta la de tokens -> rechazado",
      ac.validar({"adjusted_complexity_weights": {"COTS_PUNTOS": 4}}) == {})
check("ambas malas -> rechazado",
      ac.validar({"dynamic_whitelist_tokens": "reman",
                  "adjusted_complexity_weights": []}) == {})
check("no es un objeto -> rechazado",
      ac.validar("dynamic_whitelist_tokens") == {} and ac.validar(None) == {})
check("None no lanza", ac.validar(None) == {})
check("lista no lanza", ac.validar([1, 2, 3]) == {})

print()
print("  Filtros de los tokens:")
check("mayusculas -> minusculas",
      ac.validar({"dynamic_whitelist_tokens": ["REMAN"],
                  "adjusted_complexity_weights": {}})["dynamic_whitelist_tokens"] == ["reman"])
check("token de 2 letras se cae",
      ac.validar({"dynamic_whitelist_tokens": ["ab"],
                  "adjusted_complexity_weights": {}})["dynamic_whitelist_tokens"] == [])
check("token de 41 se cae",
      ac.validar({"dynamic_whitelist_tokens": ["x" * 41],
                  "adjusted_complexity_weights": {}})["dynamic_whitelist_tokens"] == [])
check("caracteres raros se caen",
      ac.validar({"dynamic_whitelist_tokens": ["re$man; DROP"],
                  "adjusted_complexity_weights": {}})["dynamic_whitelist_tokens"] == [])
check("duplicados se funden",
      ac.validar({"dynamic_whitelist_tokens": ["reman", "REMAN", " reman "],
                  "adjusted_complexity_weights": {}})["dynamic_whitelist_tokens"] == ["reman"])
_largos = ["token%02d" % i for i in range(40)]
_tope = ac.validar({"dynamic_whitelist_tokens": _largos,
                    "adjusted_complexity_weights": {}})
check("nunca se pasan de %d tokens" % ac.MAX_TOKENS,
      len(_tope["dynamic_whitelist_tokens"]) == ac.MAX_TOKENS,
      len(_tope["dynamic_whitelist_tokens"]))

print()
print("  Filtros de los pesos:")
_p = ac.validar({"dynamic_whitelist_tokens": [],
                 "adjusted_complexity_weights": {
                     "PENALIZACION_MANTENIMIENTO": -15,
                     "PUNTAJE_MINIMO": 1,
                     "COTS_PUNTOS": "2",
                     "SET_ASIDE_PYME_PUNTOS": True,
                     "PENALIZACION_ARMAMENTO": float("nan")}})
check("los pesos buenos pasan",
      _p["adjusted_complexity_weights"] == {"PENALIZACION_MANTENIMIENTO": -15},
      _p["adjusted_complexity_weights"])
check("una clave inventada NO existe", "PUNTAJE_MINIMO" not in _p["adjusted_complexity_weights"])
check("cadena, bool y NaN se descartan sin lanza",
      "COTS_PUNTOS" not in _p["adjusted_complexity_weights"]
      and "SET_ASIDE_PYME_PUNTOS" not in _p["adjusted_complexity_weights"]
      and "PENALIZACION_ARMAMENTO" not in _p["adjusted_complexity_weights"])

_fuera = ac.validar({"dynamic_whitelist_tokens": [],
                     "adjusted_complexity_weights": {
                         "PENALIZACION_MANTENIMIENTO": -999,
                         "PENALIZACION_URGENCIA": 50,
                         "COTS_PUNTOS": -7}})
check("fuera de rango se recorta, no se descarta",
      _fuera["adjusted_complexity_weights"]
      == {"PENALIZACION_MANTENIMIENTO": ac.PESOS_PERMITIDOS["PENALIZACION_MANTENIMIENTO"][0],
          "PENALIZACION_URGENCIA": 0,
          "COTS_PUNTOS": 0},
      _fuera["adjusted_complexity_weights"])

# =====================================================================
# 3) LA IA NO PUEDE ROMPER EL MOTOR
# =====================================================================
print()
print("=" * 76)
print("3) LA IA APRENDE, NO DECIDE")
print("=" * 76)

check("la lista blanca solo tiene pesos que existen en config",
      all(hasattr(config, n) for n in ac.PESOS_PERMITIDOS),
      sorted(ac.PESOS_PERMITIDOS))
check("y todos son enteros",
      all(isinstance(getattr(config, n), int) for n in ac.PESOS_PERMITIDOS))
check("los UMBRALES no son alcanzables",
      "PUNTAJE_MINIMO" not in ac.PESOS_PERMITIDOS
      and "PUNTAJE_MINIMO_DEGRADADO" not in ac.PESOS_PERMITIDOS)
check("cada rango contiene el valor estatico actual",
      all(lo <= getattr(config, n) <= hi
          for n, (lo, hi) in ac.PESOS_PERMITIDOS.items()),
      {n: getattr(config, n) for n in ac.PESOS_PERMITIDOS})
check("el modulo expone las dos claves del contrato",
      ac.CLAVE_TOKENS == "dynamic_whitelist_tokens"
      and ac.CLAVE_PESOS == "adjusted_complexity_weights")

_sin_calibracion()
TITULO_VETO = "GUIDED MISSILE"
sin_cal = filters.puntuar({"title": TITULO_VETO,
                           "classificationCode": "1520"}, "")
check("un titulo de defensa es -100 aunque no haya calibracion",
      sin_cal[0] == -100, sin_cal)

# El peor caso posible: la IA pide un token que apunta JUSTO al veto.
_aprender(["guided missile", "missile"], {"PENALIZACION_MANTENIMIENTO": -1,
                                          "COTS_PUNTOS": 99,
                                          "PUNTAJE_MINIMO": 1})
con_cal = filters.puntuar({"title": TITULO_VETO,
                           "classificationCode": "1520"}, "")
check("ni el token ni los pesos relajan el VETO",
      con_cal[0] == -100, con_cal)
check("el umbral sigue mandando el de siempre",
      config.peso("PUNTAJE_MINIMO") == config.PUNTAJE_MINIMO
      and config.PUNTAJE_MINIMO == 6)
check("COTS_PUNTOS se recorto al techo y no a 99",
      config.peso("COTS_PUNTOS") == ac.PESOS_PERMITIDOS["COTS_PUNTOS"][1])

print()
print("  Payloads que no son contrato, y hostiles:")
_malo = {"dynamic_whitelist_tokens": ["reman"], "version": 999}
res_malo = ac.aplicar(_malo)
check("aplicar no lanza nunca", isinstance(res_malo, dict))
check("le falta una clave -> rechazado entero", res_malo.get("ok") is False, res_malo)
check("y el estado aprendido sigue vivo",
      ac.cargar(force=True).get("dynamic_whitelist_tokens")
      == ["guided missile", "missile"],
      ac.cargar(force=True))

_hostil = {"dynamic_whitelist_tokens": [None, 7, {}, "  ", "re$man"],
           "adjusted_complexity_weights": {"NO_SE": None, "COTS_PUNTOS": []},
           "version": 999, "ciclos": "mucho", "actualizado": 123}
_v_hostil = ac.validar(_hostil)
check("tipos malos se filtran y no lanza", isinstance(_v_hostil, dict))
check("los tokens basura no pasan", _v_hostil["dynamic_whitelist_tokens"] == [])
check("los pesos basura no pasan", _v_hostil["adjusted_complexity_weights"] == {})
check("versiones ajenas no se cuelan",
      _v_hostil.get("version") == config.AUTOCALIBRACION_VERSION
      and "ciclos" not in _v_hostil, _v_hostil)

# =====================================================================
# 4) PERSISTENCIA Y DINAMISMO
# =====================================================================
print()
print("=" * 76)
print("4) PERSISTENCIA: EL MOTOR LEE EL FICHERO EN CADA CICLO")
print("=" * 76)

_sin_calibracion()
check("sin fichero no hay calibracion", ac.cargar() == {})
check("config.peso devuelve el estatico",
      config.peso("PENALIZACION_MANTENIMIENTO") == config.PENALIZACION_MANTENIMIENTO)
check("config.excepciones_dinamicas viene vacio",
      config.excepciones_dinamicas() == set())
check("el fichero sigue el camino de DB_PATH",
      config.AUTOCALIBRACION_FICHERO == CAL, config.AUTOCALIBRACION_FICHERO)
check("por defecto esta habilitado", config.AUTOCALIBRACION_HABILITADA is True)

r = _aprender(["reman"], {"PENALIZACION_MANTENIMIENTO": -15})
check("aplicar acepta un payload valido", r.get("ok") is True, r)
check("y dice que hubo cambio", r.get("sin_cambios") is False, r)
check("el fichero existe", os.path.exists(CAL))

en_fichero = json.load(open(CAL, encoding="utf-8"))
check("el fichero es JSON valido con las dos claves",
      en_fichero["dynamic_whitelist_tokens"] == ["reman"]
      and en_fichero["adjusted_complexity_weights"] == {"PENALIZACION_MANTENIMIENTO": -15},
      en_fichero)
check("lleva version y contador de ciclos",
      en_fichero["version"] == config.AUTOCALIBRACION_VERSION
      and en_fichero["ciclos"] == 1, en_fichero)
check("el fichero solo lleva las claves previstas",
      set(en_fichero) <= {"version", "actualizado", "ciclos",
                          "dynamic_whitelist_tokens",
                          "adjusted_complexity_weights"},
      sorted(en_fichero))

check("config.peso LEE EN CALIENTE el valor aprendido",
      config.peso("PENALIZACION_MANTENIMIENTO") == -15,
      config.peso("PENALIZACION_MANTENIMIENTO"))
check("config.excepciones_dinamicas LEE EN CALIENTE",
      config.excepciones_dinamicas() == {"reman"},
      config.excepciones_dinamicas())
check("LAS CONSTANTES ESTATICAS NO SE REESCRIBEN",
      config.PENALIZACION_MANTENIMIENTO == -20
      and config.COTS_PUNTOS == 2,
      (config.PENALIZACION_MANTENIMIENTO, config.COTS_PUNTOS))
check("un peso no calibrado cae al estatico",
      config.peso("COTS_PUNTOS") == config.COTS_PUNTOS)
check("un nombre que no existe devuelve None", config.peso("NO_EXISTE") is None)
check("refrescar() abre el ciclo con lo aprendido",
      ac.refrescar().get("dynamic_whitelist_tokens") == ["reman"],
      ac.refrescar())

r2 = _aprender(["reman"], {"PENALIZACION_MANTENIMIENTO": -15})
check("reaplicar lo mismo no reescribe", r2.get("sin_cambios") is True, r2)
check("y el contador de ciclos no sube",
      json.load(open(CAL, encoding="utf-8"))["ciclos"] == 1)

r3 = _aprender(["reman", "mod kit"], {"PENALIZACION_MANTENIMIENTO": -12})
check("un cambio de verdad sube el contador",
      r3.get("sin_cambios") is False
      and json.load(open(CAL, encoding="utf-8"))["ciclos"] == 2,
      json.load(open(CAL, encoding="utf-8")))
check("y el motor lo ve sin reiniciar nada",
      config.peso("PENALIZACION_MANTENIMIENTO") == -12
      and config.excepciones_dinamicas() == {"reman", "mod kit"})

print()
print("  Con la calibracion apagada, el motor cae en los estaticos:")
_config_hab = config.AUTOCALIBRACION_HABILITADA
config.AUTOCALIBRACION_HABILITADA = False
try:
    check("peso -> estatico", config.peso("PENALIZACION_MANTENIMIENTO") == -20)
    check("excepciones -> vacio", config.excepciones_dinamicas() == set())
    check("calibracion() -> vacio", config.calibracion() == {})
    check("aplicar -> rechazado",
          ac.aplicar({"dynamic_whitelist_tokens": ["x"],
                      "adjusted_complexity_weights": {}}).get("ok") is False)
finally:
    config.AUTOCALIBRACION_HABILITADA = _config_hab
check("y al reactivarla vuelve lo aprendido",
      config.peso("PENALIZACION_MANTENIMIENTO") == -12)

# =====================================================================
# 5) EL BYPASS POR TOKENS: FALSOS POSITIVOS COTS
# =====================================================================
print()
print("=" * 76)
print("5) BYPASS: UN ARTICULO COTS DEJA DE PAGAR LA PALABRA AMBIGUA")
print("=" * 76)

T_REMAN = "REMAN ALTERNATOR AND BRACKET KIT"
_sin_calibracion()

sin_token = filters.penalizaciones_patron(T_REMAN)
check("sin token, 'reman' cuesta -20", sin_token[0] == config.PENALIZACION_MANTENIMIENTO,
      sin_token)
check("y el motivo lo explica",
      any("mantenimiento/taller" in m for m in sin_token[1]), sin_token)

_aprender(["reman"], {})
con_token = filters.penalizaciones_patron(T_REMAN)
check("con token, la penalizacion se relaja a 0", con_token[0] == 0, con_token)
check("y el motivo dice por que",
      any("bypass COTS aprendido (reman)" in m for m in con_token[1]), con_token)

_sin_calibracion()
sin_puntuar = filters.puntuar({"title": T_REMAN,
                               "classificationCode": "2910"}, "")
_aprender(["reman"], {})
con_puntuar = filters.puntuar({"title": T_REMAN,
                               "classificationCode": "2910"}, "")
check("de punta a punta, el puntaje sube exactamente 20",
      con_puntuar[0] == sin_puntuar[0] + 20,
      (sin_puntuar[0], con_puntuar[0]))
check("el bypass llega a /debug", any("bypass COTS aprendido" in m
                                      for m in con_puntuar[1]), con_puntuar)

print()
print("  Limites de palabra: el token no se dispara dentro de otra:")
_aprender(["kit"], {})
check("'kit' matchea en 'PUMP KIT'", filters._bypass_aprendido("PUMP KIT") == "kit")
check("'kit' NO matchea en 'KITCHEN SINK'", filters._bypass_aprendido("KITCHEN SINK") == "")
check("'kit' NO matchea en 'KITSAP'", filters._bypass_aprendido("KITSAP COUNTY") == "")

_sin_calibracion()
check("sin calibracion no hay bypass",
      filters._bypass_aprendido("PUMP KIT") == "")
check("y las penalizaciones son las de siempre",
      filters.penalizaciones_patron(T_REMAN)[0] == config.PENALIZACION_MANTENIMIENTO)

# =====================================================================
# 6) LA TAREA ASINCRONA POST-BARRIDO
# =====================================================================
print()
print("=" * 76)
print("6) CICLO ASINCRONO: NADA BLOQUEA Y NADA LANZA")
print("=" * 76)

_sin_calibracion()
_visto = {}


def _falso_meta(paquete):
    _visto["paquete"] = paquete
    return {"dynamic_whitelist_tokens": ["reman"],
            "adjusted_complexity_weights": {"PENALIZACION_MANTENIMIENTO": -12}}


_meta_orig = ac.meta_analizar
ac.meta_analizar = _falso_meta
try:
    res = asyncio.run(ac.ciclo_post_barrido(RESUMEN))
finally:
    ac.meta_analizar = _meta_orig

check("el ciclo termina con ok", res.get("ok") is True, res)
check("llego a la IA con el paquete", bool(_visto.get("paquete", {}).get("avisos")))
check("y dejo la calibracion aplicada",
      config.peso("PENALIZACION_MANTENIMIENTO") == -12,
      config.peso("PENALIZACION_MANTENIMIENTO"))

res_vacio = asyncio.run(ac.ciclo_post_barrido({}))
check("resumen vacio no lanza", res_vacio.get("ok") is False, res_vacio)
check("y lo dice con un motivo claro",
      "paquete" in res_vacio.get("motivo", ""), res_vacio)
check("resumen None tampoco lanza",
      asyncio.run(ac.ciclo_post_barrido(None)).get("ok") is False)


def _explota(paquete):
    raise RuntimeError("boom de la IA")


ac.meta_analizar = _explota
try:
    res_err = asyncio.run(ac.ciclo_post_barrido(RESUMEN))
finally:
    ac.meta_analizar = _meta_orig
check("una excepcion se convierte en resultado, no en tarea muerta",
      res_err.get("ok") is False and "boom" in res_err.get("motivo", ""), res_err)

print()
print("  main lanza la tarea al terminar el barrido y no la retiene:")


async def _falso_ciclo(resumen_recibido):
    _visto["ciclo"] = resumen_recibido
    return {"ok": True}


import main as main_mod

_ciclo_orig = ac.ciclo_post_barrido


async def _probar_main():
    ac.ciclo_post_barrido = _falso_ciclo
    try:
        main_mod.lanzar_autocalibracion(RESUMEN)
        creadas = len(main_mod.TAREAS_FOND)
        tareas = list(main_mod.TAREAS_FOND)
        if tareas:
            await asyncio.gather(*tareas)
        for _ in range(3):
            await asyncio.sleep(0)
        quedan = len(main_mod.TAREAS_FOND)
    finally:
        ac.ciclo_post_barrido = _ciclo_orig
    return creadas, quedan


creadas, quedan = asyncio.run(_probar_main())
check("lanzar_autocalibracion crea una tarea", creadas == 1, creadas)
check("la tarea termina", _visto.get("ciclo") is RESUMEN)
check("y se suelta sola del conjunto", quedan == 0, quedan)

_sin_calibracion()

# =====================================================================
# 7) RETROCOMPATIBILIDAD
# =====================================================================
print()
print("=" * 76)
print("7) RETROCOMPATIBILIDAD: FIRMAS, DEFAULTS Y CERO SECRETOS")
print("=" * 76)

sig_p = inspect.signature(filters.puntuar)
check("puntuar conserva sus 2 parametros",
      list(sig_p.parameters) == ["opp", "descripcion"], sig_p)
check("descripcion sigue opcional", sig_p.parameters["descripcion"].default == "")

sig_l = inspect.signature(filters.penalizaciones_patron)
check("penalizaciones_patron conserva su firma",
      list(sig_l.parameters) == ["texto"], sig_l)

sig_u = inspect.signature(filters.penalizacion_urgencia)
check("penalizacion_urgencia conserva su firma",
      list(sig_u.parameters) == ["opp", "texto", "dias"], sig_u)

sig_c = inspect.signature(scanner._contar_descarte)
check("_contar_descarte sigue llamable como antes",
      all(p.default is not inspect.Parameter.empty
          for n, p in sig_c.parameters.items() if n != "motivos"), sig_c)
scanner.limpiar_descarte()
scanner._contar_descarte(["veto: prueba de la vieja escuela"])
check("la llamada vieja sigue contando",
      scanner.DESCARTE_POR_MOTIVO.get("veto") == 1,
      scanner.DESCARTE_POR_MOTIVO)
check("sin opp no se llena el log de detalle", scanner.DESCARTE_LOG == [])

sig_e = inspect.signature(scanner.escanear)
check("escanear conserva sus 3 parametros",
      list(sig_e.parameters) == ["chat_id", "dias", "progreso"], sig_e)
check("el resumen ahora publica los descartes",
      '"descartes": []' in inspect.getsource(scanner))
check("y el detalle trae PSC y NAICS",
      '"psc": str(o.get("classificationCode")' in inspect.getsource(scanner))
check("y abre el ciclo releyendo la calibracion",
      "autocalibracion.refrescar()" in inspect.getsource(scanner))

fuente_ac = inspect.getsource(ac)
check("autocalibracion no lleva claves",
      "AIza" not in fuente_ac and "SAM-" not in fuente_ac)
check("autocalibracion no lee el entorno directamente",
      "os.getenv" not in fuente_ac)
check("autocalibracion no habla HTTP crudo",
      "requests" not in fuente_ac and "urllib" not in fuente_ac)
check("el prompt exige las dos claves",
      ac.CLAVE_TOKENS in fuente_ac and ac.CLAVE_PESOS in fuente_ac)
check("el prompt prohibe tocar el puntaje minimo",
      "puntaje minimo" in fuente_ac)
check("el modulo no importa main ni telegram",
      "import main" not in fuente_ac and "telegram" not in fuente_ac)

check("las constantes estaticas siguen intactas",
      config.PENALIZACION_MANTENIMIENTO == -20
      and config.PENALIZACION_ARMAMENTO == -20
      and config.PENALIZACION_URGENCIA == -10
      and config.PENALIZACION_OBRA_PUNTOS == 3
      and config.COTS_PUNTOS == 2
      and config.SET_ASIDE_PYME_PUNTOS == 2)

# Limpieza: este test no debe dejar estado aprendido atras.
_sin_calibracion()

print()
print("=" * 76)
if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: autocalibracion y feedback autonomo en verde")
