"""
Kyomoto - Configuracion central.
Todas las claves se leen de variables de entorno. Nunca se hardcodean.
"""
import os

# Se muestra en /selftest y /estado para saber que codigo esta
# corriendo en Render. Sube la version cuando cambies algo importante.
# 2.9.0 = orientacion COTS Easy-Supply: veto de alta complejidad y defensa
# (plataformas, armamento, municion, combustible de aviacion,
# certificaciones de origen ITAR/JCP/DD250/CoC), bonus para las familias PSC
# con distribuidor comercial en USA, y meta-prompt de facilidad de
# comercializacion en Gemini.
# 2.8.0 = la Estrategia de Oferta la calcula Python con las cifras finales
# en vez de redactarla Gemini, y el prompt deja de pedirle que la escriba.
# 2.7.1 = gemini-3.6-flash-lite entra en la cadena donde estaba
# gemini-3.5-flash, en render.yaml y .env.example tambien.
# 2.7.0 = horizonte de vencimiento a 45 dias con rdlfrom/rdlto (filtro del
# servidor de SAM.gov, que no se estaba usando), ventana de publicacion a 45
# dias para poder alcanzarlos, y aviso de truncamiento por totalRecords.
# 2.6.1 = &nbsp; fuera del resumen de barrido (Telegram lo rechazaba entero),
# atajo para no pedir descripcion que ya vino, y PTYPE_SAM en config.
# 2.6.0 = el barrido automatico entrega de verdad (el destino salia como la
# cadena "None"), ficha en formato ejecutivo y 3 distribuidores con su
# verificacion en un clic.
# 2.5.0 = penalizacion de mano de obra en sitio (-3) y bonus de set-aside de
# small business (+2), sobre la base de los botones, el PSC y el PDF.
# 2.4.0 = los botones del menu vuelven a responder (allowed_updates no
# traia callback_query, asi que Telegram nunca entregaba las pulsaciones),
# PSC permisivo (cualquier codigo de 4 digitos cuenta como producto), y el
# Purchase Order en PDF con el comando /pdf.
KYOMOTO_VERSION = "2.9.0"


def _bool(nombre: str, por_defecto: bool = False) -> bool:
    return os.getenv(nombre, "1" if por_defecto else "0").strip().lower() in ("1", "true", "yes", "si", "s")


def _int(nombre: str, por_defecto: int) -> int:
    try:
        return int(os.getenv(nombre, "").strip() or por_defecto)
    except ValueError:
        return por_defecto


def _float(nombre: str, por_defecto: float) -> float:
    try:
        return float(os.getenv(nombre, "").strip() or por_defecto)
    except ValueError:
        return por_defecto


# --- Credenciales ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SAM_API_KEY = os.getenv("SAM_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Sondeo del 28-sep-2026, dos veces en el mismo dia:
#
#   00:20 Chile   3.8 OK 5.1s | 3.7 503 | 3.6 OK 4.2s | 3.5 26.1s
#                 3.5-lite OK 0.7s | flash-latest OK 8.3s | flash-lite NO EXISTE
#   09:00 Chile   3.8 503 | 3.7 503 | 3.6 OK 4.1s | 3.5 503
#                 3.5-lite 503 | flash-latest 503
#
# O sea: la saturacion cambia por completo en nueve horas y puede dejar 5 de 6
# modelos caidos a la vez. Por eso la lista de respaldo incluye TODOS los
# conocidos, no solo tres: con 5 caidos, tres no alcanzaban.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# Ante un 503 o un 429 Kyomoto prueba el siguiente de esta lista en vez de
# rendirse. La saturacion es por modelo y la cuota tambien: medido el
# 29-sep-2026, con 3.8-flash y flash-latest sin cuota mientras 3.5-flash-lite
# respondia sin problema.
#
# gemini-3.5-flash-lite VUELVE a la lista. Antes se salio porque con la config
# real del escaneo daba 400 INVALID_ARGUMENT; se descubrio que la culpa era
# thinking_budget=0 y que demas lo acepta todo. Como el 400 ahora reintenta sin
# ese campo, el modelo entra. Y hacia falta: a las 01:35 del Pacifico de ese
# dia era el UNICO con cuota libre, y sin el la cadena entera se quedaba sin
# nada que probar.
#
# Va primero porque es el mas rapido (0,7s medidos) y el que menos se satura:
# casi nadie lo usa, asi que su cuota aguanta mas.
#
# gemini-3.6-flash-lite entra el 10-oct-2026 donde estaba gemini-3.5-flash, con
# el principal ya en gemini-3.6-flash. OJO CON POR QUE NO SE RENOMBRO A SECO:
#
# "gemini-3.5-flash" esta DENTRO de "gemini-3.5-flash-lite" como subcadena, asi
# que un reemplazo de texto plano convertia tambien el -lite de la primera
# posicion. Ese es el mas rapido y el que menos se satura, y fue el UNICO con
# cuota libre durante el corte del 29-sep. No se toca.
#
# Y tampoco vale con cambiar solo el 3.5-flash suelto por gemini-3.6-flash: el
# principal ya es ese, asi que el hueco habria reintentado el mismo modelo que
# acababa de dar 429. Como la cuota es por modelo y por dia, ese reintento
# vuelve a fallar siempre: hueco inutil. Con 3.6-flash-lite es un modelo
# DISTINTO, o sea cobertura de verdad.
#
# Si 3.6-flash-lite no existiera para esta cuenta, el 404 lo salta solo y la
# cadena sigue con el siguiente (gemini_analyzer.py lo hace, y deja el nombre
# en la lista de incompatibles). No rompe el barrido.
#
# Para comprobar que responde y que cuota tiene:
#     python sondear_modelos.py
GEMINI_MODELES_ALTERNATIVOS = os.getenv(
    "GEMINI_MODELES_ALTERNATIVOS",
    "gemini-3.5-flash-lite,gemini-3.8-flash,gemini-flash-latest,"
    "gemini-3.6-flash-lite,gemini-3.7-flash",
).replace(" ", "").split(",")

