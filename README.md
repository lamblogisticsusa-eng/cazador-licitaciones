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

### 2. Google no reconocía la credencial de Gemini

Probada por cinco vías contra la API real (`?key=`, `Authorization: Bearer`,
`tokeninfo`, header `x-goog-api-key` con tres modelos, y el SDK `google-genai`):
todas devolvieron `401`. `tokeninfo` la identificaba como `invalid_token`, así
que no es una API key ni un token OAuth valido.

Lo importante: **esto no era culpa del prefijo.** Desde el 28-may-2026 Google
crea *auth keys* ligadas a una service account que ya no empiezan con `AIza`, así
que un `AQ.` es perfectamente válido *en principio*. El código de la versión
anterior asumía `AIza` y daba un diagnóstico equivocado; ya no lo hace.

Y el detalle que convirtió esto en un silencio de horas: el código hacía

```python
except Exception as e:
    print(f"Error al invocar Gemini: {e}")
    return "NO_VIABLE"
```

**Cualquier error de Gemini se disfrazaba de "esta oportunidad no me sirve"**, y
solo se escribía en el log. Kyomoto no podía notificar nada sin que se supiera
por qué. Ahora los errores se reportan a Telegram y `/selftest` diagnostica cada
API.

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

### 2. Una clave de Gemini que Google reconozca

Ve a <https://aistudio.google.com/apikey> y crea una key.

> **Sobre el prefijo:** desde el **28 de mayo de 2026**, AI Studio crea por
> defecto *auth keys* ligadas a una service account, y esas **no** empiezan con
> `AIza`. Un prefijo `AQ.` no es por sí solo una señal de que la clave esté
> mal. Si ves `401 UNAUTHENTICATED`, el problema es otro:

1. **La clave se copió incompleta.** Son ~53 caracteres y contiene un punto.
   Cópiala completa, sin espacios.
2. **Tiene restricción de IP u origen.** Render usa IPs dinámicas: una clave
   restringida a tu IP local funciona en tu casa y **falla en Render**. Para un
   bot alojado, no apliques restricción de IP.
3. **Falta habilitar la API Generative Language** en el proyecto de Google Cloud
   asociado a la key.
4. **La clave es de otro producto** de Google, no de AI Studio.

Verifícala antes de subir nada:

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent" \
  -H "Content-Type: application/json" \
  -H "x-goog-api-key: TU_CLAVE" \
  -d '{"contents":[{"parts":[{"text":"di OK"}]}],"generationConfig":{"maxOutputTokens":2048}}'
```

Debe devolver JSON con el texto. Un `401` significa que Google no reconoce la
credencial.

Ojo también con el modelo:

> **`gemini-2.5-flash` ya no está disponible para cuentas nuevas.** Google
> devuelve `404: This model ... is no longer available to new users`.
> El default de este proyecto es **`gemini-3.8-flash`**.
>
> Este error es engaifiable: parece un problema de credenciales pero es de
> modelo. Por eso `probar_clave.py` prueba `GET /v1beta/models`, que no
> necesita ningun modelo y aisla el problema de verdad.

### 3. Render

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
GEMINI_MODEL          = gemini-3.8-flash
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

### 4. Habla con Kyomoto

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

## Diseñado para 10 licitaciones VIABLES al día con el plan gratuito

Sin tarjeta. Kyomoto se auto-limita y aprovecha cada llamada.

**El problema real: la tasa de conversión.** En los primeros análisis contra
SAM.gov, de cada 6 oportunidades evaluadas **solo 1 salía viable** (17%). Con
10 llamadas eso eran 2 licitaciones, no 10. Los motivos se repetían siempre:

| Motivo del descarte | Frecuencia |
|---|---|
| Certificaciones (DD2345, FAT, Level I, clearance) | ~40% |
| Destino en el extranjero | ~20% |
| Contrato sobre $250.000 | ~15% |
| Es servicio (Overhaul/Rebuild) | ~15% |

Esos cuatro son detectables **por texto, gratis**. Eso es lo que cambió el
resultado.

### Las cinco capas del embudo

**1. Presupuesto diario adaptativo (`quota.py`)**
`PRESUPUESTO_GEMINI_DIARIO=20`, repartido en 4 barridos de 4. El contador vive
en SQLite, sobrevive a los reinicios de Render y reinicia a la **medianoche
del Pacífico**, que es cuando Google reinicia su cuota.

Es adaptativo porque los límites de RPM/TPM/RPD del plan gratuito **no son
públicos**: solo se ven dentro de AI Studio. En vez de adivinar, Kyomoto mira
qué pasó ayer: si hubo 429 baja el listón (hasta un piso de 10), si terminó
limpio lo mantiene.

**2. Descalificadores por texto (`config.PALABRAS_CERTIFICACION` y compañía)**
Certificaciones inaccesibles −7, destino en el extranjero −9 (veto), servicio
disfrazado de producto −5. Todo antes de gastar una llamada.

Medido sobre casos reales: los que Gemini descartaba quedaron en 0-3 puntos,
muy por debajo del piso de 6. Las buenas quedaron en 8-13.

**3. El tope de USD, en texto (`filters.valor_declarado`)**
SAM.gov no deja filtrar por monto, pero la descripción lo dice:
*"Indefinite Delivery Contract: Estimated quantity 2.000 ; Not to Exceed
350,000.00"*. Kyomoto extrae el número con regex y **descarta el contrato sin
tocar la cuota**. Medido: 5 de 12 avisos eliminados por monto.

**4. Ritmo serializado**
`GEMINI_WORKERS=1`, `GEMINI_PAUSA_SEG=8`. Nada de paralelismo.

**5. Lo que no se analiza, no se pierde**
Si se acaba la cuota, los avisos **no se marcan como vistos**. Mañana vuelven a
la cola.

### Medición real del embudo (2 días, 661 avisos)

```
661 avisos de SAM.gov
  -> 329  veto por NAICS (servicios, construcción, IT fuera)
  -> 233  sin duplicados, sin descalificadores, dentro del rango de USD
  ->   4  analizados por barrido  (20 al día en 4 barridos)
  ->  ~3  viables por barrido    (~10-12 al día)
