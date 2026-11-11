"""
telegram_notify.py - Envio de mensajes a Telegram sin que se pierdan.

PROBLEMAS DEL CODIGO ORIGINAL QUE ESTE MODULO ARREGLA:

1) Enviaba con parse_mode="Markdown". El texto lo genera Gemini, y el Markdown
   de Telegram es BIZOSO: solo acepta *b* _i_ `c` [t](u). Cualquier guion bajo
   o asterisco suelto (una URL, un nombre de archivo, un "10_000") hace que
   Telegram responda 400 y el mensaje se pierda. Aqui usamos HTML con escape
   correcto, que es mucho mas indulgente.

2) Telegram tiene un limite duro de 4096 caracteres por mensaje. El analisis
   de Gemini puede pasarse. Si el mensaje es largo, Telegram responde 400 por
   texto demasiado largo y el original reintentaba SIN parse_mode: fallaba de
   nuevo y el mensaje se perdia igual. Aqui dividimos en trozos de forma
   deliberada.

3) Si sendMessage falla, no se avisa. Aqui los errores quedan en el log.
"""
from __future__ import annotations

import html
import re
import textwrap
import logging

import requests

import config

log = logging.getLogger("kyomoto.telegram")

LIMITE_TELEGRAM = 4096
RESERVA = 32  # margen para el <pre> / cierre de etiquetas

_url = "https://api.telegram.org/bot{token}/{metodo}"


def _pedir(metodo: str, payload: dict) -> dict | None:
    if not config.TELEGRAM_BOT_TOKEN:
        log.error("No hay TELEGRAM_BOT_TOKEN. No se puede enviar.")
        return None
    url = _url.format(token=config.TELEGRAM_BOT_TOKEN, metodo=metodo)
    try:
        r = requests.post(url, json=payload, timeout=20)
    except requests.RequestException as e:
        log.error("Falla de red enviando a Telegram: %s", e)
        return None
    if r.status_code != 200:
        log.error("Telegram %s -> %s", r.status_code, r.text[:300])
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _corte_seguro(linea: str, corte: int, limite: int) -> int:
    """
    Ajusta un punto de corte para que no caiga DENTRO de una etiqueta HTML.

    _trocear corta en un espacio, y ese espacio puede caer dentro de un
    <a href="..."> largo. La primera mitad del trozo queda con la etiqueta sin
    cerrar y Telegram rechaza el mensaje. Si hay un "<" abierto sin cerrar en
    el punto de corte, el corte se retrasa hasta antes de ese "<".

    Garantiza devolver un valor > 0. Si no hay ningun sitio seguro (por
    ejemplo una sola linea que empieza con "<" y nunca cierra antes del
    limite), se corta en el limite aunque haya que partir la etiqueta: es un
    caso rarísimo, _sanear_html escapa el "<" roto del trozo y Telegram lo
    acepta. Lo que NO puede pasar es devolver 0, porque entonces el bucle de
    troceado no avanza y se queda colgado para siempre.
    """
    abierto = linea.rfind("<", 0, corte)
    if abierto == -1:
        return corte
    cerrado = linea.find(">", abierto, corte)
    if cerrado == -1:
        nuevo = linea.rfind(" ", 0, abierto)
        if nuevo > 0:
            return nuevo
        return min(limite, max(1, corte))
    return corte


def _trocear(texto: str, limite: int = LIMITE_TELEGRAM - RESERVA) -> list[str]:
    """Divide respetando saltos de linea y, a falta de ellos, palabras."""
    if len(texto) <= limite:
        return [texto]
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        while len(linea) > limite:
            corte = linea.rfind(" ", 0, limite)
            if corte <= 0:
                corte = limite
            # Un corte en un espacio puede caer dentro de una etiqueta, si la
            # linea es un <a href="..."> largo. La primera mitad quedaria con
            # la etiqueta sin cerrar y Telegram rechaza el trozo entero.
            corte = _corte_seguro(linea, corte, limite)
            if actual:
                trozos.append(actual)
                actual = ""
            trozos.append(linea[:corte])
            linea = linea[corte:].lstrip()
        if len(actual) + len(linea) + 1 > limite:
            trozos.append(actual)
            actual = linea
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        trozos.append(actual)
    return [t for t in trozos if t.strip()]


def _chat_id_valido(chat_id) -> bool:
    """
    Dice si un chat_id sirve para mandar de verdad.

    No basta con `if not chat_id`. El fallo real que costaba un escaneo
    entero por barrido era justamente ese: un str(None) llega como la CADENA
    "None", que tiene cuatro letras y es VERDADERA en Python, asi que el
    guarda `if not` la deja pasar y el POST sale con chat_id="None". Telegram
    contesta 400 "chat not found" y el mensaje se pierde sin que nadie se
    entere.

    Un chat_id de Telegram es un entero (positivo, o negativo para grupos y
    canales) o su cadena decimal. Cualquier otra cosa esta mal, y se rechaza
    aqui en vez de gastar una llamada a la API.
    """
    if chat_id is None:
        return False
    if isinstance(chat_id, bool):          # True/False no son chat_id
        return False
    if isinstance(chat_id, int):
        return chat_id != 0
    texto = str(chat_id).strip()
    if not texto or texto.lower() in ("none", "null", "nan", "undefined"):
        return False
    return texto.lstrip("-").isdigit()