# --- Ventana de busqueda en SAM.gov ---
# 10 dias: ademas de los avisos nuevos, entra todo lo que se publico durante
# el fin de semana y el lunes siguiente. Los sabados y domingos se publica
# muy poco, asi que con una ventana corta Kyomoto se quedaria sin nada que
# mostrarte. El filtro de fecha limite ya descarta los que se cerraron.
# --- Horizonte de vencimiento (4-oct-2026) -------------------------------
#
# Cuanto tiempo hacia adelante se buscan avisos que aun se puedan ofertar.
# Kyomoto lo pasa a la API como rdlfrom/rdlto, que es un filtro DEL SERVIDOR:
# antes se pedian todos los avisos de la ventana y se descartaban en el
# cliente. Ahora es SAM.gov el que devuelve solo los que cierran en plazo.
#
# 45 dias, y no mas, por dos razones. La primera es que la cuenta no da para
# mas: mas plazo significa mirar mas hacia atras (ver DIAS_DE_VENTANA mas
# abajo) y cada dia de mas son bloques enteros de peticiones contra un tope
# diario que ya se agota. La segunda es que pasado cierto plazo la
# oportunidad ya no sirve: un aviso que cierra dentro de dos meses casi nunca
# se gana sin trabajo previo, y aqui no hay tiempo para ese trabajo.
#
# Poner 0 desactiva el filtro de vencimiento y deja el comportamiento
# anterior, que es filtrar en el cliente. Se deja el valor a mano para poder
# comparar, no porque sea lo mejor.
HORIZON_VENCIMIENTO_DIAS = _int("HORIZON_VENCIMIENTO_DIAS", 45)

# --- Ventana de publicacion: tiene que ALCANZAR el horizonte -------------
#
# Antes era 10 dias. Con 10 no se ven los avisos de plazo largo: uno con 60
# dias de plazo publicado hace 15 dias cierra dentro de 30, o sea dentro del
# horizonte, y Kyomoto no lo miraba porque ya habia salido de la ventana.
#
# 45 dias de mira atras cubren plazos de respuesta de hasta 90, que es lo
# mas largo que se ve en licitaciones complejas. Es la misma cifra que el
# horizonte por una razon: si el plazo maximo es P, un aviso cierra dentro de
# HORIZON cuando se publico no antes de hoy-(P-HORIZON), y con P=90 y
# HORIZON=45 eso son 45 dias atras.
#
# OJO, esto NO acorta nada. El filtro de vencimiento es INCLUSIVO: con 45 dias
# siguen entrando los avisos que cierran en 1, en 3, en 10, en 25 y en 45. El
# miedo de quedarse solo con la cola lejana no ocurre: rdldesde=hoy y
# rdlhasta=hoy+45 incluyen todo el intervalo.
DIAS_DE_VENTANA = _int("DIAS_DE_VENTANA", 45)

LIMITE_POR_CHUNK = _int("LIMITE_POR_CHUNK", 1000)
DIAS_POR_CHUNK = _int("DIAS_POR_CHUNK", 3)

# Antes 12. Con DIAS_POR_CHUNK=3, 12 bloques solo llegaban a 36 dias de
# ventana, o sea menos de la que ahora se mira. 20 da margen.
# OJO: es un tope de peticiones de UNA SOLA VEZ, no por barrido. Despues del
# primer llenado de cache, esa parte sale de SQLite sin gastar nada. El
# coste de ampliar la ventana a 45 dias se paga una vez, no cada 2 horas.
MAX_CHUNKS = _int("MAX_CHUNKS", 20)

# Coherencia entre las dos ventanas. Se comprueba en vez de suponer: si el
# horizonte se sube sin ampliar la mira atras, el filtro de vencimiento se
# queda sin avisos a los que llegar en la cola, y no hay forma de verlo en los
# numeros del barrido, porque el filtro funciona: simplemente nunca trae nada
# de lejos. Sale en el arranque y dice que hacer.
if HORIZON_VENCIMIENTO_DIAS > DIAS_DE_VENTANA:
    import sys as _sys
    print("[config] AVISO: HORIZON_VENCIMIENTO_DIAS=%d es mayor que "
          "DIAS_DE_VENTANA=%d. No se podran ver los avisos de plazo largo "
          "que cierran dentro del horizonte."
          % (HORIZON_VENCIMIENTO_DIAS, DIAS_DE_VENTANA), file=_sys.stderr)

