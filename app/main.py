import asyncio
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import html  # Importiere das html Modul für escaping
import json
from sse_starlette.sse import EventSourceResponse

from app import mqtt_client  # Ihre mqtt_client.py Datei

app = FastAPI()

# Shared variable to hold the latest message HTML fragment for the index page
latest_message_html_fragment = ""

# Stellen Sie sicher, dass der Pfad zu Ihren Templates korrekt ist
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# MQTT Client starten


@app.on_event("startup")
async def startup_event():
    try:
        mqtt_client.app_event_loop = asyncio.get_running_loop()
        print("Starting MQTT client...")
        mqtt_client.start_mqtt_client()
        print("MQTT Client gestartet")
        
        # Warte kurz, damit die Verbindung hergestellt werden kann
        await asyncio.sleep(1)
        
        # Prüfe, ob die Verbindung erfolgreich war
        if mqtt_client.client.is_connected():
            print("MQTT Client erfolgreich verbunden")
        else:
            print("MQTT Client ist nicht verbunden!")
            raise Exception("MQTT Client konnte nicht verbunden werden")
    except Exception as e:
        print(f"Fehler beim Starten des MQTT Clients: {e}")
        raise


@app.on_event("shutdown")
def shutdown_event():
    mqtt_client.stop_mqtt_client()
    print("FastAPI App heruntergefahren und MQTT Client gestoppt.")

# Index-Seite


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "mqtt_client": mqtt_client})

# --- SETTINGS SEITE (GET) ---


@app.get("/settings", response_class=HTMLResponse)
async def get_settings_page(request: Request):
    # Fordert die aktuellen Einstellungen vom ESP32 an
    mqtt_client.publish_message("settings", "REQUEST_SETTINGS")
    print("MQTT-Nachricht an Topic 'settings' gesendet, um ESP32-Einstellungen anzufordern.")
    # Zeigt die zuletzt bekannten Einstellungen an, bis ein Update via SSE kommt.
    # Annahme: Settings sind Teil von latest_messages, und der ESP sendet sie nach REQUEST_SETTINGS.

    with mqtt_client.latest_messages_lock:
        # Annahme: Settings sind Teil von latest_messages
        current_settings_data = mqtt_client.latest_messages.copy()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings_data": current_settings_data
    })

# --- EINSTELLUNGEN ÄNDERN (POST via HTMX) ---


@app.post("/htmx/change_esp_setting", response_class=HTMLResponse)
async def htmx_change_esp_setting_route(
    request: Request,
    parameter_name: str = Form(...),
    new_value: str = Form(...)
):
    topic = "changeSetting"
    message = f'["{parameter_name}"],["{new_value}"]'

    print(f"FastAPI: Empfangene Einstellungsänderung via HTMX:")
    print(f"  Parameter: {parameter_name}")
    print(f"  Neuer Wert: {new_value}")
    print(f"  Sende an MQTT Topic='{topic}', Message='{message}'")

    success = mqtt_client.publish_message(topic, message)
    print(f"FastAPI: MQTT Nachricht gesendet: {success}")

    # Die Rückgabe ist ein HTML-Fragment, das von HTMX in das target geladen wird
    # Normalerweise würde man hier das aktualisierte Fragment zurückgeben oder eine Erfolgs-/Fehlermeldung.
    # Der ESP32 sendet die neuen Settings dann via MQTT, was per SSE die Anzeige aktualisiert.
    # Daher reicht hier eine einfache Bestätigung.
    if success is None or success is True:  # Annahme: publish_message gibt None oder True bei Erfolg zurück
        return HTMLResponse(f"<span class='status-message success'>Änderung gesendet! Warte auf Bestätigung...</span>")
    else:
        return HTMLResponse(f"<span class='status-message error'>Fehler beim Senden der Änderung!</span>")

# --- MQTT DASHBOARD SEITE (GET) ---


@app.get("/mqtt-dashboard", response_class=HTMLResponse)
async def mqtt_dashboard_page(request: Request):
    """Renders the MQTT dashboard page."""
    with mqtt_client.latest_messages_lock:
        initial_mqtt_data = mqtt_client.latest_messages.copy()
    return templates.TemplateResponse(
        "mqtt_dashboard.html",
        {"request": request, "initial_mqtt_data": initial_mqtt_data}
    )

# --- SSE GENERATOR FÜR MQTT DASHBOARD ---


