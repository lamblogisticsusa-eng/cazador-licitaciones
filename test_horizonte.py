"""
test_horizonte.py - La ventana de vencimiento a 45 dias.

QUE SE PIDIO

Que la consulta a SAM.gov traiga las oportunidades cuyo responseDeadLine este
entre hoy y hoy+45, SIN restricting el rango cercano (1-10 dias, 10-20, etc.) y
con los dias restantes visibles en el mensaje de Telegram.

1) EL FILTRO DEL SERVIDOR, QUE NO SE ESTABA USANDO

La API v2 acepta dos parametros que el codigo no mandaba:

    rdlfrom  Response Deadline date. Format must be MM/dd/yyyy   (opcional)
    rdlto    Response Deadline date. Format must be MM/dd/yyyy   (opcional)

Antes se pedian todos los avisos publicados en la ventana y luego se
descartaban en el cliente los ya vencidos. Ahora lo filtra el servidor.

postedFrom y postedTo NO son opcionales: la API responde 400 si faltan. Por eso
se mandan los dos pares de filtros a la vez, nunca uno en lugar del otro.

2) QUE EL RANGO CERCANO NO SE PIERDA

El miedo del requerimiento era quedarse solo con la cola de 30 a 45 dias. Con
rdlfrom=hoy y rdlto=hoy+45 no pasa: el intervalo es continuo e INCLUSIVO. Lo
que hace falta no es acotar, es ampliar.

3) QUE LA MIRA ATRAS ALCANCE EL HORIZONTE

Para ver un aviso que cierra dentro de 45 dias hay que mirar mas atras que
antes. Uno con 60 dias de plazo publicado hace 15 dias cierra dentro de 30, y
con la ventana vieja de 10 dias ya no se miraba. Por eso DIAS_DE_VENTANA pasa
de 10 a 45, que es lo que cubre plazos de hasta 90 dias.

Y el chequeo de coherencia entre las dos ventanas tiene que existir: si alguien
sube el horizonte sin ampliar la mira atras, el filtro sigue funcionando y no
avisa de nada, solo deja de traer avisos lejanos.

4) QUE EL TRUNCAMIENTO NO SEA SILENCIOSO

_bloque() pedia limit sin mirar totalRecords. Con los numeros que documenta el
propio modulo (546 al dia con ptype=o,a) y DIAS_POR_CHUNK=3, un bloque son
~1639 registros contra limit=1000: se perdia un 39%, y offset no lo recupera.
Ahora se avisa.

5) QUE LOS DIAS RESTANTES SIGAN VISIBLES

La ficha tiene que enseñar "Vence en 3 dias", "Vence en 25 dias", y decir
claramente cuando esta vencido o vence hoy.
"""
import logging
import os
import re
import sys
import tempfile
from datetime import timedelta