# Dias de la ventana que SIEMPRE se piden a la API, porque ahi es donde
# aparecen los avisos nuevos. El resto sale de la cache (SQLite).
# Con 2 dias vivos y ventana de 10, solo se piden 1 bloque por barrido.
# OJO: esto NO es un TTL. Un TTL tiene que ser mayor que INTERVALO_HORAS para
# servir de algo; si es menor, la cache nunca se usa (que fue el bug que
# hubo en la primera version de esto).
DIAS_VIVOS = _int("DIAS_VIVOS", 3)

# Objetivo del usuario: 10 licitaciones VIABLES al dia. Como no todas las
# analizadas sobreviven, se piden mas de las que quieres recibir.
PRESUPUESTO_GEMINI_DIARIO = _int("PRESUPUESTO_GEMINI_DIARIO", 20)
# Suelo del auto-ajuste: nunca baja de 10, asi siempre hay 10 analisis.
PRESUPUESTO_MINIMO = _int("PRESUPUESTO_MINIMO", 10)
# Cuantas por barrido. quota.por_barrido() reparte el presupuesto diario
# entre los barridos: con 4 barridos y 16 de presupuesto, 4 por barrido.
MAX_A_GEMINI = _int("MAX_A_GEMINI", 4)
# Piso de puntaje. Subirlo deja solo lo mas limpio a costa de revisar menos.
PUNTAJE_MINIMO = _int("PUNTAJE_MINIMO", 6)
MAX_DESCRIPCIONES = _int("MAX_DESCRIPCIONES", 40)
# Tope duro de avisos que te llegan por barrido, ordenados por puntaje.
# Esto es lo que hace que recibas 5-10 buenos y no 25 mediocres.
MAX_NOTIFICACIONES = _int("MAX_NOTIFICACIONES", 10)

# --- Reglas de negocio ---
TOPE_USD = _float("TOPE_USD", 250000.0)
MIN_USD = _float("MIN_USD", 5000.0)
DIAS_MINIMO_PARA_POSTULAR = _int("DIAS_MINIMO_PARA_POSTULAR", 7)

# --- Modelo economico del usuario (empresa unipersonal en Chile) ---
#
# El capital NO es un problema: se resuelve con factoring dentro de USA.
# Ese factoring cobra un 3-4% del valor del contrato, asi que el margen que
# importa no es el bruto sino el neto despues de factoring.
#
#   margen bruto  15% - 35%  (lo que se gana comprando y revendiendo)
#   factoring     3%  - 4%   (lo que se paga por cobrar antes)
#   margen neto   11% - 31%
#
# Kyomoto no debe recomendar una oferta que deje menos de esto, porque en la
# practica es una oferta que no vale la pena.
FACTORING_PCT = _float("FACTORING_PCT", 0.035)
MARGEN_BRUTO_MIN = _float("MARGEN_BRUTO_MIN", 0.15)
MARGEN_BRUTO_MAX = _float("MARGEN_BRUTO_MAX", 0.35)
MARGEN_NETO_MIN = _float("MARGEN_NETO_MIN", 0.12)
# Colchon para imprevistos al calcular el margen objetivo.
MARGEN_COLCHON = _float("MARGEN_COLCHON", 0.15)

# --- Rendimiento / Costos ---
# WORKERS controla las descargas de descripciones (HTTP barato a SAM.gov).
WORKERS = _int("WORKERS", 8)

# Gemini tiene su propio limite: el plan gratis es muy bajo (429
# RESOURCE_EXHAUSTED). Por eso va aparte y con pausas. Subir estos numeros
# sin subir de plan es la causa numero uno de que el escaneo se caiga a mitad.
GEMINI_WORKERS = _int("GEMINI_WORKERS", 1)
GEMINI_PAUSA_SEG = _float("GEMINI_PAUSA_SEG", 8.0)
# Ante un 429 no se reintenta a lo loco: se deja el aviso para el proximo
# barrido. Reintentar solo consume mas cuota.
GEMINI_REINTENTOS_429 = _int("GEMINI_REINTENTOS_429", 1)

GEMINI_TEMPERATURE = _float("GEMINI_TEMPERATURE", 0.2)
GEMINI_THINKING_BUDGET = _int("GEMINI_THINKING_BUDGET", 0)
# Espera ante un 503 antes de cambiar de modelo. Lasweapon documented
# documentan los picos como temporales, pero en la practica duran minutos,
# no segundos. Cambiar de modelo es mas rapido que esperar.
GEMINI_ESPERA_503 = _float("GEMINI_ESPERA_503", 20.0)
# El plan gratis de Gemini es muy justo de facto: cada llamada cuenta. Cuanto mas
# corto el prompt y mas corta la salida, mas avisos entran por barrido.
# 3000 caracteres de descripcion basta para decidir y para estimar precio;
# pedir mas solo quema cuota.
MAX_CHARS_DESCRIPCION = _int("MAX_CHARS_DESCRIPCION", 3000)
GEMINI_MAX_OUTPUT_TOKENS = _int("GEMINI_MAX_OUTPUT_TOKENS", 2048)
TIMEOUT_HTTP = _int("TIMEOUT_HTTP", 45)
MAX_REINTENTOS = _int("MAX_REINTENTOS", 3)

