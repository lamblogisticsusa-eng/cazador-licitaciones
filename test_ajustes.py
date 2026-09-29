"""
test_ajustes.py - Botones, PSC y el Purchase Order en PDF.

LOS TRES AJUSTES DEL 30-Sep-2026

1) LOS BOTONES DEL MENU NO RESPONDIAN

       app.run_polling(allowed_updates=["message"])

   "callback_query" no estaba en la lista, asi que Telegram no entregaba NUNCA
   las pulsaciones de botones. El handler estaba bien registrado, con el
   patron correcto y despachando todas las acciones: simplemente no le
   llegaba el evento. Por eso los comandos funcionaban y los botones estaban
   muertos a la vez, que es el sintoma caracteristico de esto.

2) PSC PERMISIVO

   Antes solo sumaban los ~25 codigos de PSC_BIENES. Ahora cuenta cualquier
   PSC bien formado (4 digitos, 1000-9999), y sigue siendo un +1, nunca un
   veto. Se perdian justo los buenos: 2915 partes navales, 4520 materiales,
   6010 cableado, 8125 aislamiento.

3) PDF DE ORDEN DE COMPRA

   pdf_generator.py emite el Purchase Order a nombre de L.A.M.B. Logistics LLC
   con los datos de cabecera, el numero de PO, la referencia de SAM.gov, la
   tabla de articulos con especificacion, las instrucciones de despacho y la
   firma. El comando /pdf lo genera y lo manda al chat.

   Documento privado de la empresa: lleva su membrete, su numero propio
   LAMB-, y una nota al pie que dice que no es un documento oficial de
   ninguna agencia. No lleva sello ni emblema de gobierno, porque no lo es.
"""
import io
import os
import sys
import tempfile

