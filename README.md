# Kyomoto ✨

Asistente kawaii que busca licitaciones de **compra de productos** en SAM.gov y
te las manda a Telegram con el análisis financiero, precio unitario, ganancia
neta, términos para buscar distribuidores en USA y etiqueta de envío en PDF.

---

## Por qué tu versión anterior no respondía

Dos fallos independientes, ambos ocultos porque el código se tragaba las excepciones.

### 1. `description` de la API no es texto, es una URL

Esto es lo que devuelve `/v2/search`:

```json
"description": "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid=60aa8e3f..."
```

Tu código hacía `opp.get("description")` y le pasaba esa URL a Gemini como si
fuera la descripción del producto. Gemini no tenía nada que evaluar.

El texto real hay que pedirlo aparte, al endpoint `/v1/noticedesc`, que devuelve
HTML. `sam_api.obtener_descripcion()` hace esa llamada.

> Dato medido: ~50% de los avisos devuelven `404 "Description Not Found"`
> (concentrado en *Award Notice* y *Pre-solicitation*). Kyomoto lo maneja
> como texto vacío, avisa en la ficha que las cifras son estimaciones por
> título, y sigue adelante con el pre-filtrado por título.

### 2. Tu clave de Gemini no es válida

Probada directamente contra la API de Google:

```
401 UNAUTHENTICATED - ACCESS_TOKEN_TYPE_UNSUPPORTED
```

Tu clave empieza con `AQ.`. Las claves de Google AI Studio empiezan con
**`AIza`**. Lo que tienes parece un token de OAuth, no una API key de Gemini.

Y aquí está lo peligroso: tu código hacía

```python
except Exception as e:
    print(f"Error al invocar Gemini: {e}")
    return "NO_VIABLE"
```

Es decir, **cualquier error de Gemini se disfrazaba de "esta oportunidad no me
sirve"**. Como el error solo se escribía en el log de Render y el bot no te
avisaba, tu único síntoma era silencio. Kyomoto nunca pudo evaluar nada.

`gemini_analyzer.py` ahora lanza `GeminiError` y te lo reporta a Telegram. Y
`/selftest` te dice en 20 segundos qué parte está rota.

### 3. Other bugs fixed

| Bug | Efecto | Arreglo |
|---|---|---|
| `offset` de SAM.gov no pagina | Perderías ~87% de los avisos | Se trocea la fecha en bloques de 2 días, `limit=1000`, `offset=0` |
| `limit=250` sobre 10 días | Cubría ~4% del período | Paginación por bloques + límite configurable |
| `parse_mode="Markdown"` con texto de IA | Cualquier `_` o `*` hacía 400 y se perdía el mensaje | HTML con `html.escape()` |
| Sin trocear a 4096 chars | Mensajes largos se perdían en silencio | `_trocear()` + reintento en texto plano |
| APScheduler + `run_polling` | Dos event loops compitiendo | `JobQueue` de python-telegram-bot |
| `datetime.utcnow()` | Deprecado en Python 3.12+ | `datetime.now(timezone.utc)` |
| SQLite en Render free | Disco efímera, se olvida todo | Documentado abajo |
| `gunicorn`, `python-docx` | Dependencias muertas | Fuera de `requirements.txt` |
| Sin `noticeId` en la ficha | No se podía quitar duplicados | Deduplicación por título |

---

## Datos reales de SAM.gov (medidos en septiembre 2026)

- **~450 avisos/día** con `ptype=o`, 2083 en 14 días
- `ptype=o,a` → 7647 en 14 días
- `offset` funciona hasta ~10 y **corta en 0 a partir de ~50** con `limit=100`.
  Por eso no se pagina con offset: se trocea la fecha.
- Distribución de NAICS en una muestra de 1000 avisos:
  - `541` (TI/servicios) 15% → **excluir**
  - `236/237/238` (construcción) 18% → **excluir**
  - `311-339` (manufactura) 51% → **bien**
  - `421-425 / 441-454` (comercio) 7% → **bien**

`config.py` trae estas listas con esos números como base. Si quieres afinar,
mira `/puntajes` después de un escaneo.

---

## Puesta en marcha

### 1. Consigue una clave de Gemini que sí funcione

1. Ve a <https://aistudio.google.com/apikey>
2. **Create API key**
3. Debe empezar por `AIza`
4. Verifícala antes de subir nada:

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=TU_CLAVE" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"di OK"}]}],"generationConfig":{"maxOutputTokens":2048}}'
```

Si responde `UNAUTHENTICATED`, la clave está mal.

### 2. Render

| Campo | Valor |
|---|---|
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python main.py` |
| Health Check Path | `/healthz` |

> **Start Command debe ser `python main.py`, no `gunicorn main:app`.**
> El bot usa `run_polling()`, que necesita correr en primer plano. Con gunicorn
> el proceso muere de inmediato y el bot nunca responde.

Variables de entorno:

```
TELEGRAM_BOT_TOKEN   = 123456789:AA...
TELEGRAM_CHAT_ID     = 1862179491
SAM_API_KEY          = SAM-...
GEMINI_API_KEY       = AIza...          <-- empieza con AIza
```

Opcionales (tienen buen valor por defecto):