# --- Operacion ---
# Dos barridos al dia. Cada uno gasta como 1/12 del presupuesto diario, asi
# que la cuota se reparte sin que un fallo te deje medio dia sin nada.
INTERVALO_HORAS = _float("INTERVALO_HORAS", 2)
DB_PATH = os.getenv("DB_PATH", "licitaciones.db")
DRY_RUN = _bool("DRY_RUN", False)  # True = no envia mensajes, solo loguea
ETIQUETAS_DIR = os.getenv("ETIQUETAS_DIR", "etiquetas")

PORT = _int("PORT", 10000)


# ==========================================================================
#  CLASIFICACION NAICS
#
# --- Que tipos de aviso se piden a la API ------------------------------
#
# ptype es el parametro que decide que clases de aviso trae /v2/search. Kyomoto
# pide estos desde el 30-sep-2026, por medicion y no por suposicion:
#
#   "o,a"  (lo que hay ahora)
#       o = Solicitation. Es lo ofertable: ahi se presenta una propuesta.
#       a = Award Notice. El contrato YA esta adjudicado. No se puede ofertar.
#       Volumen medido: 7647 avisos en 14 dias.
#
#   "o"    (solo solicitaciones)
#       o = Solicitation. 2083 avisos en 14 dias. Un 73% menos de volumen.
#
# POR QUE EL VALOR POR DEFECTO SIGUE SIENDO "o,a"
#
# Se pidio dejar los award fuera. El ahorro es real: 5564 peticiones menos en
# la misma ventana, contra un tope diario que ya se agota. Y un award
# adjudicado no es algo a lo que uno se pueda presentar, o sea que quitarlos
# tampoco quita nada ofertable.
#
# En contra hay algo que pesa mas que el ahorro: los award dicen QUE compra el
# gobierno de verdad, con NSN, cantidades y precios unitarios. Algunos ya
# pasaron el filtro antes de esto, entre ellos "Award Notice-1ID H2F Office
# Furniture and Equipment" con puntuacion 10 de 12. Y el cuello de botella que
# de verdad duele no es el volumen de avisos: es que todavia no ha llegado ni
# una sola ficha de punta a punta, porque el embudo se queda sin texto antes
# de llegar a Gemini. Reducir el embudo a ciegas, con el embudo ya en cero,
# hace mas facil que no llegue nunca.
#
# QUE HACER: dejar esto en "o" y mandar /puntajes tras el primer barrido. Si
# siguen saliendo avisos de producto con puntuacion alta, se queda en "o". Si
# el ahorro de peticiones compensa que aparezcan menos, se queda en "o,a".
# Es una linea, no un deshacer commit.
#
# Los codigos de SAM.gov, por si hace falta combinarlos:
#   o = Solicitation                     a = Award Notice
#   k = Cancelled solicitation           b = Combined synopsis award
#   c = Combined synopsis (other)        g = Notice of intent to award
#   i = Industry                         p = Pre-solicitation
#   u = Sources Sought
# Los tres "combined" que pide el requerimiento son k, b y c, que se piden
# juntos como "k,b,c". Ojo: hoy no entran, y no se anaden porque cambian lo
# que el bot ve. Se dejan aqui anotados para cuando se decida medido.
PTYPE_SAM = "o,a"

#  Datos reales medidos en SAM.gov (muestra de 14 dias, ptype=o, 1000 avisos):
#    541 (IT / servicios)            -> 15%   EXCLUIR
#    236/237/238 (construccion)       -> 18%   EXCLUIR
#    811 (reparacion y mantenimiento) ->  3%   EXCLUIR
#    311-339 (manufactura)            -> 51%   BIENES
#    421-425 / 441-454 (comercio)     ->  7%   BIENES
#  Naics_SERVICIOS gana siempre sobre NAICS_BIENES.
# ==========================================================================

# Prefijos NAICS de 3 digitos que SI son bienes / producto fisico.
NAICS_BIENES = {
    # --- Industria manufacturera ---
    "311", "312", "313", "314", "315", "316", "321", "322", "323",
    "325", "326", "327", "331", "332", "333", "334", "335", "336",
    "337", "339",
    # --- Mayorista ---
    "421", "422", "423", "424", "425",
    # --- Minorista ---
    "441", "442", "443", "444", "445", "446", "447",
    "451", "452", "453", "454",
}

