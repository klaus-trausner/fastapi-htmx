from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app import routes
from app import mqtt_client  # Importiere das neue MQTT Modul
import asyncio

app = FastAPI()

# Startup und Shutdown Events für den MQTT Client


@app.on_event("startup")
async def startup_event():
    print("FastAPI Anwendung startet...")
    # Übergib den aktuellen Event-Loop an das MQTT-Client-Modul
    mqtt_client.app_event_loop = asyncio.get_running_loop()
    mqtt_client.start_mqtt_client()


@app.on_event("shutdown")
async def shutdown_event():
    print("FastAPI Anwendung wird beendet...")
    mqtt_client.stop_mqtt_client()

# Binde die statischen Dateien ein (CSS, JS, Bilder etc.)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Binde die Routen aus routes.py ein
app.include_router(routes.router)
if __name__ == "__main__":
    import uvicorn
    # Starte den Uvicorn-Server. reload=True ist nützlich für die Entwicklung.
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
