import asyncio
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles  # Importieren Sie StaticFiles
# Make sure sse_starlette is installed
from sse_starlette.sse import EventSourceResponse

from app import mqtt_client  # Ihre mqtt_client.py Datei

app = FastAPI()

# Stellen Sie sicher, dass der Pfad zu Ihren Templates korrekt ist
templates = Jinja2Templates(directory="app/templates")

# MQTT Client starten (normalerweise beim Anwendungsstart)


@app.on_event("startup")
async def startup_event():
    # Den asyncio Event Loop an den MQTT Client übergeben
    mqtt_client.app_event_loop = asyncio.get_running_loop()
    mqtt_client.start_mqtt_client()
    print("FastAPI App gestartet und MQTT Client initialisiert.")


@app.on_event("shutdown")
def shutdown_event():
    mqtt_client.stop_mqtt_client()
    print("FastAPI App heruntergefahren und MQTT Client gestoppt.")

# Beispiel für eine Index-Seite


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    # Diese Route ist nur ein Beispiel, passen Sie sie an Ihre Bedürfnisse an
    with mqtt_client.latest_messages_lock:
        current_data = mqtt_client.latest_messages.copy()
    return templates.TemplateResponse("index.html", {"request": request, "latest_messages": current_data})

# --- NEUE ROUTE FÜR EINSTELLUNGEN ---


# --- NEUE ROUTE FÜR EINSTELLUNGEN (GET) ---
@app.get("/settings", response_class=HTMLResponse)
async def get_settings_page(request: Request):
    mqtt_client.publish_message("settings", "REQUEST_SETTINGS")
    print("MQTT-Nachricht an Topic 'settings' gesendet, um ESP32-Einstellungen anzufordern.")
    with mqtt_client.latest_messages_lock:
        current_settings_data = mqtt_client.latest_messages.copy()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings_data": current_settings_data
    })
# --- ENDE NEUE ROUTE ---

# --- NEUE ROUTE ZUM ÄNDERN VON EINSTELLUNGEN (POST via HTMX) ---


@app.post("/htmx/change_esp_setting", response_class=HTMLResponse)
async def htmx_change_esp_setting_route(
    request: Request,  # Request-Objekt kann nützlich sein, ist hier aber nicht zwingend
    parameter_name: str = Form(...),
    new_value: str = Form(...)
):
    topic = "changeSetting"
    # Das Format ist: ["parameterName"],["newValue"]
    # Wichtig: Die Anführungszeichen im JSON-String müssen korrekt escaped werden,
    # oder man verwendet f-strings sorgfältig.
    # Die Werte selbst (parameter_name, new_value) sollten als Strings im JSON sein.
    message = f'["{parameter_name}"],["{new_value}"]'

    print(f"FastAPI: Empfangene Einstellungsänderung via HTMX:")
    print(f"  Parameter: {parameter_name}")
    print(f"  Neuer Wert: {new_value}")
    print(f"  Sende an MQTT Topic='{topic}', Message='{message}'")

    success = mqtt_client.publish_message(topic, message)
    print(f"FastAPI: MQTT Nachricht gesendet: {success}")

    if success is None:
        # Die Rückgabe ist ein HTML-Fragment, das von HTMX in das target geladen wird
        return HTMLResponse(f"<span class='status-message success'>Gesendet!</span>")
    else:
        return HTMLResponse(f"<span class='status-message error'>Fehler beim Senden!</span>")
# --- ENDE NEUE ROUTE ---


# SSE Endpoint für Live-Updates
@app.get("/sse")
async def sse_stream(request: Request):
    async def event_generator():
        # ... (Ihr bestehender SSE Code)
        # Stellen Sie sicher, dass dieser Teil unverändert bleibt oder korrekt funktioniert
        queue = asyncio.Queue()
        last_update_type = None
        last_update_topic = None
        last_update_payload = None

        while True:
            try:
                update = await asyncio.wait_for(mqtt_client.update_queue.get(), timeout=300)
                update_type = update.get("type")
                update_topic = update.get("topic")
                update_payload = update.get("payload")

                if (update_type == last_update_type and
                    update_topic == last_update_topic and
                        update_payload == last_update_payload):
                    mqtt_client.update_queue.task_done()
                    continue

                last_update_type = update_type
                last_update_topic = update_topic
                last_update_payload = update_payload

                if update_type == "update":
                    if update_topic == "send_settings" and update_payload == "updated":
                        print(
                            "SSE: 'send_settings' Update erkannt. Rendere Einstellungs-Komponente.")
                        with mqtt_client.latest_messages_lock:
                            current_settings_data = mqtt_client.latest_messages.copy()

                        # Wichtig: Das 'request'-Objekt für das Template-Rendering
                        # muss hier korrekt übergeben werden. Da wir es im sse_stream
                        # haben, können wir es verwenden.
                        html_content = templates.get_template("components/settings_display.html").render({
                            "request": request,
                            "settings_data": current_settings_data
                        })
                        yield {"event": "settings_update", "data": html_content}
                    # ... (ggf. andere SSE-Events)

                mqtt_client.update_queue.task_done()

            except asyncio.TimeoutError:
                yield {"event": "keep-alive", "data": ""}
            except asyncio.CancelledError:
                print("SSE-Verbindung vom Client geschlossen.")
                break
            except Exception as e:
                print(f"Fehler im SSE event_generator: {e}")
                await asyncio.sleep(1)

    return EventSourceResponse(event_generator())
# Fügen Sie hier ggf. weitere Routen oder statische Datei-Mounts hinzu
# from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="app/static"), name="static")