# Prefijos NAICS de 3 digitos que son servicios, obra o alquiler. GANA sobre la lista de bienes.
NAICS_SERVICIOS = {
    # Agricultura, mineria, services publicos
    "111", "112", "113", "114", "115", "116",
    "211", "212", "213", "221", "222", "223",
    # Construccion
    "236", "237", "238",
    # Transporte
    "481", "482", "483", "484", "485", "486", "487", "488",
    "491", "492", "493",
    # Informacion, finanzas, seguros
    "511", "512", "513", "519", "521", "522", "523", "524", "525",
    # Inmuebles y alquiler
    "531", "532", "533",
    # Servicios profesionales y TI
    "541", "542",
    # Servicios a empresas
    "551", "552", "553", "554", "555", "556", "561", "562", "563",
    # Salud y educacion
    "611", "612", "613", "614", "615", "616", "617", "618", "619",
    "621", "622", "623", "624", "625", "626",
    # Ocio, alojamiento, alimentacion
    "711", "712", "713", "721", "722", "723", "724", "725",
    # Reparacion y servicios personales
    "811", "812", "813", "814", "815", "816", "817",
    # Administracion publica
    "921", "922", "923", "926", "927", "928", "931", "932", "941", "942",
}

# --- Set-aside: pequeño negocio estadounidense (30-sep-2026) ---
#
# L.A.M.B. Logistics LLC es una empresa unipersonal en Albuquerque, New
# Mexico. Es decir, ES una small business de EE.UU. Un "Total Small Business
# Set-Aside" no es solo una pista de que la oportunidad es buena: es la
# confirmacion de que la empresa PUEDE presentarse, y contra quien compite.
# Por eso vale +2 y no +1.
#
# Los set-asides socioeconómicos (WOSB, EDWOSB, HBC, VOSBC, SDVOSBC) también
# valen +2, pero ADVIERTEN: exigen certificación de propiedad. Si L.A.M.B.
# Si L.A.M.B. consigue alguna de esas certificaciones, el mismo contrato
# se presenta ahi con el mismo +2 y con menos competencia.
# ahi con el mismo +2 y la competencia es menor.
#
# Se comparan por palabra y no por igualdad exacta a propósito. SAM.gov manda
# el valor completo ("Total Small Business Set-Aside"), que no es igual a
# "TOTAL": con igualdad se escapaba justo el set-aside que mas le conviene a
# una PYME.
SET_ASIDE_PYME = {
    "TOTAL", "TOTAL SMALL BUSINESS", "SBA", "SBS", "TOTAL SMALL",
    "WOSB", "EDWOSB", "HBC", "SDVOSBC", "VOSBC", "SDOB",
    "SMALL BUSINESS", "TOTAL SMALL BUSINESS SET-ASIDE",
    "EDWOSB 927", "WOSB 927", "HBC 927", "SDVOSBC 927", "VOSBC 927",
}
SET_ASIDE_PYME_PUNTOS = 2

# Set-asides donde la empresa tiene que tener certificacion de propiedad.
SET_ASIDE_CON_CERTIFICACION = {
    "WOSB", "EDWOSB", "HBC", "SDVOSBC", "VOSBC", "SDOB", "927",
}

# --- Penalizacion de mano de obra en sitio (30-sep-2026) ---
#
# Equipo pesado: sin esto, un "REPLACEMENT PARTS" de una valvula no se
#penaliza nunca, que es lo correcto.
EQUIPO_PESADO = (
    "generator", "genset", "hvac", "chiller", "boiler", "chilled water",
    "rooftop", "air handler", "cooling tower", "heating plant",
    "electrical generator", "power plant", "pump station",
)

# Trabajo en sitio. NO esta "replacement" a proposito: medirlo sobre los
# titulos reales del barrido del 29-sep mostro que "replacement parts" y
# "parts kit, replacement" son el nucleo del negocio, no mano de obra.
TRABAJO_EN_SITIO = (
    "installation", "install and", "furnish and install", "install,", "installed",
    "repair", "repair of", "maintenance", "servicing", "overhaul",
    "dismantle", "erect", "startup and commissioning", "on-site", "on site",
)

# Si el titulo dice esto, es una VENTA DE PIEZAS, no una obra: no se
# penaliza aunque lleve equipo pesado. Aqui es donde se pierden los kits de
# repuesto de generador, que son de las mejores oportunidades que existen.
VENTA_DE_PIEZAS = (
    "parts kit", "replacement parts", "spare parts", "spare parts kit",
    "kit,", " kits", "kit ", "parts list", "parts package", "nsn",
)

PENALIZACION_OBRA_PUNTOS = 3