```
GEMINI_MODEL          = gemini-2.5-flash
DIAS_DE_VENTANA       = 3      # ventana de busqueda
MAX_A_GEMINI          = 25     # cuantas analiza Gemini
MAX_NOTIFICACIONES    = 10     # <= cuantas te llegan (lo que pediste: 5-10 diarias)
PUNTAJE_MINIMO        = 6      # sube para ver menos pero mas limpios
TOPE_USD              = 250000
MIN_USD               = 5000
INTERVALO_HORAS       = 24     # una vez al dia
WORKERS               = 8
DRY_RUN               = 0
```

### Cómo se eligen las 5-10 diarias

El flujo tiene cuatro filtros y el último es el que decide:

1. **SAM.gov** devuelve ~450 avisos por día. Kyomoto revisa una ventana de 3 días.
2. **Veto por NAICS** (`filtros.py`): gratis, inmediato. Si el sector es servicios,
   construcción o TI, se descarta aunque el título diga "supply".
3. **Puntaje** de 0 a ~12 con título, NAICS, PSC, palabras clave de producto,
   *set-aside* y días que quedan para postular. Piso en 6.
4. **Gemini** evalúa los 25 de mayor puntaje y aplica las reglas duras: producto
   físico, dentro del rango de USD, y que se pueda despachar a destino.
5. **Tope de 10**: si salen 18 viables, te llegan las 10 mejores. Las otras 8
   quedan marcadas como vistas para no repetir mañana.

Para afinar: baja `PUNTAJE_MINIMO` a 4 si te llegan menos de 5, súbelo a 8 si te
llegan demasiados. Mira `/puntajes` para ver por qué se cayó cada una.

`render.yaml` ya viene con todo esto. Si lo usas, Render te pregunta si quieres
importarlo: acepta y solo llenas las 4 claves.

### 3. Habla con Kyomoto

```
/selftest     -> te dice si Telegram, SAM.gov y Gemini responden
/escaneo      -> barrido manual
/escaneo 14   -> barrido de 14 días
/puntajes     -> por qué sí o no cada aviso
/etiqueta W9127N26QA145  -> PDF de la etiqueta
/estado       -> último barrido
/off  /on     -> pausar / reactivar
/reset        -> olvidar el historial
```

---

## Sobre el plan gratuito de Render

El plan free **duerme el servicio a los 15 minutos sin tráfico entrante**.
`run_polling()` genera tráfico saliente, que puede no contar, así que el bot
puede quedarse congelado y solo despertar cuando le llegue un mensaje (y
responder con 5-10 minutos de retraso).

Dos salidas:

1. **Plan `starter` (USD 7/mes)** — es lo que dice `render.yaml`. Para una
   herramienta de trabajo es lo razonable.
2. **Mantener free** y pegar un ping cada 10 minutos. Agrega un cron de
   UptimeRobot o cron-job.org contra `https://tu-servicio.onrender.com/healthz`.

Además, en plan free **el disco es efímero**: `licitaciones.db` se borra en
cada deploy y en cada despertar, y Kyomoto olvida qué ya te notificó. Para no
recibir duplicados, o usas plan pagado, o guardas el historial fuera
(Turso/Supabase). `store.py` está preparado para cambiar el backend, es un solo
módulo.

---

## Estructura

```
config.py            variables de entorno, listas NAICS/PSC, palabras clave
sam_api.py           cliente SAM.gov: troceo de fechas, noticedesc, reintentos
filters.py           pre-filtrado gratis por NAICS + palabras (costo 0 de API)
gemini_analyzer.py   evaluación con Gemini, devuelve JSON, errores visibles
scanner.py           orquesta: trae → filtra → puntúa → analiza → notifica
telegram_notify.py   envío seguro en HTML, troceado, con escape
label.py             etiqueta de envío y packing list en PDF (reportlab)
store.py             SQLite: qué ya se notificó
main.py              bot de Telegram, comandos, servidor web de salud
test_pipeline.py     prueba el pipeline real contra SAM.gov
test_render.py       prueba escape HTML, troceado, PDF y fuga de secretos
```

## Probarlo en local

```bash
pip install -r requirements.txt

# Lee las claves del entorno; nunca las pongas en el código.
$env:SAM_API_KEY="SAM-..."
$env:GEMINI_API_KEY="AIza..."
$env:TELEGRAM_BOT_TOKEN="123:AA..."
$env:TELEGRAM_CHAT_ID="1862179491"
$env:DRY_RUN="1"          # no envía mensajes, solo muestra en consola

python test_pipeline.py 5   # prueba búsqueda y filtrado
python test_render.py       # prueba render, troceado y PDF
python main.py              # arranca el bot
```

## Una advertencia de negocio

Filtra bien antes de ofertar. Dos cosas que Kyomoto **no** puede verificar por
API y tú tienes que mirar en el aviso:

- **Estar registrado en SAM.gov** es obligatorio para ofertar. Si no estás, no
  puedes presentarte. Y buena parte de las oportunidades son *set-aside* para
  small business **[doméstico]**: muchos exigen un negocio de EE.UU. Lee el
  aviso, porque si el set-aside es USA-only no puedes participar aunque el
  producto sea perfecto.
- **El tope de USD 250.000** es una decisión comercial, no un filtro de la API.
  SAM.gov no deja filtrar por monto, así que el análisis lo estima Gemini desde
  la descripción. Para contratos grandes, el valor de la descripción es una
  palabra como "NTE" (*not to exceed*). Trátalo como referencia, no como dato.