def enviar(chat_id: str, texto: str, html_mode: bool = True) -> bool:
    """Envia texto, troceado si hace falta. Nunca lanza excepcion."""
    if not _chat_id_valido(chat_id):
        log.error(
            "chat_id de destino invalido (%r). No se envio nada. Revisa "
            "TELEGRAM_CHAT_ID en Render y de donde se calcula el destino.",
            chat_id,
        )
        return False
    ok = True
    degradado = False
    for trozo in _trocear(texto):
        cuerpo = _sanear_html(trozo) if html_mode else trozo
        payload = {"chat_id": chat_id, "text": cuerpo}
        if html_mode:
            payload["parse_mode"] = "HTML"
            payload["disable_web_page_preview"] = True
        res = _pedir("sendMessage", payload)
        if res is None and html_mode:
            # Reintento en texto plano: si falla por HTML, asi al menos llega.
            # Pero se avisa, porque llega SIN negritas, SIN links y SIN
            # cursivas. Antes esto pasaba inadvertido: enviar() devolvia True
            # y el usuario recibia un bloque gris creyendo que era normal.
            log.error(
                "Telegram rechazo el HTML; se reenvia en texto plano. Llega, "
                "pero sin formato ni links. Revisa _sanear_html y las "
                "constantes de kyo.py."
            )
            plano = {"chat_id": chat_id, "text": cuerpo}
            res = _pedir("sendMessage", plano)
            degradado = True
        if res is None:
            ok = False
    return ok and not degradado


# Etiquetas que Telegram acepta en parse_mode=HTML. Todo lo demas se escapa:
# un "<" suelto es un intento de etiqueta y hace que Telegram rechace el
# mensaje ENTERO con un 400, sin enviar nada.
#
# Esto viene de un fallo real del 28-sep-2026: la firma de Kyomoto era
# "... (˶>ᴗ<˶) ⁾" y el "<" de "ᴗ<" lo leia Telegram como el inicio de una
# etiqueta. Resultado:
#   Bad Request: can't parse entities: Unsupported start tag "\u02f6)"
# La ficha no llegaba, y como enviar() reintenta en texto plano y devolvia
# True, el sistema decia "ok" mientras mandaba un bloque gris sin formato.
_ETIQUETAS_OK = re.compile(
    r"^</?([a-z][a-z0-9-]*)"
    r"(?:\s[^<>]*?)?/?>$",
    re.IGNORECASE,
)
# Las que Telegram acepta de verdad en parse_mode=HTML. Las demas, aunque
# tengan forma de etiqueta, se escapan: "unsupported start tag" tambien es un
# 400 que tira el mensaje entero.
_ETIQUETAS_TELEGRAM = {"b", "i", "u", "s", "a", "code", "pre",
                       "blockquote", "tg-spoiler"}


def _sanear_html(texto: str) -> str:
    """
    Deja el HTML en un estado que Telegram acepta, aunque el texto venga
    roto. Red de seguridad, no sustituto de _esc: los campos sueltos de
    Gemini siguen pasando por _esc, que escapa ampersand y comillas.

    Hace tres cosas:

    1. Escapa cualquier "<" que no abra una etiqueta de las que Telegram
       acepta. Ese era el fallo real: la firma "(\u02f6>\u1d17\u02f6)" traia un
       "<" suelto y Telegram rechazaba la ficha entera con
       "Unsupported start tag".
    2. Escapa tambien el ">" del mismo fragmento, para que se vea el texto tal
       cual y no dependa de que un ">" suelto sea legal.
    3. Cierra las etiquetas que se hayan quedado abiertas al final. Una "<b>"
       sin su "</b>" tambien es un 400, y aqui no se nota mirando el texto.

    No toca las entidades ya escapadas (&lt;, &gt;, &amp;): no empiezan por
    "<", y el recorrido solo mira los "<".
    """
    if "<" not in texto:
        return texto

    partes = []
    abiertas: list[str] = []
    i = 0
    largo = len(texto)
    while i < largo:
        j = texto.find("<", i)
        if j == -1:
            partes.append(texto[i:])
            break
        partes.append(texto[i:j])
        # Telegram corta en el primer ">", asi que se busca ese y no mas lejos.
        k = texto.find(">", j)
        if k == -1:
            partes.append("&lt;")
            i = j + 1
            continue

        trozo = texto[j:k + 1].strip()
        m = _ETIQUETAS_OK.match(trozo)
        nombre = (m.group(1) or "").lower() if m else ""
        cerrar = trozo.startswith("</")
        # Un "/>" final no abre nada: es un cierre disfrazado de apertura.
        auto_cierra = trozo.endswith("/>")

        if not m or nombre not in _ETIQUETAS_TELEGRAM:
            partes.append("&lt;" + texto[j + 1:k] + "&gt;")
        elif cerrar:
            if nombre in abiertas:
                # Anidamiento imperfecto como "<b>a<i>b</b>": el </b> cierra la
                # de fuera, pero la <i> de dentro se queda abierta y Telegram
                # rechaza el mensaje. Se emiten los cierres de lo que se
                # descarta antes del que cierra de verdad.
                while abiertas and abiertas[-1] != nombre:
                    partes.append(f"</{abiertas.pop()}>")
                if abiertas:
                    abiertas.pop()
                partes.append(trozo)
            else:
                partes.append("&lt;" + texto[j + 1:k] + "&gt;")
        else:
            if not auto_cierra:
                abiertas.append(nombre)
            partes.append(trozo)
        i = k + 1

    # Lo que quedo abierto se cierra aqui. Sin esto, "<b>texto" sin cierre
    # haria que Telegram rechazase el mensaje entero.
    while abiertas:
        partes.append(f"</{abiertas.pop()}>")
    return "".join(partes)


def _esc(valor) -> str:
    return html.escape(str(valor if valor is not None else ""), quote=False)


def _cantidad(valor) -> str:
    """Sin decimales si es entera. 50.0 se ve como 50, no como 50.0 EA."""
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return str(valor or "")
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.2f}"