# Clasificacion PSC (Product and Service Classification), 4 digitos.
#
# ANTES: una lista blanca de ~25 codigos. Solo esos sumaban, y un PSC valido
# fuera de la lista no.contribuia en nada. Ejemplos que se perdian:
#   2915 maquinaria y partes navales   <- piezas de repuesto, el nucleo
#   4520 materiales de construccion
#   6010 cableado electrico
#   8125 relleno de aislamiento
#
# AHORA: PSC_BIENES se mantiene como lista de los codigos mas frecuentes, que
# sirve para el detalle del log, pero el filtro acepta CUALQUIER codigo de 4
# digitos valido. Ver filtros.py::_es_psc_producto.
#
# Se mantiene como senal DEBIL (+1) y nunca como veto. Que un codigo no este
# en la lista no significa que el aviso sea un servicio: significa que nadie
# lo escribio.
#
# Las familias que si son servicios (5800 software, 9710 consulting,
# C1xx-C2xx construccion) siguen sin vetar: eso lo deciden las palabras de
# servicio del titulo y Gemini al leer la especificacion. Este cambio relaja
# el veto, no lo sustituye.
PSC_BIENES = {
    "5330",  # suministros y consumibles generales
    "5340",  # utiles de dibujo y taller
    "5345",  # cajas, bolsas, material de empaque
    "5350",  # ferreteria y suministros de mantenimiento
    "5510",  # ropa de cama y textil
    "5520",  # ropa y equipo de proteccion personal
    "5820",  # equipo de comunicaciones
    "7030",  # instrumentos de prueba y medicion
    "7040",  # laboratorio y quimica
    "7050",  # instrumentacion
    "7065",  # componentes electronicos
    "7100",  # equipo general
    "7120",  # instrumentos de medida
    "7210",  # equipos de pruebas
    "7310",  # equipos de ingenieria
    "7330",  # utilidades de ingenieria
    "7360",  # maquinas herramientas
    "7400",  # estructuras y componentes
    "7410",  # construccion y mineria
    "7430",  # ingenieria estructural
    "7510",  # ferreteria
    "7610",  # climatizacion
    "7670",  # servicios de construccion
    "7690",  # servicios auxiliares
    "9140",  # preparacion del terreno
    "9900",  # suministros varios
}

# --- Orientacion COTS Easy-Supply (10-oct-2026) --------------------------
#
# Kyomoto se orienta a licitaciones hasta USD 250.000 (TOPE_USD, ya estaba ahi)
# que ademas sean FACILES de suplir como intermediario: producto de catalogo,
# con distribuidor comercial abierto en EE.UU., sin fabricacion a medida y sin
# certificacion de origen de defensa.
#
# Estas tres listas son el filtro de esa orientacion. Se midieron antes de
# aplicarlas contra los 11 titulos reales del barrido del 29-sep (los kits de
# repuestos navales, que son el nucleo del negocio) y contra 12 titulos de lo
# que hay que excluir: 0 de 11 reales tocados, 12 de 12 excluidos.

# Plataformas de defensa, armamento y municion. Se buscan SOLO en el titulo:
# son el encabezado de la licitacion, y en la descripcion salen de paso.
#
# Se usan pares y no adjetivos sueltos a proposito: "armored cable" y "armored
# hose" son productos comerciales que se compran en cualquier distribuidor, lo
# militar es el casco o el vehiculo ("armored hull", "fighting vehicle"). Con
# "armored" suelto, un "ARMORED CABLE 12/2" comercial caeria.
EXCLUSION_COMPLEJIDAD_DEFENSA = (
    # Plataformas de aviacion militar y buques de guerra
    "fighter aircraft", "strike fighter", "aircraft platform",
    "combat aircraft", "helicopter platform", "rotary wing aircraft",
    "warship", "combatant vessel", "destroyer", "frigate", "submarine",
    "aircraft carrier", "surface combatant",
    # Vehiculos blindados
    "battle tank", "main battle tank", "fighting vehicle", "combat vehicle",
    "armored vehicle", "armoured vehicle", "armored hull", "armour hull",
    "mine resistant", "mrap",
    # Armamento y municion
    "weapon system", "gun system", "ordnance", "ammunition", "munition",
    "artillery", "howitzer", "missile", "grenade", "cannon",
)

# Combustible de aviacion militar y materiales energeticos. Tambien en el
# titulo. Las referencias JP-x se comprueban con limite de palabra.
EXCLUSION_COMBUSTIBLE_DEFENSA = (
    "aviation fuel", "jet fuel", "defense fuel", "propellant",
    "energetic material", "explosive", "jp-5", "jp-8", "jp-7",
)

# Certificaciones de ORIGEN de defensa, exclusivas. Se buscan en la
# DESCRIPCION, no en el titulo, porque ahi es donde se exigen.
#
# OJO con "itar": esta DENTRO de "military", "maritime", "similar" y
# "particular". Por eso se comprueban con limite de palabra. Un aviso que habla
# de origen militar normal, que es justo lo que se quiere aceptar, se
# descartaria si no.
EXCLUSION_CERTIFICACION_ORIGEN = (
    "itar", "jcp", "joint certification program",
    "dd250", "dd 250",
    "certificate of conformance to origin",
)