async def mqtt_dashboard_event_generator(request: Request):
    """
    Generiert SSE-Events für MQTT-Datenupdates für das Dashboard.
    Liest von mqtt_client.update_queue.
    """
    # Variablen zur Vermeidung doppelter Verarbeitung durch diesen spezifischen Generator
    global latest_message_html_fragment  # Access the shared variable
    last_processed_update_content_for_dashboard = None

    while True:
        if await request.is_disconnected():
            print("Client disconnected from MQTT Dashboard SSE")
            break

        # Um sicherzustellen, dass task_done nur aufgerufen wird, wenn ein Element geholt wurde
        raw_update = None
        try:
            # Timeout für Keep-Alive
            raw_update = await asyncio.wait_for(mqtt_client.update_queue.get(), timeout=30)

            update_type = raw_update.get("type")
            update_topic = raw_update.get("topic")
            update_payload = raw_update.get("payload")

            # Erstelle einen eindeutigen Bezeichner für den Inhalt des Updates
            current_update_content = (
                update_type, update_topic, update_payload)

            # Verarbeite nur, wenn sich der Inhalt seit dem letzten Mal geändert hat (für diesen Generator)
            if current_update_content == last_processed_update_content_for_dashboard:
                mqtt_client.update_queue.task_done()
                # Zurücksetzen, da verarbeitet (oder eher ignoriert als Duplikat)
                raw_update = None
                continue

            last_processed_update_content_for_dashboard = current_update_content

            # Das Dashboard soll alle Änderungen in `latest_messages` widerspiegeln.
            # Wir rendern die Tabelle neu, wenn ein beliebiges Update aus der Queue kommt,
            # das nicht explizit nur für die Settings-Seite gedacht ist (obwohl die Konkurrenz um die Queue bleibt).
            # Eine einfachere Annahme: Jedes Update in der Queue könnte für das Dashboard relevant sein.

            print(f"MQTT Dashboard SSE: Update aus Queue: {raw_update}")
            with mqtt_client.latest_messages_lock:
                current_mqtt_data = mqtt_client.latest_messages.copy()

            html_fragment = templates.get_template("components/mqtt_table.html").render({
                "request": request,
                "mqtt_data": current_mqtt_data
            })
            yield {"event": "message", "data": html_fragment}

            mqtt_client.update_queue.task_done()
            raw_update = None  # Zurücksetzen nach erfolgreicher Verarbeitung

        except asyncio.TimeoutError:
            yield {"event": "keep-alive", "data": "mqtt_dashboard_keep_alive"}
        except asyncio.CancelledError:
            print("MQTT Dashboard SSE connection closed by client.")
            break  # Wichtig, um die Schleife zu beenden
        except Exception as e:
            print(f"Error in MQTT Dashboard SSE event_generator: {e}")
            await asyncio.sleep(1)  # Kurze Pause vor dem nächsten Versuch
        finally:
            # Stellen Sie sicher, dass task_done aufgerufen wird, wenn ein Element erfolgreich
            # aus der Queue geholt wurde, aber während der Verarbeitung ein Fehler auftrat.
            if raw_update is not None:  # pragma: no cover
                # Dies wird erreicht, wenn get() erfolgreich war, aber später ein Fehler auftrat
                # bevor task_done() im try-Block erreicht wurde.
                try:
                    mqtt_client.update_queue.task_done()
                except ValueError:  # Falls task_done schon gerufen wurde oder nicht nötig
                    pass


# --- SSE ENDPOINT FÜR MQTT DASHBOARD ---
@app.get("/events/mqtt-updates")
async def mqtt_dashboard_sse_endpoint(request: Request):
    """SSE endpoint for MQTT data updates for the dashboard."""
    return EventSourceResponse(mqtt_dashboard_event_generator(request))

# --- SSE ENDPOINT FÜR SETTINGS LIVE-UPDATES ---


@app.get("/sse")
async def settings_sse_stream(request: Request):
    """
    Generiert SSE-Events spezifisch für Einstellungs-Updates.
    Liest von mqtt_client.update_queue.
    """
    # Variablen zur Vermeidung doppelter Verarbeitung durch diesen spezifischen Generator
    last_update_type_settings = None
    last_update_topic_settings = None
    last_update_payload_settings = None

    async def event_generator():
        nonlocal last_update_type_settings, last_update_topic_settings, last_update_payload_settings
        while True:
            if await request.is_disconnected():
                print("Client disconnected from Settings SSE")
                break

            raw_update = None
            try:
                # Timeout für Keep-Alive
                raw_update = await asyncio.wait_for(mqtt_client.update_queue.get(), timeout=30)

                update_type = raw_update.get("type")
                update_topic = raw_update.get("topic")
                update_payload = raw_update.get("payload")

                # Verhindere doppelte Verarbeitung derselben Nachricht durch diesen Handler
                if (update_type == last_update_type_settings and
                    update_topic == last_update_topic_settings and
                        update_payload == last_update_payload_settings):
                    mqtt_client.update_queue.task_done()
                    raw_update = None
                    continue

                last_update_type_settings = update_type
                last_update_topic_settings = update_topic
                last_update_payload_settings = update_payload

                # Dieser Handler ist spezifisch für Settings-Updates
                if update_type == "update" and update_topic == "send_settings" and update_payload == "updated":
                    print(
                        "Settings SSE: 'send_settings' Update erkannt. Rendere Einstellungs-Komponente.")
                    with mqtt_client.latest_messages_lock:
                        current_settings_data = mqtt_client.latest_messages.copy()

                    html_content = templates.get_template("components/settings_display.html").render({
                        "request": request,
                        "settings_data": current_settings_data
                    })
                    yield {"event": "settings_update", "data": html_content}

                mqtt_client.update_queue.task_done()
                raw_update = None

            except asyncio.TimeoutError:
                yield {"event": "keep-alive", "data": "settings_keep_alive"}
            except asyncio.CancelledError:
                print("Settings SSE connection closed by client.")
                break
            except Exception as e:
                print(f"Fehler im Settings SSE event_generator: {e}")
                await asyncio.sleep(1)
            finally:
                if raw_update is not None:  # pragma: no cover
                    try:
                        mqtt_client.update_queue.task_done()
                    except ValueError:
                        pass
    # Die event_generator Funktion ist hier definiert, aber die Route gibt sie zurück
    return EventSourceResponse(event_generator())

