"""
test_render.py - Verifica el render HTML de Telegram y el PDF de la etiqueta.
No necesita internet ni claves validas.
"""
import os
import sys

os.environ.setdefault("SAM_API_KEY", "x")
os.environ.setdefault("DRY_RUN", "1")

import config
import label
import telegram_notify as tn

FALLA = 0


def check(nombre, condicion, extra=""):
    global FALLA
    if condicion:
        print(f"  OK    {nombre}")
    else:
        FALLA += 1
        print(f"  FALLA {nombre} {extra}")


print("=" * 70)
print("1) ESCAPE HTML (el bug del parse_mode Markdown)")
print("=" * 70)
# Texto hostile: es exactamente lo que Gemini puede devolver.
hostil = {
    "title": 'Kit *sello* _mecanico_ [con Discount](https://x.com) <b>inyectado</b>',
    "solicitation": "W9127N26QA145",
    "agencia": "DEPT OF *DEFENSE* < Army Corps",
    "naics": "339991",
    "psc": "5330",
    "set_aside": "SBA",
    "producto": "Sellos mecanicos *10,000_uds* & mas <script>",
    "cantidad_estimada": "2,000 EA",
    "especificacion_tecnica_clave": "Dureza 60 HRC, acero <b>inox</b>",
    "lugar_entrega": "Portland / Oregon / UNITED STATES",
    "limite": "2026-10-10T18:00:00-07:00",
    "sin_descripcion": True,
    "valor_contrato_usd": 45000.0,
    "costo_proveedor_usd": 31000.0,
    "ganancia_neta_usd": 14000.0,
    "margen_porcentaje": 31.1,
    "precio_unitario_referencia_usd": 22.5,
    "precio_unitario_sugerido_usd": 34.5,
    "precio_oferta_sugerido_usd": 41000.0,
    "estrategia_oferta": "Ofertar 8% bajo el estimado *con_underline* incluido.",
    "busquedas_distribuidores": ["wholesale mechanical seal usa", "seals & gaskets distributor"],
    "nivel_riesgo": "medio",
    "preguntas_criticas": ["¿El NAICS exige fabricante original?", "¿Aceptan substitutes?"],
    "observaciones": "Verificar *licencia* de exportacion",
    "contacto": "Joseph Jarvis | joseph.b.jarvis@usace.army.mil",
    "ui_link": "https://sam.gov/workspace/contract/opp/60aa8e3f/view",
    "notice_id": "60aa8e3f9222455eab2e94bf4be98e70",
}
html = tn.formatear_analisis(hostil)

check("No deja <script> crudo", "<script>" not in html)
check("No deja <b>inyectado</b> del proveedor", "<b>inyectado</b>" not in html)
check("Escapa el ampersand", "&amp; mas" in html or "&amp;" in html)
check("Conserva el enlace de SAM.gov", 'href="https://sam.gov/workspace' in html)
check("Muestra los 3 numeros financieros", html.count("$") >= 5)
check("Avisa que falta la descripcion", "sin descripcion oficial" in html)
check("Muestra limite y destino", "2026-10-10" in html and "Portland" in html)
check("Terminos de busqueda en <code>", "<code>wholesale mechanical seal usa</code>" in html)
print()

print("=" * 70)
print("1b) LIMPIEZA DE MARKDOWN DE LA IA")
print("=" * 70)
import gemini_analyzer as ga
casos = [
    ("**negrita**", "negrita"),
    ("*cursiva*", "cursiva"),
    ("_subrayado_", "subrayado"),
    ("`codigo`", "codigo"),
    ("***muy fuerte***", "muy fuerte"),
    ("10_000 unidades", "10_000 unidades"),          # guion bajo interno NO se toca
    ("a * b", "a * b"),                              # asterisco suelto no se toca
    ("mezcla **de** cosas _varias_", "mezcla de cosas varias"),
]
for entrada, esperado in casos:
    salida = ga._limpiar_ia(entrada)
    check(f"{entrada!r} -> {salida!r}", salida == esperado, f"(esperado {esperado!r})")
check("No inventa texto", ga._limpiar_ia(None) == "")
check("Tolera campo numerico", ga._limpiar_ia(45000) == "45000")
print()

print("=" * 70)
print("2) TROCEADO (limite duro de 4096 de Telegram)")
print("=" * 70)
largo = "linea con *markdown* y _subrayado_ y un link https://a.com/b/c\n" * 400
trozos = tn._trocear(largo)
check("Trocea un texto largo", len(trozos) > 1, f"-> {len(trozos)} trozos")
check("Ningun trozo pasa de 4096", all(len(t) <= 4096 for t in trozos),
      f"-> max {max(len(t) for t in trozos)}")
check("No se pierde contenido", sum(len(t) for t in trozos) == len(largo.rstrip("\n")) - 0
      or "".join(trozos).replace("\n", "") == largo.replace("\n", ""))
vacio = tn._trocear("")
check("Texto vacio no rompe", vacio == [""])
corto = tn._trocear("hola")
check("Texto corto queda entero", corto == ["hola"])
print()

print("=" * 70)
print("3) PDF DE LA ETIQUETA")
print("=" * 70)
ruta = label.generar(hostil["notice_id"], hostil)
if not ruta:
    print("  FALLA reportlab no esta instalado")
    FALLA += 1
else:
    existe = os.path.exists(ruta)
    tam = os.path.getsize(ruta) if existe else 0
    check("El PDF se creo", existe, f"-> {ruta}")
    check("No esta vacio (peso > 2 KB)", tam > 2048, f"-> {tam} bytes")
    with open(ruta, "rb") as f:
        cabecera = f.read(5)
    check("Es un PDF valido", cabecera == b"%PDF-", f"-> {cabecera!r}")
    check("Nombre con el numero de solicitud", "W9127N26QA145" in os.path.basename(ruta))
print()

print("=" * 70)
print("4) FUGA DE SECRETOS EN EL CODIGO")
print("=" * 70)
import re
secretos = re.compile(r"(AIza[A-Za-z0-9_\-]{20,}|SAM-[0-9a-f\-]{20,}|[0-9]{8,10}:[A-Za-z0-9_\-]{30,})")
for archivo in sorted(os.listdir(".")):
    if not archivo.endswith(".py") and archivo not in ("requirements.txt", "render.yaml"):
        continue
    with open(archivo, encoding="utf-8", errors="replace") as f:
        contenido = f.read()
    encontrados = secretos.findall(contenido)
    check(f"{archivo} sin claves hardcodeadas", not encontrados,
          f"-> {[e[:12] + '...' for e in encontrados]}")
print()

print("=" * 70)
if FALLA:
    print(f"RESULTADO: {FALLA} comprobacion(es) fallida(s)")
    sys.exit(1)
print("RESULTADO: todo correcto")