# Las familias PSC donde SI existe distribuidor o mayorista comercial abierto en
# EE.UU. con entrega directa: es donde se compra a precio de intermediario, que
# es de donde sale el margen.
#
# Da BONUS, no veto. El PSC nunca veta (decision del 30-sep, comprobada en
# test_ajustes.py), y esta lista no lo cambia: solo sube lo que ya pasa.
PSC_COTS = {
    # Medico y hospitalario comercial (McKesson, Henry Schein, Cardinal Health)
    "6505",  # Hospital Furnishings, Equipment and Supplies
    "6510",  # Medical and Surgical Equipment and Supplies
    "6515",  # Medical, Pharmaceutical, and Veterinary Materials and Equipment
    "6525",  # Radiologic Equipment
    "6545",  # Medical, Dental and Veterinary Basic Medical Supplies
    "6550",  # Medical Furniture and Equipment
    # MRO, herramientas y ferreteria industrial estandar
    "5110",  # Hardware, Tools and Shop Equipment
    "5120",  # Shop Equipment, Small Tools and Supplies
    "5130",  # Tracing and Cutting Tools
    "5140",  # Tool and Shop Equipment
    "5150",  # Hardware, Tools and Shop Equipment, Miscellaneous
    "5340",  # Tools and Hardware for Hand and Power Tools
    "5350",  # Hardware, Tools and Shop Equipment, Maintenance and Repair
    # Limpieza, higiene, empaque y suministros de instalaciones
    "4210",  # Housekeeping and Sanitation Equipment and Supplies
    "7250",  # Miscellaneous Polishing and Buffing Machinery
    "7310",  # Engineering and Equipment for Construction
    "7360",  # Machine Tools
    "7390",  # Tools and Equipment for Manufacturing
    "7930",  # Laundry, Dry Cleaning and Pressure Bottle Cleaning
    "8020",  # Housekeeping and Sanitation Equipment
    "8135",  # Gloves and Garments, Protective, Rubber
    # Computo, redes y TI comercial (CDW, Insight, SHI: catalogo abierto)
    "7025",  # Data Processing Equipment, Computer
    "7035",  # Communications and Security Equipment
    "7040",  # Laboratory and Scientific Equipment
    "7045",  # ADP Equipment and Software
    "7070",  # Coherence Equipment
    # Flota vehicular comercial terrestre (fleet parts, sin control de export)
    "2510",  # Parts, Light Truck and Utility Vehicles
    "2530",  # Parts, Heavy Truck and Nonengine Passenger
}
# Cuanto suma una familia COTS. Poco a proposito: PUNTAJE_MINIMO es 6 y el
# filtro ya suma bastante, asi que +2 coloca la oportunidad por delante sin
# empujarla por encima de una que sea mejor por otros motivos.
COTS_PUNTOS = 2

# Rango valido de un PSC federal. Cuatro digitos, 1000 a 9999.
PSC_MINIMO = 1000
PSC_MAXIMO = 9999

# --- Purchase Order en PDF (ver pdf_generator.py) ---
# Carta, con margenes en pulgadas. Se dejan-generosos porque el PDF lleva
# membrete (logo) arriba y firma abajo, y con margenes de 0.5" el bloque de
# texto se comia el logo.
PDF_MARGEN_IZQUIERDO = 0.75
PDF_MARGEN_DERECHO = 0.75
PDF_MARGEN_SUPERIOR = 0.45
PDF_MARGEN_INFERIOR = 0.45

# ==========================================================================
#  DESCALIFICADORES QUE APRENDI DE LOS ANALISIS REALES
#
#  Medido sobre avisos reales de SAM.gov: de cada 6 que evaluo Gemini, solo
#  1 sale viable. Las causas se repiten siempre, y la mayoria se pueden
#  detectar por texto ANTES de gastar una llamada. Esto es lo que sube la
#  tasa de acierto del presupuesto diario.
# ==========================================================================

# Certificaciones y accesos que una empresa en Chile no puede obtener.
# Peso alto: si aparece, casi siempre es descarte.
PALABRAS_CERTIFICACION = {
    "dd2345", "dd233", "data custodian", "security clearance", "clearance level",
    "secret clearance", "top secret", "public trust", "faci", "fobs",
    "first article testing", "first article approval", "fat", "faa",
    "certificate of conformance", "certification level i", "level i scope",
    "special security agreement", "cage code", "facility clearance",
    "nispom", "nist sp 800-171", "defense industrial base", "dib",
    "itar", "export control", "technology control plan",
    "iso 9001", "as9100", "nadcap", "six sigma", "itpsr",
    "oem approved", "manufacturer authorized", "authorized distributor",
    "sole source", "sole-source", "brand name only", "or equal",
}

# Servicio disfrazado de producto: la palabra "repair"/"overhaul" convierte la
# venta en un servicio, y los servicios no son el negocio.
PALABRAS_SERVICIO_OCULTO = {
    "overhaul", "rebuild", "refurbish", "refurbishment", "remanufactur",
    "repair of", "repair services", "maintenance of", "servicing",
    "installation of", "install and", "furnish and install",
    "calibration service", "technician", "technicians", "labor",
}

# Las de arriba que SI son un veto practico para un proveedor extranjero.
# "or equal" o "authorized distributor" son negociables, estas no.
PALABRAS_CERTIFICACION_DUROS = {
    "dd2345", "dd233", "data custodian", "security clearance", "clearance level",
    "secret clearance", "top secret", "public trust", "first article testing",
    "first article approval", "certificate of conformance", "certification level i",
    "level i scope", "special security agreement", "cage code",
    "facility clearance", "nispom", "nist sp 800-171", "itar", "export control",
    "defense industrial base", "sole source", "sole-source", "brand name only",
    "oem approved", "manufacturer authorized", "authorized distributor",
}