# --- New SSE Generator for Index Page (New Messages) ---


async def new_message_event_generator(request: Request):
    """
    Generates SSE-Events for new MQTT messages for the index page.
    Liest von mqtt_client.update_queue.
    """
    # Variables to prevent processing the same update multiple times by this specific generator
    last_processed_update_content_for_index = None

    while True:
        if await request.is_disconnected():
            print("Client disconnected from New Messages SSE")
            break

        raw_update = None
        try:
            # Timeout for Keep-Alive
            print("New Messages SSE: Waiting for item from queue...")
            raw_update = await asyncio.wait_for(mqtt_client.update_queue.get(), timeout=30)
            print(f"New Messages SSE: Received item from queue: {raw_update}")

            update_type = raw_update.get("type")
            update_topic = raw_update.get("topic")
            update_payload = raw_update.get("payload")
            current_update_content = (update_type, update_topic, update_payload)
            
            if last_processed_update_content_for_index == current_update_content:
                mqtt_client.update_queue.task_done()
                continue
                
            last_processed_update_content_for_index = current_update_content
            
            if update_type == "update":
                escaped_topic = html.escape(str(update_topic))
                escaped_payload = html.escape(str(update_payload))
                
                # Erstelle die Liste-Elemente
                list_items = []
                for topic, payload in mqtt_client.latest_messages.items():
                    escaped_topic = html.escape(str(topic))
                    escaped_payload = html.escape(str(payload))
                    list_items.append(f"<li><strong>{escaped_topic}:</strong> {escaped_payload}</li>")
                
                # Sende die aktualisierten Nachrichten als JSON
                with mqtt_client.latest_messages_lock:
                    current_messages = mqtt_client.latest_messages.copy()
                # Sortiere die Nachrichten nach Topic für konsistente Anzeige
                sorted_messages = dict(sorted(current_messages.items()))
                # Send JSON data
                yield {"data": json.dumps(sorted_messages)}
            else:
                print(f"New Messages SSE: Skipping update with type '{update_type}'")
                continue
                
            mqtt_client.update_queue.task_done()
            raw_update = None
            
        except asyncio.TimeoutError:
            yield {"event": "keep-alive", "data": "new_messages_keep_alive"}
        except asyncio.CancelledError:
            print("New Messages SSE connection closed by client.")
            break
        except Exception as e:
            print(f"Error in New Messages SSE event_generator: {e}")
            await asyncio.sleep(1)
        finally:
            if raw_update is not None:
                try:
                    mqtt_client.update_queue.task_done()
                except ValueError:
                    pass
# --- New Endpoint to return the latest message HTML fragment ---


@app.get("/htmx/get-latest-message-html", response_class=HTMLResponse)
async def get_latest_message_html():
    """Returns the HTML fragment for the single latest message."""
    # Return the stored HTML fragment
    return HTMLResponse(content=latest_message_html_fragment)

# --- New SSE Endpoint for Index Page ---


@app.get("/events/new-messages")
async def new_messages_sse_endpoint(request: Request):
    """SSE endpoint for new MQTT message updates for the index page."""
    return EventSourceResponse(new_message_event_generator(request))

# Statische Dateien (CSS, JS, Bilder etc.)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Neue Route für das aktuelle MQTT-HTML-Fragment
@app.get("/htmx/get-latest-message-html", response_class=HTMLResponse)
async def get_latest_message_html():
    """Gibt das aktuelle MQTT-HTML-Fragment zurück."""
    return HTMLResponse(content=latest_message_html_fragment)