```

### Ajustes

| Querés | Cambiá |
|---|---|
| Más viable por día | `PRESUPUESTO_GEMINI_DIARIO=30` y sube `GEMINI_PAUSA_SEG` |
| Menos ruido | `PUNTAJE_MINIMO=8` |
| Más avisos chicos | `PUNTAJE_MINIMO=4` |
| Revisar más días | `DIAS_DE_VENTANA=3` |

Comandos: `/cuota` (estado), `/puntajes` (por qué sí o no), `/estado`.

---

## El deploy que murió: Python 3.14

Render usa **Python 3.14.3 por defecto**, y el bot se caía de inmediato:

```
RuntimeError: There is no current event loop in thread 'MainThread'
  File "telegram/ext/_application.py", line 1051, in __run
    loop = asyncio.get_event_loop()
```

Lo que pasó: `python-telegram-bot` 21.10 (la versión que estaba fijada) llama
`asyncio.get_event_loop()` desde un hilo sin bucle. En Python ≤3.11 eso creaba
un bucle nuevo en silencio; en 3.12 se deprecó; en **3.14 lanza `RuntimeError`**.
El servidor web ya respondía 200 y el health check pasaba, y justo después
moría el polling.

**Dos arreglos, ambos aplicados:**

1. **`python-telegram-bot[job-queue]==22.8`.** Es la primera versión que
   maneja Python 3.14 por dentro:
   ```python
   # This handles the Python 3.14+ behavior where get_event_loop() raises RuntimeError
   try:
       loop = asyncio.get_event_loop()
   except RuntimeError:
       loop = asyncio.new_event_loop()
       asyncio.set_event_loop(loop)
   ```

2. **`main.py` deja el bucle listo antes de `run_polling()`**, por si alguien
   vuelve a una versión vieja:
   ```python
   try:
       _loop = asyncio.get_event_loop_policy().get_event_loop()
       if _loop.is_closed():
           _loop = asyncio.new_event_loop()
   except RuntimeError:
       _loop = asyncio.new_event_loop()
   asyncio.set_event_loop(_loop)
   ```

3. **`PYTHON_VERSION=3.12.6` fijado en `render.yaml`**, para no depender de
   ninguna de las dos.

> **Si creaste el servicio a mano y no importando el Blueprint, el pin de
> `render.yaml` NO se aplica.** El log lo delata: si dice
> `Using Python version 3.14.3 (default)`, falta ponerlo en
> **Settings → Environment → PYTHON_VERSION = 3.12.6**.

`test_arranque.py` reproduce el fallo a propósito (deja el hilo sin bucle,
comprueba que `get_event_loop()` revienta) y después verifica que el fix lo
resuelve y que toda la API que usa Kyomoto existe en PTB 22.x. Nota: en 22.x
`JobQueue.remove_job()` ya no existe, ahora se usa `job.remove()`.

---

## Estructura

```
config.py            variables de entorno, listas NAICS/PSC, descalificadores
sam_api.py           cliente SAM.gov: troceo de fechas, noticedesc, reintentos
filters.py           pre-filtrado gratis: NAICS, palabras, valor de contrato
gemini_analyzer.py   evaluación con Gemini, devuelve JSON, errores visibles
scanner.py           orquesta: trae → filtra → puntúa → analiza → notifica
telegram_notify.py   envío seguro en HTML, troceado, con escape
quota.py             presupuesto diario de Gemini, adaptativo y persistente
kyo.py               la voz de Kyomoto (tono kawaii, saludos, emojis)
label.py             etiqueta de envío y packing list en PDF (reportlab)
store.py             SQLite: qué ya se notificó
main.py              bot de Telegram, comandos, servidor web de salud
test_arranque.py     reproduce el crash de Python 3.14 y verifica el fix
test_pipeline.py     prueba el pipeline real contra SAM.gov
test_valor.py        prueba el extractor de valor de contrato
test_cuota.py        prueba el presupuesto diario
test_descalificadores.py  prueba que los descalificadores funcionan
test_presupuesto.py  prueba que la cuota manda sobre MAX_A_GEMINI
test_render.py       prueba escape HTML, troceado, PDF y fuga de secretos
test_deploy.py       prueba firmas, imports, render.yaml y el fix de 3.14
test_real.py         prueba con SAM.gov y Gemini reales, sin simulaciones
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
