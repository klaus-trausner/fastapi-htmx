import paho.mqtt.client as mqtt
import threading
import threading
import time
import json  # Hinzugefügt für JSON Parsing
import asyncio  # Hinzugefügt

# MQTT Broker Konfiguration
MQTT_BROKER_HOST = "81.7.10.99"
MQTT_BROKER_PORT = 1883
MQTT_USERNAME = "klaus"
# ACHTUNG: Passwort sollte idealerweise nicht hartcodiert sein!
MQTT_PASSWORD = "DHisddS!"

MQTT_TOPICS = [
    "esp32/zisterne",
    "esp32/temperature",
    "esp32/pressure",
    "esp32/humidity",
    "bodenfeuchte",
    "steuerungstemperatur",
    "status",
    "send_settings",
    "ext1/temperature",
    "ext1/humidity",
    "ext2/temperature",
    "ext2/humidity",
    "wind_dir",
    "innen",
    "test"
]

# Globaler Speicher für die letzten Nachrichten und asyncio Queue
latest_messages = {}
latest_messages_lock = threading.Lock()  # Lock für latest_messages
update_queue = asyncio.Queue()
app_event_loop = None  # Wird von main.py gesetzt


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Erfolgreich mit MQTT Broker verbunden.")
        # Abonnieren der Topics nach erfolgreicher Verbindung
        for topic in MQTT_TOPICS:
            client.subscribe(topic)  # QoS 0 per default
            print(f"Abonniert: {topic}")

    else:
        print(f"Verbindung zum MQTT Broker fehlgeschlagen mit Code: {rc}")

#


def on_message(client, userdata, msg):
    payload_str = msg.payload.decode()
    original_topic = msg.topic  # Behalte den ursprünglichen Topic für das Update-Signal
    print(
        f"MQTT_CLIENT: Nachricht empfangen auf Topic '{original_topic}': {payload_str}")

    sse_topic_to_signal = original_topic
    sse_payload_to_signal = payload_str

    with latest_messages_lock:
        if original_topic == "innen":
            try:
                parts = payload_str.split('-')
                if len(parts) == 3:
                    temp, humidity, pressure = parts
                    latest_messages["indoor/temperature"] = temp.strip()
                    latest_messages["indoor/humidity"] = humidity.strip()
                    latest_messages["indoor/pressure"] = pressure.strip()
                    # Entferne den ursprünglichen "innen" Topic, da er aufgeteilt wurde
                    if "innen" in latest_messages:
                        del latest_messages["innen"]
                    print(
                        f"MQTT_CLIENT: Topic 'innen' Daten '{payload_str}' verarbeitet.")
                    # SSE signal will use original_topic="innen"; handler can check for sub-keys
                else:
                    print(
                        f"MQTT_CLIENT: WARNUNG - 'innen' Topic Payload Formatfehler. Speichere original.")
                    # Speichere die Originalnachricht bei Formatfehler
                    latest_messages[original_topic] = payload_str
            except Exception as e:
                print(
                    f"MQTT_CLIENT: FEHLER - Beim Parsen des 'innen' Topic Payloads '{payload_str}': {e}. Speichere original.")
                # Speichere die Originalnachricht bei Exception
                latest_messages[original_topic] = payload_str
        elif original_topic == "send_settings":
            # Rohen Payload für Anzeige speichern
            latest_messages["send_settings_payload"] = payload_str
            try:
                settings_data = json.loads(payload_str)
                if isinstance(settings_data, dict):
                    for key, value in settings_data.items():
                        latest_messages[f"setting_{key}"] = value
                    print(
                        f"MQTT_CLIENT: Topic 'send_settings' Daten verarbeitet und als 'setting_KEY' gespeichert.")
                    sse_topic_to_signal = "send_settings"  # Spezifisches Signal für SSE Handler
                    # Anzeigen, dass Daten geparst und bereit sind
                    sse_payload_to_signal = "updated"
                else:
                    print(
                        f"MQTT_CLIENT: WARNUNG - 'send_settings' Payload ist kein JSON-Objekt. Speichere original unter '{original_topic}'.")
                    latest_messages[original_topic] = payload_str  # Fallback
            except json.JSONDecodeError as e:
                print(
                    f"MQTT_CLIENT: FEHLER - Beim Parsen des 'send_settings' JSON Payloads: {e}. Speichere original unter '{original_topic}'.")
                latest_messages[original_topic] = payload_str
            except Exception as e:
                print(
                    f"MQTT_CLIENT: FEHLER - Beim Verarbeiten des 'send_settings' Payloads: {e}. Speichere original unter '{original_topic}'.")
                latest_messages[original_topic] = payload_str
        else:
            # Standardbehandlung für alle anderen Topics
            latest_messages[original_topic] = payload_str

    # Informiere den SSE-Handler über die neue Nachricht
    if app_event_loop and update_queue:
        asyncio.run_coroutine_threadsafe(update_queue.put(
            {"type": "update", "topic": sse_topic_to_signal, "payload": sse_payload_to_signal}), app_event_loop)


def on_disconnect(client, userdata, rc):
    print(f"Verbindung zum MQTT Broker getrennt mit Code: {rc}")
    if rc != 0:
        print("Versuche erneute Verbindung...")
        # Einfacher Wiederverbindungsversuch (kann verbessert werden, z.B. mit Backoff)
        # time.sleep(5)
        # client.reconnect() # Vorsicht mit automatischem Reconnect in Schleife ohne Backoff


def publish_message(topic, payload, qos=0, retain=False):
    if client.is_connected():
        result = client.publish(topic, payload, qos, retain)
        result.wait_for_publish()  # Warten bis die Nachricht gesendet wurde (optional)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            print(
                f"Nachricht '{payload}' erfolgreich an Topic '{topic}' gesendet.")
        else:
            print(
                f"Fehler beim Senden der Nachricht an Topic '{topic}': {mqtt.error_string(result.rc)}")
    else:
        print("MQTT Client ist nicht verbunden. Nachricht konnte nicht gesendet werden.")


def start_mqtt_client():
    # app_event_loop wird in main.py gesetzt, bevor diese Funktion aufgerufen wird.
    if not app_event_loop:
        print("WARNUNG: asyncio event loop wurde nicht im mqtt_client gesetzt. SSE Updates funktionieren möglicherweise nicht.")

    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    try:
        client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
        # Starte den MQTT Client Loop in einem separaten Thread,
        # damit er nicht die Hauptanwendung (FastAPI) blockiert.
        client.loop_start()  # Startet einen Thread für den Netzwerk-Loop
    except Exception as e:
        print(f"Fehler beim Verbinden oder Starten des MQTT Clients: {e}")


def stop_mqtt_client():
    print("MQTT Client wird gestoppt.")
    client.loop_stop()  # Stoppt den Netzwerk-Loop-Thread
    client.disconnect()