def _usd(valor) -> str:
    if valor is None:
        return "N/E"
    return f"${valor:,.2f}"


MESES = ("ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic")


def _fecha_legible(bruto) -> tuple[str, str]:
    """
    Convierte el limite de ofertar a algo que se lee de un vistazo.

    Devuelve ("Vence en 36 dias", "4 nov 2026, 17:00 UTC-4"). El dato mas
    accionable de la ficha no puede quedar como "2026-11-04T17:00:00-04:00" en
    medio de un bloque de logistica: eso no lo lee nadie.

    Si la fecha no se puede interpretar, devuelve dos cadenas vacias y la
    ficha lo omite, en vez de ensuciar con un formato raro.
    """
    if not bruto:
        return "", ""
    from datetime import datetime, timezone

    texto = str(bruto).strip().replace("Z", "+00:00")
    dt = None
    for parseo in (datetime.fromisoformat,):
        try:
            dt = parseo(texto)
            break
        except (ValueError, TypeError):
            continue
    if dt is None:
        return "", ""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    ahora = datetime.now(timezone.utc)
    dias = (dt - ahora).days
    # "4 nov 2026, 17:00 UTC-4" montado a mano: "%-d" (dia sin cero) es una
    # extension de glibc y NO existe en Windows, donde da
    # "ValueError: Invalid format string". Kyomoto corre en Render (Linux)
    # pero el diagnostico corre en el portatil del usuario, y una función que
    # solo funciona en un sistema no es una función.
    zona = ""
    if dt.utcoffset() is not None:
        total = int(dt.utcoffset().total_seconds() // 60)
        signo = "+" if total >= 0 else "-"
        zona = f" UTC{signo}{abs(total) // 60:02d}"
    cuando = (
        f"{dt.day} {MESES[dt.month - 1]} {dt.year}, "
        f"{dt.hour:02d}:{dt.minute:02d}{zona}"
    )

    if dias < 0:
        return "⚠️ VENCIÓ", cuando
    if dias == 0:
        return "⏰ VENCE HOY", cuando
    if dias == 1:
        return "⏰ Vence mañana", cuando
    if dias <= 10:
        return f"⏰ Vence en {dias} días", cuando
    return f"🗓️ Vence en {dias} días", cuando


def _decision(a: dict) -> str:
    """
    La linea de triage: un vistazo y sabes si abrir el enlace o no.

    Se responde la pregunta que importa al leer un aviso "¿esto me sirve?":
    cuanto queda, cuanto gano y si el margen pasa mi piso. Sin esto habia que
    leer hasta el final para decidir.
    """
    neta = a.get("ganancia_neta_usd")
    margen = a.get("margen_neto_porcentaje")
    if neta is None:
        return ""
    umbral = config.MARGEN_NETO_MIN * 100
    if margen is None:
        return f"Te quedarías <b>{_usd(neta)}</b> netos."
    pasa = "pasa tu piso" if margen >= umbral else "⚠️ por debajo de tu piso"
    return (
        f"Te quedarías <b>{_usd(neta)}</b> netos "
        f"(<b>{margen:.1f}%</b> · {pasa} del {umbral:.0f}%)"
    )


# Abreviaturas de las agencias federales de EE.UU. que mas contratos publican.
# Se usan en la linea del producto, que tiene que caber en una sola linea: el
# nombre completo ("Department of the Air Force / Wright-Patterson AFB")
# duplicaria lo que dice la linea de agencia justo debajo.
_ABREVIATURAS = {
    "department of the air force": "USAF",
    "air force": "USAF",
    "department of the navy": "USN",
    "navy": "USN",
    "naval": "USN",
    "department of the army": "ARMY",
    "army": "ARMY",
    "marine corps": "USMC",
    "department of defense": "DOD",
    "defense logistics agency": "DLA",
    "defense health agency": "DHA",
    "national security agency": "NSA",
    "space force": "USSF",
    "coast guard": "USCG",
    "general services administration": "GSA",
    "general services": "GSA",
    "department of veterans affairs": "VA",
    "department of homeland security": "DHS",
    "department of energy": "DOE",
    "department of agriculture": "USDA",
    "department of commerce": "DOC",
    "department of justice": "DOJ",
    "department of the interior": "DOI",
    "department of transportation": "DOT",
    "department of state": "DOS",
    "national aeronautics and space administration": "NASA",
    "environmental protection agency": "EPA",
    "nuclear regulatory commission": "NRC",
    "national archives and records": "NARA",
    "government publishing office": "GPO",
    "small business administration": "SBA",
    "federal communications commission": "FCC",
    "bureau of prisons": "BOP",
    "drug enforcement administration": "DEA",
    "federal bureau of investigation": "FBI",
    "national science foundation": "NSF",
}

# Palabras que no cuentan para sacar iniciales: "Department of the" no es
# informacion, es relleno.
_RELLENO = {"of", "the", "for", "and", "de", "del", "la", "of"}


def _agencia_corta(agencia) -> str:
    """
    Abreviatura de la agencia para la linea del producto.

    Tres formas, en orden:
      1. La abreviatura conocida del diccionario. Cubre lo que de verdad
         publica contratos en SAM.gov.
      2. Una sigla que ya venga entre parentesis en el propio nombre, que es
         justo como lo escribe SAM.gov: "Department of the Navy (NAVSEA)".
      3. Iniciales de las palabras con contenido. Es la red de seguridad para
         una agencia que no este en el diccionario.

    Sin esto, la linea del producto seria larguisima con el nombre entero y
    mas el nombre entero otra vez en la linea siguiente.
    """
    if not agencia:
        return "GOV"
    texto = str(agencia).strip()
    bajo = texto.lower()

    # 1) diccionario
    for nombre, abreviado in _ABREVIATURAS.items():
        if nombre in bajo:
            return abreviado

    # 2) sigla ya presente entre parentesis
    import re as _re

    m = _re.search(r"\(([A-Z][A-Z0-9&./\- ]{1,14})\)", texto)
    if m:
        return m.group(1).strip()

    # 3) iniciales de las palabras con contenido
    palabras = [
        p for p in _re.findall(r"[A-Za-z]+", bajo)
        if p not in _RELLENO
    ]
    if not palabras:
        return "GOV"
    if len(palabras) == 1:
        return palabras[0][:5].upper()
    return "".join(p[0] for p in palabras[:5]).upper()


def _descripcion_corta(a: dict) -> str:
    """
    Una sola frase con el producto, el modelo, la cantidad y la especificacion
    clave, en ese orden. Todo en un parrafo: el diseno pedido tiene una sola
    linea de descripcion, y partirla en dos obligaba a leer de un bloque al
    otro.
    """
    partes = []
    producto = (a.get("producto") or "").strip()
    if producto:
        partes.append(producto)

    modelo = (a.get("modelo_especifico") or "").strip()
    cantidad = a.get("cantidad_total")
    unidad = (a.get("unidad_medida") or "").strip()

    extras = []
    if modelo:
        extras.append(modelo)
    if cantidad and unidad:
        extras.append(f"{_cantidad(cantidad)} {unidad}")
    elif cantidad:
        extras.append(f"{_cantidad(cantidad)} unidades")

    if extras:
        if partes:
            partes[0] = f"{partes[0]} ({', '.join(extras)})"
        else:
            partes.append("(" + ", ".join(extras) + ")")

    spec = (a.get("especificacion_tecnica_clave") or "").strip()
    if spec:
        if partes:
            partes[0] += f". {spec}"
        else:
            partes.append(spec)

    return ". ".join(partes) if partes else "Sin descripcion publicada."


def _usd_texto(valor) -> str:
    """
    Monto con el signo y los decimales, SIN la palabra USD detras.

    El formato pedido escribe "~$85,000.00 USD" y "$74,500.00 USD", o sea el
    texto y la unidad separadas. _usd() devuelve solo "$85,000.00", asi que
    aqui se añade el separador para poder pegarle el " USD" que pide el
    diseno sin que quede pegado.
    """
    if valor is None:
        return "N/E"
    return f"${valor:,.2f}"


_SEPARADOR = "━━━━━━━━━━━━━━━━━━━━"


def _bloque_financiero(a: dict) -> list[str]:
    """
    El resumen financiero en dos columnas alineadas.

    Etiqueta a la izquierda, cifra a la derecha, y la ganancia neta separada
    al final con una raya, porque es el unico numero que importa y el unico
    que sale cerrado.

    Va en <pre> por una razon concreta: sin <pre>, el ancho de una celda es
    el de su contenido mas largo, y en cuanto una etiqueta pasa de la
    siguiente, la cifra baja de linea. Las columnas dejan de alinearse
    justo cuando hay mas cantidad que comparar, que es cuando mas falta
    hacen. En <pre> el ancho lo fija el texto y las columnas se caen
    siempre en el mismo sitio.
    """
    presupuesto = a.get("valor_contrato_usd")
    costo = a.get("costo_total_usd")
    oferta = a.get("precio_oferta_sugerido_usd")
    margen = a.get("margen_neto_porcentaje")

    filas = [
        ("Presupuesto govt.",
         f"~{_usd_texto(presupuesto)} USD" if presupuesto is not None else "N/E"),
        ("Costo proveedor",
         f"~{_usd_texto(costo)} USD" if costo is not None else "N/E"),
        ("Precio a ofertar",
         f"{_usd_texto(oferta)} USD" if oferta is not None else "N/E"),
    ]

    L: list[str] = ["💰 <b>Resumen financiero</b>", "<pre>"]
    for etiqueta, valor in filas:
        L.append(f"{etiqueta:<17}{valor:>18}")
    L.append("\u2500" * 35)
    # El "~" no aparece en la ganancia ni en el precio ofertado: los dos se
    # calculan aqui, no salen de una estimacion del modelo. Poner "~" seria
    # mentir sobre lo unico que sale cerrado.
    L.append(f"{'GANANCIA NETA':<17}{_usd_texto(a.get('ganancia_neta_usd')):>18}")
    if margen is not None:
        L.append(f"{'Margen neto':<17}{f'{margen:.1f}%':>18}")
    L.append("</pre>")
    return L


def _bloque_escenarios(a: dict) -> list[str]:
    """
    Los tres escenarios tacticos (100% / 97% / 95% del presupuesto), con su
    precio, su ganancia neta y su margen, ya con el factoring descontado.

    Va en su PROPIO <pre>, justo DESPUES del resumen financiero. No se funde
    con el de arriba porque son dos preguntas distintas: el resumen dice lo
    que hay, la tabla dice lo que se puede hacer con ello. Mezclarlas en una
    sola tabla obligaria a leer dos cosas como si fueran la misma, y el
    lector tiene diez segundos.

    Se pone despues (nunca antes) a proposito: test_formato mide el primer
    <pre> del mensaje para comprobar que las columnas del resumen cuadran,
    y si este bloque se adelantara, esa medida se estaria haciendo sobre la
    tabla equivocada.

    Sin datos no hay bloque. Una tabla con "N/E" en cada celda no ayuda a
    decidir: estorba.
    """
    import sensibilidad

    filas = a.get("escenarios_oferta") or sensibilidad.escenarios(
        a.get("valor_contrato_usd"), a.get("costo_total_usd"))
    pintadas: list[str] = []
    for f in filas or []:
        try:
            pintadas.append(
                f"{f['pct']:g}%".ljust(9)
                + _usd_texto(f["precio"]).rjust(12)
                + _usd_texto(f["ganancia_neta"]).rjust(13)
                + f"{f['margen']:.1f}%".rjust(8))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            # Una fila mal formada no tira el bloque entero: se salta.
            continue
    if not pintadas:
        return []

    L: list[str] = ["📊 <b>Escenarios de oferta (sensibilidad)</b>", "<pre>",
                    f"{'Escenario':<9}{'Precio':>12}"
                    f"{'Ganancia':>13}{'Margen':>8}"]
    L.extend(pintadas)
    L.append("</pre>")
    # El tipo de factoring se escribe AQUI en vez de hardcodear 3.5%: sale de
    # config, que lo lee del entorno. Si el operador cambia FACTORING_PCT,
    # la nota cambia con el y no se queda mintiendo en la ficha.
    L.append(
        f"<i>Factoring dinámico {config.FACTORING_PCT * 100:g}% descontado "
        "en cada escenario · ganancia neta sobre el precio ofertado · "
        "base: presupuesto govt.</i>")
    return L


def _bloque_distribuidores(d: dict) -> list[str]:
    """
    Los 3 distribuidores candidatos, cada uno con su busqueda de verificacion.

    Cada nombre lleva su enlace de Google, porque el nombre SOLO es una
    sugerencia hasta que se abre el enlace. Kyomoto no muestra ni correo ni
    telefono: no hay forma de comprobar un contacto generado por un modelo, y
    escribir a una direccion inventada hace que el cliente queme su
    reputacion con la empresa equivocada. El nombre es barato de comprobar y
    el enlace lo deja comprobar en un clic.

    Si no hay candidatos de confianza, se degrada a la busqueda general, que
    es lo que hay. Nunca se muestra un bullet vacio.
    """
    L = ["🏭 <b>Distribuidores a verificar (USA)</b>"]
    # El Ship-To, para que se vea DE DONDE sale el orden de la lista. Sin
    # esta linea el lector ve unos nombres antes que otros y no sabe si es
    # una recomendacion o una casualidad; con ella, sabe que es por region.
    _dest = d.get("destino") or {}
    _sitio = ", ".join(x for x in (_dest.get("ciudad"), _dest.get("estado"))
                       if x)
    if _sitio:
        _linea = f"📍 Ship-To: {_esc(_sitio)}"
        if _dest.get("region"):
            _linea += f" · Región: {_esc(_dest['region'])}"
        L.append(_linea)
        if d.get("ordenado_por_cercania"):
            L.append("<i>Ordenados por cercanía a la región del destino "
                     "(FOB Destination).</i>")
    candidatos = d.get("candidatos") or []
    if not candidatos:
        etiqueta, url = d["principal"]
        L.append(f'<a href="{_esc(url)}">{_esc(etiqueta)}</a>')
        L.append("")
        L.append("<i>Sin candidatos de confianza para este producto: "
                 "usa la búsqueda general.</i>")
        return L

    for n, c in enumerate(candidatos, 1):
        linea = f'{n}. <a href="{_esc(c["url"])}"><b>{_esc(c["nombre"])}</b></a>'
        if c.get("tipo"):
            linea += f' <i>({_esc(c["tipo"])})</i>'
        L.append(linea)
        if c.get("porque"):
            L.append(f'   <i>{_esc(c["porque"][:150])}</i>')

    L.append("")
    L.append("<i>Nombres sugeridos: ábrelos y confirma que venden este "
             "producto antes de escribir. Kyomoto no da correos ni teléfonos "
             "porque no puede verificarlos.</i>")
    return L


# =====================================================================
# RFQ LISTO PARA ENVIAR (COPY-PASTE)
# =====================================================================
#
# El correo va dentro de <pre>: en Telegram eso ES el bloque de codigo, se
# copia de un toque y lo que sale es texto plano. Por eso dentro del <pre>
# no se pone ni un <b> ni un <i>: si fuera asi, al pegar el correo en el
# gestor de correo llegarian las etiquetas pegadas al texto.
#
# Va dirigido al distribuidor #1 de la lista (el geo-matched de la Fase 3),
# pero sin linea "To:": Kyomoto no inventa correos, y la razon esta escrita
# en _bloque_distribuidores. Quien recibe la ficha pega la direccion que el
# mismo verifico en la web del distribuidor. Por eso no hay ni un "@" en el
# mensaje entero, que es ademas lo que comprueban test_fase3 y
# test_cadena_distribuidores.
_MESES_EN = ("January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December")

# Mismo patron que pdf_generator._RE_NSN. No se importa ese modulo porque
# arrastra reportlab a un aviso de Telegram que tiene que salir rapido.
_RE_NSN = re.compile(r"\b(\d{4}-\d{2}-\d{3}-\d{4})\b")

# "P/N 6140-01", "PN: 12345", "Part No. AB-12". Exige un separador (":" o
# "#" o un espacio) detras de la abreviatura: sin eso, "PN" se engancharia a
# cualquier palabra que simplemente empiece por esas dos letras.
_RE_PARTE = re.compile(
    r"\b(?:P\s*/\s*N|PN|Part\s*(?:No\.?|Number))\s*(?:[:#]\s*|\s+)"
    r"([A-Za-z0-9][A-Za-z0-9._/-]{2,})", re.IGNORECASE)

# Firma del RFQ, tal cual la pidio el cliente. El EIN no es un secreto (va
# en SAM.gov y en toda factura) y sin el el correo no identifica a la
# empresa ante el distribuidor.
FIRMA_RFQ = ("Bastian Cordero\n"
             "Managing Member | L.A.M.B Logistics LLC\n"
             "Albuquerque, NM 87110 | EIN: 32-0868469")


def _fecha_ingles(bruto) -> str:
    """
    La fecha de cierre como va en un correo: "November 4, 2026, 17:00 UTC-04".

    No se puede reutilizar _fecha_legible, que devuelve "4 nov 2026": el RFQ
    esta en ingles comercial y una fecha en espanol en medio de un correo en
    ingles es justo lo que la politica de cero espanol no permite.

    Si la fecha no se lee, devuelve vacio y el bloque omite la linea entera.
    Una fecha mal leida es peor que ninguna, porque contra ella cotiza el
    distribuidor.
    """
    if not bruto:
        return ""
    from datetime import datetime, timezone

    texto = str(bruto).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(texto)
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    cuando = f"{_MESES_EN[dt.month - 1]} {dt.day}, {dt.year}"
    if dt.hour or dt.minute:
        # La hora solo si la trae el aviso. Una fecha de solo dia pinta
        # "00:00 UTC+00" y eso es ruido que el distribuidor lee como un
        # cierre a medianoche que no existe.
        zona = ""
        if dt.utcoffset() is not None:
            total = int(dt.utcoffset().total_seconds() // 60)
            signo = "+" if total >= 0 else "-"
            zona = f" UTC{signo}{abs(total) // 60:02d}"
        cuando += f", {dt.hour:02d}:{dt.minute:02d}{zona}"
    return cuando


def _identificador_tecnico(a: dict) -> str:
    """
    NSN o numero de parte escrito en el aviso, o el modelo exacto si no hay.

    Nada de esto se inventa: un numero de parte fabricado hace que el
    distribuidor cotice una pieza que no es, y el que pierde el tiempo es el
    cliente. Se recorre todo buscando un NSN antes que un PN, porque el NSN
    es el identificador que el proveedor puede buscar sin preguntar nada.

    OJO: NO se mira "contacto". Ese campo trae el correo del oficial de
    contratacion, y de ahi saldria un "@" que tira los tests de la ficha y,
    sobre todo, un destinatario que no es el distribuidor.
    """
    textos = [str(a.get(c) or "")
              for c in ("especificacion_tecnica_clave", "modelo_especifico",
                        "title")]

    for texto in textos:
        m = _RE_NSN.search(texto)
        if m:
            return f"NSN {m.group(1)}"
    for texto in textos:
        m = _RE_PARTE.search(texto)
        if m:
            return f"P/N {m.group(1)}"
    # El modelo tal cual, sin etiqueta inventada: es lo unico que queda y
    # sigue identificando la pieza ante el distribuidor.
    return str(a.get("modelo_especifico") or "").strip()


def _cortar_palabra(texto: str, limite: int) -> str:
    """
    Corta en un limite, pero en el borde de una palabra.

    "Batteries, Lead-Acid, 12 Volt, Group 31," cortado a 40 a secas deja el
    asunto del correo a media frase con la coma colgando, y eso se lee como
    un error de copia. Aqui corta en el ultimo espacio y cierra con puntos.
    """
    if len(texto) <= limite:
        return texto
    corte = texto[:limite]
    espacio = corte.rfind(" ", 15)
    if espacio > 15:
        corte = corte[:espacio]
    return corte.rstrip(" ,;-.") + "..."


def _bloque_rfq(a: dict, d: dict) -> list[str]:
    """
    El RFQ en ingles comercial estadounidense, listo para copiar y pegar,
    con el distribuidor #1 de la lista.

    Se arma SOLO con datos que ya estan en la ficha: solicitud, especificacion
    tecnica, cantidad, cierre, Ship-To y el primer candidato geo-matched. No
    hay ninguna llamada a Gemini por detras, y por eso la firma, el EIN y las
    cantidades no pueden salir inventados por un modelo.

    Todo el cuerpo va dentro de <pre> y como texto plano, para que lo que se
    copia sea el correo entero y no un monton de etiquetas HTML pegadas.

    Nunca se pinta un campo vacio ni una linea de relleno: si falta la fecha
    se omite el cierre, si falta el destino no se nombra destino ninguno, y
    si no hay candidatos el saludo es generico. Un RFQ con datos de relleno
    hace que el distribuidor responda mal una vez y no responda a la segunda.
    """
    # --- Con quien se habla -----------------------------------------------
    candidatos = d.get("candidatos") or []
    nombre = str((candidatos[0] if candidatos else {}).get("nombre")
                 or "").strip()[:40]
    saludo = f"Dear {nombre} Sales Team," if nombre else "Dear Sales Team,"

    # --- Que se cotiza -----------------------------------------------------
    sol = str(a.get("solicitation") or "").strip()
    titulo = str(a.get("title") or "").strip()[:120]
    ident = _identificador_tecnico(a)
    spec = str(a.get("especificacion_tecnica_clave") or "").strip()[:140]
    fecha = _fecha_ingles(a.get("limite"))

    cantidad = a.get("cantidad_total")
    unidad = str(a.get("unidad_medida") or "").strip()
    cantidad_txt = f"{_cantidad(cantidad)} {unidad}".strip() if cantidad else ""

    # --- A donde llega -----------------------------------------------------
    # Solo lo que la Fase 3 pudo leer del Ship-To. Sin ciudad ni estado no
    # se nombra destino: una direccion a medias hace cotizar un flete que
    # despues no se usa, y el pais solo jamas sirve de destino.
    destino = d.get("destino") or {}
    sitio = ", ".join(x for x in (destino.get("ciudad"), destino.get("estado"))
                      if x)
    region = str(destino.get("region") or "").strip()
    # La region entre parentesis DETRAS de la ciudad/estado, nunca separada
    # por coma: "Norfolk, VA, (South)" se lee como un error de tipeo y
    # "Norfolk, VA (South)" es como se escribe de verdad.
    ship_to = f"{sitio} ({region})" if (sitio and region) else sitio

    cuerpo: list[str] = []

    # --- Asunto ------------------------------------------------------------
    asunto = "RFQ"
    if sol:
        asunto += f" - {sol}"
    if titulo:
        asunto += f" - {_cortar_palabra(titulo, 40)}"
    cuerpo.append(f"Subject: {asunto}")
    cuerpo.append("")
    cuerpo.append(saludo)
    cuerpo.append("")
    cuerpo.append("Requesting a quotation for a U.S. federal solicitation:")
    cuerpo.append("")

    # --- Los datos, en columna --------------------------------------------
    # Se trocea la linea COMPLETA, con el etiquetado ya puesto: si se
    # troceara solo el valor, la primera linea saldria con 16 caracteres de
    # mas y se saldria de las 72 columnas del bloque.
    def campo(etiqueta: str, valor: str) -> None:
        if not valor:
            return
        linea = f"  {etiqueta.ljust(14)}{valor}"
        cuerpo.extend(textwrap.wrap(linea, width=72,
                                    subsequent_indent=" " * 16) or [linea])

    campo("Solicitation:", sol)
    campo("Item:", titulo)
    campo("Part / Model:", ident)
    campo("Specs:", spec)
    campo("Quantity:", cantidad_txt)
    campo("Closing:", fecha)
    campo("Ship-To:", ship_to)

    # --- Lo que se pide ----------------------------------------------------
    if sitio:
        destino_txt = ship_to
    else:
        destino_txt = "the delivery location stated in the solicitation"
    cuerpo.append("")
    cuerpo.append("Please quote:")
    cuerpo.append("1. Unit price and total wholesale price for the required "
                  "quantity.")
    cuerpo.append("2. Stock availability and estimated lead time.")
    cuerpo.extend(textwrap.wrap(
        f"3. Ground shipping cost, FOB Destination, to {destino_txt}. "
        "Please advise if freight is waived at this volume.",
        width=72, subsequent_indent="   "))
    cuerpo.extend(textwrap.wrap(
        "4. Technical compliance, with a Certificate of Conformity (CoC) or "
        "OEM traceability if this is a technical or military spare part.",
        width=72, subsequent_indent="   "))
    cuerpo.append("")
    # La frase de los dos dias: con la fecha si la hay (apunta a la linea de
    # arriba), sin ella si no, porque decir "la fecha de cierre" sin poder
    # decirla deja al distribuidor sin el dato que le piden.
    if fecha:
        frase = ("Please send your quotation at least two (2) days before "
                 "the closing date above, so we can submit our federal "
                 "offer on time.")
    else:
        frase = ("Please send your quotation at least two (2) days before "
                 "the solicitation closing date, so we can submit our "
                 "federal offer on time.")
    cuerpo.extend(textwrap.wrap(frase, width=72))
    cuerpo.append("")
    cuerpo.append("Thank you,")
    cuerpo.append("")
    cuerpo.extend(FIRMA_RFQ.split("\n"))

    # --- Se pinta ----------------------------------------------------------
    # Se escapa linea a linea DESPUES de trocear: si se escapara antes,
    # textwrap partiria un "&amp;" por la mitad y Telegram mostraria el
    # trozo roto.
    L: list[str] = ["✉️ <b>RFQ Listo para Enviar (Copy-Paste)</b>", "<pre>"]
    L.extend(_esc(linea) for linea in cuerpo)
    L.append("</pre>")
    L.append("<i>Dirigido al distribuidor #1 de la lista: confirma que vende "
             "el producto antes de escribir.</i>")
    return L


def formatear_analisis(a: dict) -> str:
    """
    La ficha, en formato ejecutivo.

    Estructura de arriba abajo, pensada para decidir en diez segundos si se
    abre el enlace o no:

        SEPARADOR
        Que es, de quien es, cuando cierra      <- identidad y urgencia
        SEPARADOR
        Producto
        SEPARADOR
        Resumen financiero en tabla             <- las cifras, alineadas
        SEPARADOR
        Estrategia, con el porque en la misma linea
        SEPARADOR
        Los 3 distribuidores, con su verificacion
        SEPARADOR
        Enlace a SAM.gov
        Cierre

    Regla de oro, y no es negociable: la cuenta se sostiene. Si el bot dice que
    vas a ofertar $74,500, la ganancia se mide contra $74,500 y no contra el
    presupuesto del gobierno, porque lo que entra a tu cuenta es lo que
    ofertaste. Medirla contra el presupuesto infla el margen y lleva a
    ofertar por debajo de tu propia ganancia.
    """
    import distribuidores
    import kyo

    L: list[str] = [
        "✨ <b>¡Amo, oportunidad nueva y bien armada!</b> (≧◡≦)",
        _SEPARADOR,
        "",
    ]

    # --- Que es, de quien, y cuanto queda ---
    # La abreviatura va delante del producto para que la linea se escanee de
    # un vistazo: "USAF - Generadores 50kW" dice mas que el nombre entero, y el
    # nombre entero va justo debajo, que es donde toca leerlo.
    #
    # Si el titulo ya empieza por esa misma abreviatura, no se antepone: sale
    # "USAF - USAF - Suministro de Repuestos", que es ruido. El nombre del
    # titulo tal cual viene de Gemini y a veces ya trae la agencia.
    _corta = _agencia_corta(a.get("agencia"))
    _titulo = str(a["title"]).strip()
    if _titulo[:len(_corta) + 1].upper().startswith(_corta + "-") or \
       _titulo[:len(_corta) + 1].upper().startswith(_corta + " "):
        L.append(f"📦 <b>{_esc(_titulo)}</b>")
    else:
        L.append(f"📦 <b>{_esc(_corta)} - {_esc(_titulo)}</b>")
    L.append(f"🏛️ {_esc(a['agencia'])}")
    L.append(f"🔢 Solicitud: <code>{_esc(a['solicitation'])}</code>")

    # La fecha sube aqui porque es lo que decide si hay tiempo de ofertar, y
    # en el formato anterior estaba debajo de todo el bloque economico.
    #
    # OJO: _fecha_legible YA devuelve su propio emoji ("⏰ Vence en 3 días",
    # "🗓️ Vence en 35 días"). Anteponer otro aqui saca "⏰ 🗓️ Vence en 35
    # días", que es un emoji pegado a otro. Se usa la cadena tal cual.
    _vence, _cuando = _fecha_legible(a.get("limite"))
    if _vence:
        L.append(f"<b>{_esc(_vence)}</b> · {_esc(_cuando)}")

    # Con la regla de +2 del 30-sep, saber si el contrato esta reservado a
    # pequenas empresas cambia la decision, y ese dato no aparecia.
    _sa = str(a.get("set_aside") or "").strip()
    if _sa and "sin set-aside" not in _sa.lower():
        L.append(f"🏷️ {_esc(_sa)}")
    L.append(_SEPARADOR)
    L.append("")

    # --- Producto ---
    L.append("📝 <b>Producto</b>")
    L.append(_esc(_descripcion_corta(a)))
    L.append("")
    L.append(_SEPARADOR)
    L.append("")

    # --- Analisis financiero ---
    L.extend(_bloque_financiero(a))
    # Los escenarios van justo detras del resumen, sin separador propio:
    # son la continuacion natural de las mismas cifras (mismo presupuesto,
    # mismo costo), y un separador entre los dos los presentaria como dos
    # temas distintos cuando son la misma pregunta vista desde otro angulo.
    _escenarios = _bloque_escenarios(a)
    if _escenarios:
        L.append("")
        L.extend(_escenarios)
    L.append("")
    L.append(_SEPARADOR)
    L.append("")

    # --- Estrategia de oferta ---
    # La explicacion va en la MISMA linea, entre parentesis: es el "por que" de
    # ese numero, y separarla obligaba a saltar de un bloque a otro.
    _expl = (a.get("razonamiento_oferta") or a.get("estrategia_oferta") or "").strip()
    L.append("🎯 <b>Estrategia de oferta</b>")
    # La frase de estrategia la calcula Python y empieza por "Ofertar a X USD",
    # con el precio dentro (ver gemini_analyzer.calcular_estrategia_oferta). Si
    # ademas la plantilla antepone su propio "Ofertar a X USD", el precio sale
    # dos veces seguidas:
    #
    #   Ofertar a $74,500.00 USD (Ofertar a 74,500.00 USD. Con este monto...)
    #
    # Cuando la explicacion ya trae el precio, se muestra ella sola. El texto
    # del modelo antes no lo traia y por eso se anteponia; ahora la logica es
    # la misma pero se detecta en vez de suponer.
    if _expl.lower().startswith("ofertar a"):
        L.append(_esc(_expl[:300]))
    else:
        _linea = f'· Ofertar a <b>{_usd_texto(a["precio_oferta_sugerido_usd"])} USD</b>'
        if _expl:
            _linea += f" ({_esc(_expl[:300])})"
        L.append(_linea)
    L.append(_SEPARADOR)
    L.append("")

    # --- Distribuidores ---
    d = distribuidores.para_oportunidad(
        a.get("busquedas_distribuidores") or [],
        a.get("producto") or "",
        a.get("lugar_entrega") or "",
        a.get("query_google_proveedores") or "",
        a.get("distribuidores_candidatos") or [],
    )
    L.extend(_bloque_distribuidores(d))
    L.append("")
    L.append(_SEPARADOR)
    L.append("")

    # --- Aviso que hay que leer si aplica ---
    if a.get("sin_descripcion"):
        L.append("⚠️ <i>SAM.gov no publicó descripción; las cifras con «~» son "
                 "estimaciones. Lee el aviso antes de ofertar.</i>")
        L.append("")

    # --- Enlace ---
    if a.get("ui_link"):
        L.append(f'🔗 <a href="{_esc(a["ui_link"])}"><b>Ver ficha en SAM.gov</b></a>')
    else:
        L.append("🔗 Ver ficha en SAM.gov")

    # --- RFQ listo para enviar (copy-paste) ---
    # Va como ULTIMO bloque de contenido, detras del enlace: el enlace es la
    # verificacion previa y el RFQ es la accion que se toma con la ficha en
    # la mano. El cierre de Kyomoto sigue siendo lo ultimo del mensaje.
    L.append("")
    L.append(_SEPARADOR)
    L.append("")
    L.extend(_bloque_rfq(a, d))

    L.append("")
    L.append(f"<i>{kyo.CIERRE}</i>")
    # Ultima red: aunque alguien escribiera un "<" a mano en una constante,
    # la ficha sale con el HTML valido. Sin esto, un solo angulo suelto
    # rechazaba el mensaje ENTERO con un 400 y la ficha no llegaba.
    return _sanear_html("\n".join(L))


def aviso_error(chat_id: str, titulo: str, detalle: str) -> None:
    cuerpo = (
        f"🚨 <b>{_esc(titulo)}</b>\n\n"
        f"<pre>{_esc(detalle[:2500])}</pre>\n\n"
        "<i>Kyomoto necesita que revises la configuracion.</i>"
    )
    enviar(chat_id, cuerpo, html_mode=True)