# Paises fuera de EE.UU. no son despachables para un proveedor en Chile.
PAISES_EXTRANJEROS = {
    "united arab emirates", "uae", "afghanistan", "albania", "algeria",
    "argentina", "australia", "austria", "bahrain", "bangladesh",
    "belgium", "bolivia", "brazil", "bulgaria", "cambodia", "cameroon",
    "canada", "chile", "china", "colombia", "croatia", "cyprus", "czech",
    "ecuador", "egypt", "estonia", "ethiopia", "france", "georgia",
    "germany", "ghana", "greece", "guatemala", "honduras", "hungary",
    "india", "indonesia", "iraq", "ireland", "israel", "italy", "japan",
    "jordan", "kazakhstan", "kenya", "korea", "kuwait", "latvia",
    "lebanon", "libya", "lithuania", "luxembourg", "malaysia", "mali",
    "mexico", "mongolia", "morocco", "nepal", "nicaragua", "nigeria",
    "norway", "pakistan", "panama", "paraguay", "peru", "philippines",
    "poland", "portugal", "qatar", "romania", "russia", "saudi arabia",
    "senegal", "serbia", "singapore", "slovakia", "slovenia", "somalia",
    "south africa", "spain", "sri lanka", "sudan", "sweden", "switzerland",
    "syria", "taiwan", "tanzania", "thailand", "tunisia", "turkey",
    "uganda", "ukraine", "united kingdom", "uruguay", "uzbekistan",
    "venezuela", "vietnam", "yemen", "zambia", "zimbabwe",
    "africa", "europe", "middle east", "asia", "overseas", "foreign",
}

# Palabras que delatan un producto fisico.
PALABRAS_PRODUCTO = {    "supply", "supplies", "material", "materials", "equipment", "part", "parts",
    "component", "components", "furniture", "appliance", "appliances", "tool", "tools",
    "hardware", "fastener", "fasteners", "fittings", "valve", "valves", "filter", "filters",
    "battery", "batteries", "packaging", "uniform", "uniforms", "ppe",
    "personal protective equipment", "safety equipment", "laboratory equipment",
    "lab equipment", "medical supplies", "medical equipment", "office supplies",
    "cleaning supplies", "janitorial supplies", "linens", "bedding", "towel", "towels",
    "luminaire", "lighting", "paint", "coating", "adhesive", "lubricant", "flooring",
    "lumber", "timber", "cement", "aggregate", "steel", "aluminum", "tube", "pipe", "pipes",
    "vehicle parts", "automotive parts", "engine parts", "tractor", "vehicle", "vehicles",
    "printer", "printers", "monitor", "monitors", "laptop", "server", "servers", "router",
    "switch", "cable", "cables", "wire", "generator", "pump", "pumps", "compressor",
    "tank", "container", "containers", "pallet", "pallets", "crate", "crates",
    "instrument", "instruments", "calibration", "clothes", "boots", "gloves", "goggles",
    "commercial kitchen", "groceries", "feed", "propane", "tarpaulin", "duct", "hose",
    "bearing", "bearings", "gasket", "gaskets", "seal", "seals", "pump", "blades",
    "turbine", "generator set", "submersible", "screw", "conveyor", "motor", "motors",
}

# Palabras que delatan un servicio. Restan puntos y pueden descalificar.
PALABRAS_SERVICIO = {
    "consulting", "consultant", "advisory", "training", "training services", "staffing",
    "staffing services", "temporary services", "temp help", "help desk", "call center",
    "call centre", "customer service", "technical support", "managed service",
    "software", "software as a service", "saas", "cloud services", "cloud computing",
    "hosting", "subscription", "license", "licensing", "licenses", "data processing",
    "programming", "cybersecurity", "network services", "system administration",
    "janitorial services", "cleaning services", "landscaping", "grounds maintenance",
    "legal services", "attorney", "accounting", "bookkeeping", "audit", "auditing",
    "marketing", "advertising", "public relations", "recruitment", "recruiting",
    "research and development", "evaluation services", "survey services",
    "installation services", "plumbing", "electrical work", "roofing", "hvac",
    "construction", "renovation", "remodeling", "demolition", "excavation",
    "towing", "vehicle rental", "equipment rental", "leasing", "rental of",
    "freight services", "transportation services", "shipping services", "courier",
    "medical services", "healthcare services", "nursing services", "therapy services",
    "telehealth", "childcare", "food services", "catering", "meal service",
    "laundry services", "dry cleaning", "repair services", "maintenance services",
    "preventive maintenance", "installation of", "installation and",
    "furnish and install", "labor services", "manpower", "personnel services",
    "grant management", "grant writing", "public outreach", "media services",
    "document destruction", "records management", "travel services", "tour services",
    "vehicle maintenance", "fleet maintenance", "road maintenance", "street sweeping",
}