DB = os.path.join(tempfile.gettempdir(), "kyo_test_ajustes.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

os.environ["DB_PATH"] = DB
os.environ["DRY_RUN"] = "1"
os.environ["SAM_API_KEY"] = "x"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:FAKE"
os.environ["TELEGRAM_CHAT_ID"] = "1"
os.environ["GEMINI_API_KEY"] = "x"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FALLA = 0


def check(nombre, cond, extra=""):
    global FALLA
    if cond:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


print("=" * 70)
print("1) LOS BOTONES: allowed_updates tiene que traer callback_query")
print("=" * 70)
_f = io.open("main.py", encoding="utf-8").read()
check("'allowed_updates' aparece en el arranque",
      "allowed_updates" in _f)
import re

m = re.search(r"allowed_updates\s*=\s*\[([^\]]*)\]", _f)
lista = m.group(1) if m else ""
print(f"  allowed_updates = [{lista.strip()}]")
check("Incluye 'message' (los comandos)", "message" in lista)
check("Y 'callback_query' (los botones)", "callback_query" in lista,
      "-> sin esto, Telegram NO manda las pulsaciones y el boton queda muerto")

# Y que el handler exista y con el patron correcto, que es la otra mitad.
check("El CallbackQueryHandler esta registrado",
      "CallbackQueryHandler(" in _f)
check("Con el patron de los botones del menu", 'r"^k:"' in _f)
import menu
check("Y el menu genera los callback_data con ese prefijo",
      menu.PREFIJO == "k:")
b = menu.teclado(True)
datos = [btn.callback_data for fila in b.inline_keyboard for btn in fila]
print(f"  Botones del menu: {datos}")
check("Todos empiezan por 'k:'", all(d.startswith("k:") for d in datos))
check("Y hay un boton para cada accion que se despacha",
      set(datos) == {"k:buscar", "k:estado", "k:cuota", "k:puntajes",
                     "k:pdf", "k:selftest", "k:off"},
      f"-> {sorted(datos)}")

# Y que cmd_boton despache TODAS las que genera el menu, que es donde un
# boton se queda mudo si le falta su caso.
_acciones = set(re.findall(r'accion == "(\w+)"', _f))
_faltan = [d.split(":", 1)[1] for d in datos
           if d.split(":", 1)[1] not in _acciones]
check("cmd_boton tiene un caso para cada boton", not _faltan,
      f"-> sin caso: {_faltan}")
print(f"  Acciones despachadas: {sorted(_acciones)}")

print()
print("=" * 70)
print("2) PSC: cualquier codigo de 4 digitos cuenta como producto")
print("=" * 70)
import config
import filters

# Los que antes NO sumaban y son justo los que interesan.
CODIGOS = [
    ("2915", "maquinaria y partes navales", True),
    ("4520", "materiales de construccion", True),
    ("6010", "cableado electrico", True),
    ("8125", "relleno de aislamiento", True),
    ("5330", "suministros generales", True),
    ("1000", "primer codigo del rango", True),
    ("9999", "ultimo codigo del rango", True),
    ("9949", "casi el ultimo", True),
    # Y lo que NO debe contar.
    ("abc", "no es numerico", False),
    ("123", "tres digitos", False),
    ("12345", "cinco digitos", False),
    ("", "vacio", False),
    ("29 5", "con espacio", False),
    ("29.5", "con punto", False),
]
for codigo, nota, esperado in CODIGOS:
    check(f"  {codigo!r:8} {nota:28}", filters._es_psc_producto(codigo) is esperado)

check("El rango esta en config", config.PSC_MINIMO == 1000 and config.PSC_MAXIMO == 9999)

# Lo importante: el PSC NUNCA veta, solo suma. Un PSC de servicio tiene que
# seguir llegando a la fase de Gemini, que es quien decide.
print()
print("  El PSC suma, pero NO veta:")
_caso = {
    "title": "MAINTENANCE OF GENERATOR 50KW",
    "naicsCode": "332999", "classificationCode": "2915",
    "typeOfSetAside": "", "responseDeadLine": "2026-11-04T17:00:00-04:00",
}
_p, _m = filters.puntuar(_caso, "")
check("Un PSC valido suma al menos 1", _p >= 1, f"-> {_p}")
check("Y no lo veta (puntaje no negativo)", _p > -100, f"-> {_p}")
check("Y el motivo menciona que es tangible",
      any("tangible" in x for x in _m), f"-> {_m}")

print()
print("=" * 70)
print("3) PDF: Purchase Order de L.A.M.B. Logistics")
print("=" * 70)
import pdf_generator as pdfg

check("El modulo existe y trae los datos de la empresa",
      pdfg.EMPRESA == "L.A.M.B. Logistics LLC")
check("Con la direccion pedida",
      pdfg.DIRECCION == ["1209 Mountain Rd. PL. NE, Ste. H",
                         "Albuquerque, NM 87110"],
      f"-> {pdfg.DIRECCION}")
check("Y el firmante", pdfg.FIRMANTE == "Bastian Cordero"
      and pdfg.CARGO_FIRMANTE == "CEO")

# --- El numero de PO: correlativo y persistente -------------------------
print()
print("  Numero de Purchase Order:")
n1 = pdfg.siguiente_numero_po()
n2 = pdfg.siguiente_numero_po()
print(f"    1o: {n1}")
print(f"    2o: {n2}")
check("Empieza por LAMB-", n1.startswith("LAMB-"), f"-> {n1}")
check("Los dos son DISTINTOS", n1 != n2,
      "-> dos ordenes con el mismo numero no se pueden conciliar")
check("Y el nombre del fichero lleva el numero",
      pdfg.nombre_archivo(n1) == n1 + ".pdf", f"-> {pdfg.nombre_archivo(n1)}")
check("El nombre no sale 'PO.pdf' para todo",
      pdfg.nombre_archivo(n1) != "PO.pdf")

# --- Generacion real ----------------------------------------------------
print()
print("  Generando un PDF de verdad...")
_analisis = {
    "notice_id": "W9127N26QA145", "solicitation": "W9127N26QA145",
    "title": "PARTS KIT, ROTARY PUMP, NAVAL AUXILIARY",
    "agencia": "Department of the Navy (NAVSEA)",
    "producto": "Kits de repuestos para bomba rotativa auxiliar naval",
    "modelo_especifico": "MIL-STD-1660",
    "unidad_medida": "KIT", "cantidad_total": 45,
    "especificacion_tecnica_clave": "Acero fundido, sellos mecanicos",
    "lugar_entrega": "Naval Station Norfolk / VA / UNITED STATES",
    "limite": "2026-11-04T17:00:00-04:00",
    "costo_unitario_costo": 380.0, "costo_total_usd": 17100.0,
    "precio_oferta_sugerido_usd": 21825.0,
}
_proveedor = {"nombre": "Grainger Industrial Supply",
              "contacto": "Dept. de Ventas",
              "email": "ventas@grainger.example",
              "direccion": "1 Grainger Way, Chicago, IL 60606"}
_datos = pdfg.desde_analisis(_analisis, _proveedor)
_pdf = pdfg.generar_po(_datos)

check("El PDF tiene la cabecera magica %PDF-", _pdf[:5] == b"%PDF-")
check("Y pesa algo razonable", len(_pdf) > 2000, f"-> {len(_pdf)} bytes")
check("El numero de PO sale en los datos", bool(_datos.get("numero_po")))
check("Y el documento y el nombre salen del MISMO numero",
      _datos["numero_po"] in _datos["numero_po"]
      and pdfg.nombre_archivo(_datos["numero_po"]).startswith(_datos["numero_po"]))

# --- Contenido: hay que LEER el PDF, no buscar en los bytes ------------
# Los flujos de reportlab van comprimidos: buscar "Grainger" en los bytes
# crudos no encuentra nada aunque el texto este ahi. Eso hizo fallar la
# primera version de esta comprobacion.
try:
    from pypdf import PdfReader

    _ruta = os.path.join(tempfile.gettempdir(), "kyo_test_po.pdf")
    with open(_ruta, "wb") as _fh:
        _fh.write(_pdf)
    _txt = "\n".join(p.extract_text() or "" for p in PdfReader(_ruta).pages)
except ImportError:
    _txt = ""
    print("    (pypdf no esta: se comprueba solo que el PDF se genera)")

if _txt:
    print()
    print("  Texto real del PDF:")
    for _l in [x for x in _txt.split("\n") if x.strip()][:6]:
        print(f"    {_l[:66]}")
    print()
    CONTENIDO = [
        ("Nombre de la empresa", "L.A.M.B. Logistics LLC"),
        ("Direccion linea 1", "1209 Mountain Rd. PL. NE, Ste. H"),
        ("Direccion linea 2", "Albuquerque, NM 87110"),
        ("Titulo PURCHASE ORDER", "PURCHASE ORDER"),
        ("Numero de PO", _datos["numero_po"]),
        ("Referencia SAM.gov", "W9127N26QA145"),
        ("Bloque de proveedor", "SUPPLIER / VENDOR"),
        ("Nombre del distribuidor", "Grainger Industrial Supply"),
        ("Tabla de articulos", "ITEMS ORDERED"),
        ("Cantidad", "45"),
        ("Unidad", "KIT"),
        ("Precio unitario", "$380.00"),
        ("Importe total", "$17,100.00"),
        ("Instrucciones de despacho", "SHIPPING INSTRUCTIONS"),
        ("Condiciones", "TERMS AND CONDITIONS"),
        ("Firma", "Bastian Cordero"),
        ("Cargo", "CEO"),
        ("Modelo en la descripcion", "MIL-STD-1660"),
    ]
    for etiqueta, busca in CONTENIDO:
        check(f"  {etiqueta:28}", busca in _txt, f"-> no sale {busca!r}")

    # --- Lo que tiene que ser legible, no ISO ----------------------------
    print()
    print("  Formato de la fecha limite:")
    check("  No sale en ISO crudo", "2026-11-04T17:00:00" not in _txt)
    check("  Sale en fecha legible", "November 4, 2026" in _txt,
          f"-> {_txt[_txt.find('RESPONSE DUE'):][:60]}")
    check("  Y con la zona horaria", "EDT" in _txt)

    print()
    print("  La columna ITEM no parte el numero de solicitud:")
    # "26QA14" no sirve para esto: es subcadena de "W9127N26QA145" y siempre
    # esta, partido o no. Lo que delata una columna estrecha es que el numero
    # aparezca FRAGMENTADO en lineas sueltas en vez de entero en una.
    _lineas_txt = [x.strip() for x in _txt.split("\n") if x.strip()]
    _partes_sol = [x for x in _lineas_txt
                   if x in ("W9127N", "26QA14", "5", "26QA", "145")
                   and "ITEM" not in x]
    check("  No hay fragmentos sueltos del numero de solicitud",
          not _partes_sol, f"-> fragmentos: {_partes_sol}")
    check("  El numero aparece entero en una linea",
          "W9127N26QA145" in _lineas_txt,
          "-> la columna ITEM lo parte porque no cabe en 0.62 pulgadas")
    # Y la columna ITEM lleva el numero de linea, no el de solicitud.
    # El encabezado de la tabla tiene 6 celdas (ITEM, DESCRIPTION, QTY, UNIT,
    # UNIT PRICE, AMOUNT) y pypdf las saca en lineas separadas, asi que el
    # numero de linea esta unas 7 lineas despues, no 3.
    _tras_item = _lineas_txt.index("ITEM") if "ITEM" in _lineas_txt else -1
    check("  La columna ITEM lleva el numero de linea",
          _tras_item >= 0 and "1" in _lineas_txt[_tras_item:_tras_item + 9],
          f"-> {_lineas_txt[_tras_item:_tras_item + 9]}")

    print()
    print("  Documento privado, no oficial:")
    check("  Dice que NO es documento oficial del gobierno",
          "not a document" in _txt.lower() or "No es un documento oficial" in _txt)
    # Y que no lleve nada que lo parezca.
    for prohibido in ("UNITED STATES GOVERNMENT", "DEPARTMENT OF DEFENSE SEAL",
                      "GPO", "OFFICIAL USE ONLY", "SEAL"):
        check(f"  No lleva '{prohibido}'", prohibido not in _txt.upper(),
              "-> el documento podria confundirse con uno de agencia")

print()
print("=" * 70)
print("4) EL COMANDO /pdf EXISTE Y ESTA REGISTRADO")
print("=" * 70)
check("El comando /pdf esta registrado",
      'CommandHandler("pdf"' in _f)
check("Y la funcion cmd_pdf existe", "async def cmd_pdf(" in _f)
check("Y hay un boton que lo lanza en el menu",
      "PREFIJO + \"pdf\"" in io.open("menu.py", encoding="utf-8").read())
check("Y el boton tiene su caso en cmd_boton", 'accion == "pdf"' in _f)
check("store guarda el analisis (sin esto /pdf no tendria datos)",
      "def guardar_analisis(" in io.open("store.py", encoding="utf-8").read())
check("Y se puede buscar por numero de solicitud",
      "def buscar_por_solicitud(" in io.open("store.py", encoding="utf-8").read())
check("Y el escaner lo guarda cuando un aviso queda viable",
      "guardar_analisis" in io.open("scanner.py", encoding="utf-8").read())
print("  Sin persistir el analisis, /pdf responderia siempre 'no encontrado':")
print("  store.marcar() solo guardaba id, titulo, viable y puntaje.")

# El round trip completo de la base de datos.
print()
print("  Ida y vuelta por la base de datos:")
import store
store.init_db()
store.guardar_analisis(_analisis)
_leido = store.buscar_por_solicitud("W9127N26QA145")
check("Se guarda y se recupera por el numero exacto",
      bool(_leido), "-> no se encontro")
check("Y vienen todos los datos", _leido.get("costo_total_usd") == 17100.0,
      f"-> {_leido.get('costo_total_usd')}")
check("Busca tambien en minusculas",
      bool(store.buscar_por_solicitud("w9127n26qa145")))
check("Y con coincidencia parcial",
      bool(store.buscar_por_solicitud("W9127N26")))
check("analisis_recentes lo lista",
      len(store.analisis_recientes(limite=5)) >= 1)
check("Un numero que no existe devuelve vacio",
      store.buscar_por_solicitud("NO-EXISTE-XYZ") == {})

for s in ("", "-wal", "-shm"):
    try:
        os.remove(DB + s)
    except OSError:
        pass

print()
print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} fallo(s)")
    sys.exit(1)
print("RESULTADO: los tres ajustes funcionan")
