"""
Revisa TODO el codigo del bot en busca de llamadas a metodos async de
Telegram que no se esperen.

POR QUE ESTO EN UN ARCHIVO APARTE

Porque el bug que se acaba de encontrar en /pdf no era un descuido puntual: era
un patron. Y el patron es silencioso. reply_text() sin await devuelve una
corrutina que Python se lleva sin executar, el chat no recibe nada, y el unico
rastro es un RuntimeWarning al final de la ejecucion que en un bot con mucho
log se pierde de vista.

Un revisor de codigo que buscase "await" a ojo no lo pilla en un archivo de 900
lineas. Uno que mire el arbol sintactico, si.

QUE HACE

Recorre el AST de cada .py y busca llamadas a los metodos de Telegram que son
corrutinas: reply_text, reply_document, edit_text, send_chat_action,
reply_photo, delete_message, send_message. Si la linea donde se llama no esta
precedida de await, la senala.

Por que mira la linea anterior y no el arbol de awaits: una llamada suelta
dentro de un bloque async es justamente el caso que se busca, y mirarlo por
AST completo seria mas correcto pero mas fragil de escribir sin un entorno de
prueba.
"""
import ast
import glob
import io
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

METODOS_ASYNC = {
    "reply_text", "reply_document", "edit_text", "reply_photo",
    "reply_audio", "reply_video", "send_chat_action", "send_message",
    "delete_message", "edit_message_text", "answer_callback_query",
}

FALLA = []
print("=" * 72)
print("LLAMADAS A METODOS ASYNC DE TELEGRAM SIN await")
print("=" * 72)
print()

for f in sorted(glob.glob("*.py")):
    try:
        texto = io.open(f, encoding="utf-8").read()
        arbol = ast.parse(texto, f)
    except SyntaxError as e:
        print("  %s no parsea: %s" % (f, e))
        continue

    lineas = texto.splitlines()
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        func = nodo.func
        if not isinstance(func, ast.Attribute) or func.attr not in METODOS_ASYNC:
            continue

        # Se mira si la instruccion completa de la que cuelga la llamada
        # empieza por await. Un Call puede estar dentro de un Await, que es
        # el caso bueno.
        esperando = False
        for padre in ast.walk(arbol):
            if isinstance(padre, ast.Await) and nodo in ast.walk(padre):
                esperando = True
                break
        if esperando:
            continue

        # Si no esta dentro de un Await, se mira la linea: a veces el await
        # esta en la linea anterior por el formato multilinea.
        idx = nodo.lineno - 1
        if idx > 0:
            anterior = lineas[idx - 1].strip()
            if anterior.endswith("await") or anterior.endswith("= await"):
                continue
            # Y si la propia linea lo tiene repartido, del tipo
            #   await (\n       update.message.reply_text(...)\n   )
            ventana = " ".join(lineas[max(0, idx - 3):idx + 1])
            if "await (" in ventana or "await update" in ventana \
                    or "await self." in ventana:
                continue

        FALLA.append((f, nodo.lineno, func.attr,
                      lineas[nodo.lineno - 1].strip()[:70]))

if FALLA:
    print("  Se han encontrado sin await:")
    print()
    for f, l, met, txt in FALLA:
        print("    %s L%d  .%s(...)" % (f, l, met))
        print("      %s" % txt)
else:
    print("  Ninguna. Todos los mensajes de Telegram se esperan.")

print()
print("=" * 72)
print("RESULTADO: %d llamada(s) sin await" % len(FALLA))
print("=" * 72)
sys.exit(1 if FALLA else 0)