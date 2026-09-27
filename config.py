"""
Kyomoto - Configuracion central.
Todas las claves se leen de variables de entorno. Nunca se hardcodean.
"""
import os


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

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

# --- Ventana de busqueda en SAM.gov ---
# DIAS_POR_CHUNK debe ser chico: la API devuelve ~450 registros/dia y corta en 1000.
DIAS_DE_VENTANA = _int("DIAS_DE_VENTANA", 3)
DIAS_POR_CHUNK = _int("DIAS_POR_CHUNK", 2)
LIMITE_POR_CHUNK = _int("LIMITE_POR_CHUNK", 1000)
MAX_CHUNKS = _int("MAX_CHUNKS", 10)

# Cuantas oportunidades pasan a Gemini en cada barrido.
MAX_A_GEMINI = _int("MAX_A_GEMINI", 25)
# Piso de puntaje. Subirlo deja solo lo mas limpio a costa de revisar menos.
PUNTAJE_MINIMO = _int("PUNTAJE_MINIMO", 6)
# Cuantas descripciones bajamos de la API antes de puntuar en detalle.
MAX_DESCRIPCIONES = _int("MAX_DESCRIPCIONES", 40)
# Tope duro de avisos que te llegan por barrido, ordenados por puntaje.
# Esto es lo que hace que recibas 5-10 buenos y no 25 mediocres.
MAX_NOTIFICACIONES = _int("MAX_NOTIFICACIONES", 10)

# --- Reglas de negocio ---
TOPE_USD = _float("TOPE_USD", 250000.0)
MIN_USD = _float("MIN_USD", 5000.0)
DIAS_MINIMO_PARA_POSTULAR = _int("DIAS_MINIMO_PARA_POSTULAR", 7)

# --- Rendimiento / Costos ---
# WORKERS controla las descargas de descripciones (HTTP barato a SAM.gov).
WORKERS = _int("WORKERS", 8)

# Gemini tiene su propio limite: el plan gratis es muy bajo (429
# RESOURCE_EXHAUSTED). Por eso va aparte y con pausas. Subir estos numeros
# sin subir de plan es la causa numero uno de que el escaneo se caiga a mitad.
GEMINI_WORKERS = _int("GEMINI_WORKERS", 2)
GEMINI_PAUSA_SEG = _float("GEMINI_PAUSA_SEG", 4.0)
# Ante un 429 no se reintenta a lo loco: se deja el aviso para el proximo
# barrido. Reintentar solo consume mas cuota.
GEMINI_REINTENTOS_429 = _int("GEMINI_REINTENTOS_429", 1)

GEMINI_TEMPERATURE = _float("GEMINI_TEMPERATURE", 0.2)
GEMINI_THINKING_BUDGET = _int("GEMINI_THINKING_BUDGET", 0)
# El plan gratis de Gemini es muy justo de facto: cada llamada cuenta. Cuanto mas
# corto el prompt y mas corta la salida, mas avisos entran por barrido.
# 3000 caracteres de descripcion basta para decidir y para estimar precio;
# pedir mas solo quema cuota.
MAX_CHARS_DESCRIPCION = _int("MAX_CHARS_DESCRIPCION", 3000)
GEMINI_MAX_OUTPUT_TOKENS = _int("GEMINI_MAX_OUTPUT_TOKENS", 2048)
TIMEOUT_HTTP = _int("TIMEOUT_HTTP", 45)
MAX_REINTENTOS = _int("MAX_REINTENTOS", 3)

# --- Operacion ---
# Kyomoto revisa una vez al dia: SAM.gov publica ~450 avisos diarios, asi que
# cada 4 horas solo repetiria barridos para no encontrar nada nuevo y gastar
# cuota de Gemini al pedo.
INTERVALO_HORAS = _float("INTERVALO_HORAS", 24)
DB_PATH = os.getenv("DB_PATH", "licitaciones.db")
DRY_RUN = _bool("DRY_RUN", False)  # True = no envia mensajes, solo loguea
ETIQUETAS_DIR = os.getenv("ETIQUETAS_DIR", "etiquetas")

PORT = _int("PORT", 10000)


# ==========================================================================
#  CLASIFICACION NAICS
#
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

# Clasificacion PSC (4 digitos) que suele ser producto tangible. Senal debil (+1).
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
    "7190",  # mantenimiento de equipo
    "7220",  # equipo de proceso
    "7240",  # equipos de computacion
    "7360",  # mobiliario y equipos de oficina
    "7410",  # maquinaria de construccion
    "7420",  # maquinaria de extraccion
    "7450",  # vehiculos industriales
    "7510",  # transporte de carga
    "7690",  # miscelaneos de fabricacion
}

# Palabras que delatan un producto fisico.
PALABRAS_PRODUCTO = {
    "supply", "supplies", "material", "materials", "equipment", "part", "parts",
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