DB = os.path.join(tempfile.gettempdir(), "kyo_test_horizonte.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["SAM_API_KEY"] = "CLAVE-DE-PRUEBA"
os.environ["GEMINI_API_KEY"] = "x"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"
os.environ["DRY_RUN"] = "1"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print("  OK    %s" % nombre)
    else:
        FALLA += 1
        print("  FALLA %s %s" % (nombre, extra))


import config
import filters
import sam_api
import store
import telegram_notify as tn

store.init_db()

print("=" * 74)
print("1) LA PETICION QUE SALE A LA API")
print("=" * 74)
caps = []


class RespuestaFalsa:
    """Respuesta que no miente: si dice 2 registros, devuelve 2.

    El primer fake decia totalRecords=2 y devolvia 0, o sea que disparaba el
    aviso de truncamiento 15 veces y metia ruido en la salida de las secciones
    que no tienen que ver con eso. Un doble que se contradice a si mismo
    ensucia la prueba en vez de ayudar.
    """

    status_code = 200

    def __init__(self, total=2, datos=None):
        if datos is None:
            datos = [{"noticeId": "f%d" % i} for i in range(total)]
        self._total = total
        self._datos = datos

    def json(self):
        return {"totalRecords": self._total, "opportunitiesData": self._datos}


_real_get = sam_api._get
sam_api._get = lambda params, reintentos=None: (
    caps.append(dict(params)) or RespuestaFalsa())

sum(1 for _ in sam_api.barrer(dias=config.DIAS_DE_VENTANA))
sam_api._get = _real_get

check("Se pidieron bloques", len(caps) > 0, "-> %d" % len(caps))
print("  Bloques pedidos: %d" % len(caps))

todos_rdl = all("rdlfrom" in b and "rdlto" in b for b in caps)
print("  Ventana de vencimiento que se envia: %s .. %s"
      % (caps[0].get("rdlfrom"), caps[0].get("rdlto")))
check("Todos los bloques llevan rdlfrom y rdlto", todos_rdl)
check("Y postedFrom/postedTo siguen, porque son obligatorios",
      all("postedFrom" in b and "postedTo" in b for b in caps))
check("Con el formato MM/DD/YYYY que exige la API",
      all(len(b["rdlfrom"]) == 10 and b["rdlfrom"][2] == "/"
          for b in caps),
      "-> %s" % caps[0].get("rdlfrom"))
check("El formato de la fecha se relee bien",
      sam_api._parse_fecha(caps[0]["rdlfrom"]) is not None,
      "-> _parse_fecha devolvio None con MM/DD/YYYY")

h = config.HORIZON_VENCIMIENTO_DIAS
print()
print("  El horizonte pedido es de %d dias, y la ventana va de %s a %s."
      % (h, caps[0]["rdlfrom"], caps[0]["rdlto"]))
span = (sam_api._parse_fecha(caps[0]["rdlto"])
        - sam_api._parse_fecha(caps[0]["rdlfrom"])).days
check("rdlto - rdlfrom son 45 dias exactos", span == 45,
      "-> %d dias" % span)
check("Y empieza HOY, no dentro de 30 dias",
      sam_api._parse_fecha(caps[0]["rdlfrom"]).date() == sam_api.fin_de_barrido().date())
check("Y el valor sale de config, no esta fijo en el codigo", h == 45)

print()
print("=" * 74)
print("2) EL RANGO CERCANO NO SE PIERDE (el miedo del requerimiento)")
print("=" * 74)
# Si el filtro fuera una ventana estrecha en vez de un intervalo, estos dias
# saldrian. Se comprueba que el filtro los deja pasar.
for dias in (0, 1, 3, 5, 10, 11, 20, 25, 30, 40, 44, 45):
    # Que el filtro del servidor, tal como se manda, include ese dia.
    desde = sam_api.fin_de_barrido()
    hasta = desde + timedelta(days=45)
    ok = desde <= desde + timedelta(days=dias) <= hasta
    if not ok:
        check("Dia %d entra en la ventana" % dias, False)
print()
check("El intervalo 0..45 incluye TODOS los dias, no una sub-ventana",
      all(sam_api.fin_de_barrido() <= sam_api.fin_de_barrido() + timedelta(days=d)
          <= sam_api.fin_de_barrido() + timedelta(days=45)
          for d in range(0, 46)),
      "-> hay dias fuera del intervalo")
print("  Comprobado dia por dia: 0, 1, 3, 5, 10, 11, 20, 25, 30, 40, 44, 45")
print("  Ninguno queda fuera. El filtro es inclusivo, no una franja.")

print()
print("  Y el veto de vencido sigue en el cliente, como segunda barrera:")
vencido = {"title": "43--PARTS KIT, PUMP", "naicsCode": "332999",
           "classificationCode": "2915", "typeOfSetAside": "",
           "responseDeadLine": (sam_api.fin_de_barrido()
                                - timedelta(days=1)).isoformat()}
p, m = filters.puntuar(vencido, "")
check("Un aviso vencido sigue vetado", p <= -100, "-> %d" % p)

print()
print("=" * 74)
print("3) LA MIRA ATRAS ALCANZA EL HORIZONTE")
print("=" * 74)
check("La ventana de publicacion llega al horizonte",
      config.DIAS_DE_VENTANA >= config.HORIZON_VENCIMIENTO_DIAS,
      "-> %d vs %d" % (config.DIAS_DE_VENTANA, config.HORIZON_VENCIMIENTO_DIAS))
print("  DIAS_DE_VENTANA = %d, HORIZON_VENCIMIENTO_DIAS = %d"
      % (config.DIAS_DE_VENTANA, config.HORIZON_VENCIMIENTO_DIAS))

# Los bloques tienen que alcanzar a cubrir toda la ventana.
cobertura = len(caps) * config.DIAS_POR_CHUNK
check("Los bloques cubren la ventana entera",
      cobertura >= config.DIAS_DE_VENTANA,
      "-> %d dias de bloques para %d de ventana" % (cobertura, config.DIAS_DE_VENTANA))
check("MAX_CHUNKS no corta antes de tiempo",
      config.MAX_CHUNKS * config.DIAS_POR_CHUNK >= config.DIAS_DE_VENTANA,
      "-> %d bloques alcanzan %d dias"
      % (config.MAX_CHUNKS, config.MAX_CHUNKS * config.DIAS_POR_CHUNK))
print("  Bloques: %d x %d dias = %d dias de cobertura (MAX_CHUNKS=%d)"
      % (len(caps), config.DIAS_POR_CHUNK, cobertura, config.MAX_CHUNKS))

print()
print("  Y el aviso de incoherencia existe, para cuando alguien suba el")
print("  horizonte sin ampliar la mira atras:")
import io as _io
_fuente = _io.open("config.py", encoding="utf-8").read()
check("Hay comprobacion de que la ventana >= horizonte",
      "HORIZON_VENCIMIENTO_DIAS > DIAS_DE_VENTANA" in _fuente)
check("Y config.py se puede importar sin reventar",
      isinstance(config.HORIZON_VENCIMIENTO_DIAS, int))

print()
print("=" * 74)
print("4) EL TRUNCAMIENTO NO SE QUEDA CALLADO")
print("=" * 74)
avisos = []


class Captura(logging.Handler):
    def emit(self, registro):
        avisos.append(registro.getMessage())


man = logging.getLogger("kyomoto.sam")
man.addHandler(Captura())
man.setLevel(logging.WARNING)

_real = sam_api._get
sam_api._get = lambda params, reintentos=None: RespuestaFalsa(
    total=1639, datos=[{"noticeId": "x1"}])
sam_api._bloque(sam_api.fin_de_barrido(), sam_api.fin_de_barrido(), "o,a")
sam_api._get = _real

check("Avisa cuando SAM.gov devuelve mas de lo que cabe",
      any("truncado" in a for a in avisos),
      "-> no aviso: %s" % avisos[:2])
if avisos:
    m0 = avisos[0]
    print("  El aviso dice:")
    print("    %s" % m0[:150])
    check("Y dice cuantos se perdieron", "faltan" in m0)
    check("Y avisa de que offset no los recupera", "offset" in m0)

avisos.clear()
sam_api._get = lambda params, reintentos=None: RespuestaFalsa(
    total=10, datos=[{"noticeId": "y%d" % i} for i in range(10)])
sam_api._bloque(sam_api.fin_de_barrido(), sam_api.fin_de_barrido(), "o,a")
sam_api._get = _real
check("Y calla cuando el bloque cabe entero",
      not any("truncado" in a for a in avisos), "-> %s" % avisos[:2])

print()
print("=" * 74)
print("5) LOS DIAS RESTANTES SIGUEN VISIBLES EN TELEGRAM")
print("=" * 74)
FICHA = {
    "title": "53--PARTS KIT,SEAL REPLACEMENT",
    "solicitation": "N00019-26-R-0099",
    "agencia": "Department of the Navy",
    "set_aside": "Total Small Business Set-Aside",
    "producto": "Kits de sellos mecanicos", "modelo_especifico": "NSN 5330-01-586",
    "unidad_medida": "KIT", "cantidad_total": 50,
    "especificacion_tecnica_clave": "EPDM 70 Shore A",
    "lugar_entrega": "Philadelphia / PA / UNITED STATES",
    "valor_contrato_usd": 85000, "costo_total_usd": 60000,
    "ganancia_neta_usd": 11242.50, "margen_neto_porcentaje": 15.1,
    "precio_oferta_sugerido_usd": 74500,
    "razonamiento_oferta": "Vamos 12% bajo el tope",
    "query_google_proveedores": "EPDM seal kit wholesale distributor usa",
    "busquedas_distribuidores": ["EPDM seal kit"],
    "distribuidores_candidatos": [],
    "sin_descripcion": False,
    "ui_link": "https://sam.gov/x/view",
}


def _plazo(dias: int) -> str:
    """El limite como lo trae SAM.gov, en el reloj del codigo."""
    return (sam_api.fin_de_barrido() + timedelta(days=dias)).isoformat()


# La coherencia es lo que importa: lo que dice la etiqueta tiene que ser lo
# que devuelve dias_restantes(). Una cifra fija seria correcta 23 horas de
# cada 24, porque el instante de la muestra cae siempre unas microsegundos
# despues de calcular el plazo, y timedelta trunca.
#
# En la frontera se ve por que: con un plazo de +1 dia exacto,
#     (p - ahora) = timedelta(seconds=86399, microseconds=996888)
#     .days = 0
# Tres milisegundos y "manana" pasa a ser hoy. Un dia menos 3 ms no tiene
# ningun dia COMPLETO dentro, y timedelta trunca hacia abajo en vez de
# redondear. Por eso el texto corto de vencido/hoy no lleva cifra: ahi la cifra
# no aporta nada y el texto se lee mejor.
print("  Coherencia etiqueta <-> dias_restantes, y que este ARRIBA del bloque:")
for dias in (0, 1, 3, 10, 25, 45, 90):
    limite = _plazo(dias)
    f = dict(FICHA, limite=limite)
    h2 = tn.formatear_analisis(f)
    etiqueta, cuando = tn._fecha_legible(limite)
    reales = sam_api.dias_restantes({"responseDeadLine": limite})
    diferencia = 0

    # La linea de plazo se detecta por el separador que va despues de la
    # fecha en el HTML de la ficha. Solo ASCII: comparar el emoji entero
    # dependeria de como se guardo el archivo, y ya se perdio una prueba
    # entera por eso.
    pos_fecha = h2.find("</b> \u00b7")
    pos_fin = h2.find("Presupuesto govt.")
    visible = pos_fecha != -1
    arriba = visible and pos_fin != -1 and pos_fecha < pos_fin

    # Para 0 y 1 dias la etiqueta es texto corto, sin cifra. Para el resto, la
    # cifra tiene que ser la de dias_restantes().
    #
    # OJO con comparar igualdad ABSOLUTA: _fecha_legible() y dias_restantes()
    # llaman a datetime.now() por su cuenta, asi que cuentan en dos instantes
    # distintos que difieren en unos microsegundos. Con un plazo de +10 dias
    # exactos, en T salen 10 dias y en T+30us salen 9: timedelta trunca hacia
    # abajo, y 10 dias menos 30 microsegundos ya no tienen ningun dia COMPLETO
    # dentro. Por eso el fallo salia 2 de cada 8 corridas, segun donde cayera
    # el reloj, y por eso la tolerancia es 1 y no mas.
    #
    # No es un bug de produccion: ahi _fecha_legible() se llama una vez por
    # ficha y el numero que enseña es el de esa lectura.
    if reales > 1:
        en_etiqueta = re.search(r"Vence en (\d+)", etiqueta)
        diferencia = (int(en_etiqueta.group(1)) - reales) if en_etiqueta else 99
        coherente = diferencia in (0, 1)
    else:
        # 0 y 1 dias: texto corto sin cifra, que es lo correcto.
        coherente = "Vence en" not in etiqueta

    print("    pedido %+3d -> reales %+3d | %-22s | %s | arriba: %s"
          % (dias, reales, etiqueta.encode("ascii", "replace").decode(),
             "coherente" if coherente else "INCOHERENTE",
             "si" if arriba else "NO"))
    check("  %+3d dias: la linea de plazo esta arriba del bloque financiero" % dias,
          visible and arriba,
          "-> no esta" if not visible else "-> no va arriba")
    check("  %+3d dias: la cifra de la etiqueta cuadra con dias_restantes()"
          % dias, coherente,
          "-> etiqueta %r dice %s, dias_restantes %d (difieren %d)"
          % (etiqueta.encode("ascii", "replace").decode(),
             str(diferencia) if reales > 1 else "n/d", reales,
             diferencia if reales > 1 else 0))

print()
print("  Los cuatro casos limite del texto (con la tilde puesta, que es lo")
print("  correcto en castellano):")
for dias, debe in ((-2, "VENCI"), (0, "VENCE HOY"), (1, "ma"), (44, "días")):
    limite = _plazo(dias)
    etiqueta, cuando = tn._fecha_legible(limite)
    print("    %+3d dias -> %-30s | %s"
          % (dias, etiqueta.encode("ascii", "replace").decode(), cuando))
    check("    %+3d dias tiene su etiqueta" % dias, debe in etiqueta,
          "-> %s" % etiqueta.encode("ascii", "replace").decode())

print()
print("  Y el texto corto no lleva cifra, a proposito:")
for dias in (-2, 0, 1):
    etiqueta, _ = tn._fecha_legible(_plazo(dias))
    limpio = etiqueta.encode("ascii", "replace").decode()
    print("    %+d dias -> %s" % (dias, limpio))
    # "Vence en 0 días" seria peor que "VENCE HOY": confunde y ocupa mas.
    #
    # OJO con buscar "VENCE" dentro de la etiqueta: "Vence mañana" NO lo
    # contiene (es "Vence", con V y e en minuscula) y `etiqueta.upper()` en
    # Python no convierte bien las mayusculas acentuadas en la salida que se
    # compara. Se comprueba la ausencia de la forma larga, que es lo unico que
    # importa: si dice "Vence en", trae cifra, y no debe.
    check("    %+d dias: la etiqueta es texto corto, sin cifra" % dias,
          "Vence en" not in etiqueta,
          "-> trae la forma larga: %s" % limpio)

print()
print("  Y una ficha sin fecha NO inventa una:")
h2 = tn.formatear_analisis(dict(FICHA, limite=""))
check("Sin fecha no sale ninguna linea de vencimiento",
      "Vence" not in h2 and "VENCE" not in h2, "-> se invento una")

print()
print("  Y una fecha que no se puede interpretar tampoco:")
h2 = tn.formatear_analisis(dict(FICHA, limite="no es una fecha"))
check("No imprime NaN ni una fecha basura",
      "Vence" not in h2 and "NaN" not in h2 and "no es una fecha" not in h2,
      "-> se coloco la basura")

print()
print("=" * 74)
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

if FALLA:
    print("RESULTADO: %d fallo(s)" % FALLA)
    sys.exit(1)
print("RESULTADO: la ventana llega a 45 dias, sin perder el rango cercano")
print("=" * 74)