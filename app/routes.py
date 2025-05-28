from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import asyncio
import traceback  # Import für detailliertere Fehlerausgaben
from app import mqtt_client  # Zugriff auf Queue und latest_messages

router = APIRouter()

templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse, name="read_root")
async def read_root(request: Request):
    return templates.TemplateResponse(
        request=request, name="index.html", context={"welcome_message": "Herzlich Willkommen zu FastAPI & HTMX!"}
    )


@router.get("/htmx-message", response_class=HTMLResponse)
async def get_htmx_message():
    # Dieser Endpunkt wird von HTMX aufgerufen
    # Du kannst hier komplexere Logik oder Datenbankabfragen einbauen
    return "<p>Diese Nachricht wurde dynamisch mit HTMX geladen!</p>"


async def mqtt_event_generator(request: Request):
    """
    Sendet die initialen MQTT-Daten und lauscht dann auf Updates aus der Queue.
    Sendet bei jedem Update das komplette Set an MQTT-Daten neu.
    """
    print("SSE_GENERATOR: Starting event generator.")
    client_disconnected = False
    try:
        print("SSE_GENERATOR: Waiting for first MQTT update to send via SSE.")

        while not client_disconnected:  # Schleife läuft, bis Client die Verbindung trennt oder ein Fehler auftritt
            try:
                # Warte auf ein Update-Signal aus der MQTT-Client-Queue
                # Ein Timeout hilft, die Verbindung offen zu halten (Keep-Alive)
                print(
                    f"SSE_GENERATOR: Warte auf Item aus Queue. Aktuelle Queue size: {mqtt_client.update_queue.qsize()}")
                update_item = await asyncio.wait_for(mqtt_client.update_queue.get(), timeout=25.0)
                print(f"SSE_GENERATOR: Item aus Queue erhalten: {update_item}")

                if update_item:  # Wenn ein Item (kein Timeout) empfangen wurde
                    with mqtt_client.latest_messages_lock:
                        current_data = mqtt_client.latest_messages.copy()
                    # Log des empfangenen Items
                    print(
                        f"SSE_GENERATOR: Processing update_item from queue: {update_item}")
                    if not current_data:
                        print(
                            "SSE_GENERATOR: CRITICAL WARNING! current_data is EMPTY before rendering. latest_messages was empty at copy time.")
                        print(
                            f"SSE_GENERATOR: This occurred for update_item: {update_item}")
                    print(
                        f"SSE_GENERATOR: Verarbeite Update. latest_messages vor Render: {current_data}")
                    html_fragment = templates.get_template("partials/mqtt_data_display.html").render(
                        request=request, mqtt_data=current_data
                    )

                    fragment_length = len(html_fragment)
                    fragment_preview = html_fragment.strip()[:500]
                    print(
                        f"SSE_GENERATOR: Gerendertes HTML Fragment (Länge: {fragment_length}): '{fragment_preview}...'")

                    if not html_fragment.strip():
                        print(
                            "SSE_GENERATOR: WARNUNG! Gerendertes HTML Fragment ist leer oder nur Whitespace!")

                    yield f"data: {html_fragment}\n\n"
                    mqtt_client.update_queue.task_done()

            except asyncio.TimeoutError:
                print("SSE_GENERATOR: Timeout in Queue.get(), sende SSE Keep-Alive.")
                yield ": sse keep-alive\n\n"
            except asyncio.CancelledError:
                # Dieser Fehler tritt auf, wenn der Client die Verbindung schließt
                # oder die Aufgabe von außen abgebrochen wird.
                print(
                    "SSE_GENERATOR: Task cancelled (likely client disconnected or server shutdown).")
                client_disconnected = True  # Signalisiert das Verlassen der äußeren Schleife
                raise  # Erneut auslösen, damit der äußere try/except es fangen kann
            except Exception as e_inner:
                print(
                    f"SSE_GENERATOR: Fehler während der Event-Verarbeitung in der Schleife: {e_inner}")
                traceback.print_exc()
                print(
                    "SSE_GENERATOR: Breche Event-Schleife aufgrund eines internen Fehlers ab.")
                client_disconnected = True  # Signalisiert das Verlassen der äußeren Schleife
                break

    except asyncio.CancelledError:
        # Wird gefangen, wenn die Aufgabe explizit abgebrochen wird (z.B. Client disconnect)
        print("SSE_GENERATOR: Event generator task was explicitly cancelled.")
    except Exception as e_outer:
        # Fängt andere unerwartete Fehler im Generator
        print(
            f"SSE_GENERATOR: Unerwarteter Fehler im äußeren Bereich des Event-Generators: {e_outer}")
        traceback.print_exc()
    finally:
        # Wird immer ausgeführt, egal ob ein Fehler auftrat oder nicht
        print("SSE_GENERATOR: Exiting event generator function.")


@router.get("/events/mqtt-updates", response_class=StreamingResponse)
async def stream_mqtt_updates(request: Request):
    return StreamingResponse(mqtt_event_generator(request), media_type="text/event-stream")


@router.get("/mqtt-dashboard", response_class=HTMLResponse, name="mqtt_dashboard")
async def mqtt_dashboard_page(request: Request):
    # Übergib die aktuell bekannten MQTT-Daten für die initiale Anzeige
    initial_data_for_page = mqtt_client.latest_messages.copy()
    print(
        f"MQTT_DASHBOARD_PAGE: Rendering initial page. initial_mqtt_data is: {initial_data_for_page}")
    return templates.TemplateResponse(
        request=request, name="mqtt_dashboard.html", context={"initial_mqtt_data": initial_data_for_page}
    )
