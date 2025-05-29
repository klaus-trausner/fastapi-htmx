from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import asyncio
import traceback
import json
from app import mqtt_client

router = APIRouter()

templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse, name="read_root")
async def read_root(request: Request):
    return templates.TemplateResponse(
        request=request, name="index.html", context={"welcome_message": "Herzlich Willkommen zu FastAPI & HTMX!"}
    )


@router.get("/htmx-message", response_class=HTMLResponse)
async def get_htmx_message():
    return "<p>Diese Nachricht wurde dynamisch mit HTMX geladen!</p>"


def generate_mqtt_html(mqtt_data):
    """
    Generiert HTML für MQTT-Daten
    """
    if not mqtt_data:
        return '<p>Warte auf MQTT Daten oder noch keine Daten empfangen...</p>'

    html_parts = [
        '<table class="table" style="width:100%; border-collapse: collapse; margin-top: 15px;">',
        '<thead>',
        '<tr>',
        '<th style="border: 1px solid #ddd; padding: 8px; text-align: left; background-color: #f2f2f2;">Topic</th>',
        '<th style="border: 1px solid #ddd; padding: 8px; text-align: left; background-color: #f2f2f2;">Wert</th>',
        '</tr>',
        '</thead>',
        '<tbody>'
    ]

    # Sortiere die Topics für konsistente Anzeige
    for topic, value in sorted(mqtt_data.items()):
        # Escape HTML-spezielle Zeichen
        topic_escaped = str(topic).replace('&', '&amp;').replace(
            '<', '&lt;').replace('>', '&gt;')
        value_escaped = str(value).replace('&', '&amp;').replace(
            '<', '&lt;').replace('>', '&gt;')

        html_parts.extend([
            '<tr>',
            f'<td style="border: 1px solid #ddd; padding: 8px;">{topic_escaped}</td>',
            f'<td style="border: 1px solid #ddd; padding: 8px;">{value_escaped}</td>',
            '</tr>'
        ])

    html_parts.extend([
        '</tbody>',
        '</table>'
    ])

    return ''.join(html_parts)


async def mqtt_event_generator(request: Request):
    """
    SSE Event Generator für MQTT Updates
    """
    print("SSE_GENERATOR: Starting event generator.")
    client_disconnected = False

    try:
        while not client_disconnected:
            try:
                # print(
                #    f"SSE_GENERATOR: Warte auf Item aus Queue. Queue size: {mqtt_client.update_queue.qsize()}")
                update_item = await asyncio.wait_for(mqtt_client.update_queue.get(), timeout=25.0)
                # print(f"SSE_GENERATOR: Item aus Queue erhalten: {update_item}")

                if update_item:
                    # Sichere Kopie der aktuellen MQTT-Daten
                    with mqtt_client.latest_messages_lock:
                        current_data = mqtt_client.latest_messages.copy()

                    # print(f"SSE_GENERATOR: Current MQTT data: {current_data}")

                    if not current_data:
                        print("SSE_GENERATOR: WARNUNG! Keine MQTT-Daten verfügbar.")
                        html_fragment = '<p>Keine MQTT-Daten verfügbar</p>'
                    else:
                        html_fragment = generate_mqtt_html(current_data)

                    # print(
                    #    f"SSE_GENERATOR: HTML Fragment Länge: {len(html_fragment)}")
                    # print(
                    #    f"SSE_GENERATOR: HTML Preview: {html_fragment[:200]}...")

                    # WICHTIG: SSE-Format muss exakt stimmen
                    # Keine Leerzeichen nach dem Doppelpunkt, \n\n am Ende
                    sse_data = f"event: message\ndata: {html_fragment}\n\n"

                    # print(
                    #   f"SSE_GENERATOR: Sende SSE Event (erste 100 Zeichen): {sse_data[:100]}...")
                    yield sse_data

                    mqtt_client.update_queue.task_done()

            except asyncio.TimeoutError:
                print("SSE_GENERATOR: Timeout, sende Keep-Alive")
                yield ": keep-alive\n\n"

            except asyncio.CancelledError:
                print("SSE_GENERATOR: Task cancelled")
                client_disconnected = True
                raise

            except Exception as e:
                print(f"SSE_GENERATOR: Fehler in Event-Schleife: {e}")
                traceback.print_exc()
                client_disconnected = True
                break

    except asyncio.CancelledError:
        print("SSE_GENERATOR: Event generator cancelled")
    except Exception as e:
        print(f"SSE_GENERATOR: Unerwarteter Fehler: {e}")
        traceback.print_exc()
    finally:
        print("SSE_GENERATOR: Generator beendet")


@router.get("/events/mqtt-updates")
async def stream_mqtt_updates(request: Request):
    """
    SSE Endpoint für MQTT Updates
    """
    response = StreamingResponse(
        mqtt_event_generator(request),
        media_type="text/event-stream"
    )

    # Kritische SSE Headers
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Cache-Control"
    # Nginx buffering deaktivieren
    response.headers["X-Accel-Buffering"] = "no"

    return response


@router.get("/mqtt-dashboard", response_class=HTMLResponse, name="mqtt_dashboard")
async def mqtt_dashboard_page(request: Request):
    """
    MQTT Dashboard Seite
    """
    with mqtt_client.latest_messages_lock:
        initial_data_for_page = mqtt_client.latest_messages.copy()

    print(f"MQTT_DASHBOARD_PAGE: Initial data: {initial_data_for_page}")

    return templates.TemplateResponse(
        request=request,
        name="mqtt_dashboard.html",
        context={"initial_mqtt_data": initial_data_for_page}
    )


# Debug-Endpoint zum Testen der HTML-Generierung
@router.get("/debug/mqtt-html", response_class=HTMLResponse)
async def debug_mqtt_html():
    """Debug-Endpoint um die HTML-Generierung zu testen"""
    with mqtt_client.latest_messages_lock:
        current_data = mqtt_client.latest_messages.copy()

    html = generate_mqtt_html(current_data)
    return HTMLResponse(content=html)
