import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS


load_dotenv()
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR / "frontend"
DOWNLOADS_DIR = BASE_DIR / "downloads"

app = Flask(
    __name__,
    static_folder=str(FRONTEND_DIR),
    static_url_path="",
)
CORS(app, resources={r"/api/*": {"origins": "*"}})


def leer_dataset(nombre):
    """Lee uno de los JSON generados por main.py."""
    ruta = DATA_DIR / nombre
    try:
        contenido = json.loads(ruta.read_text(encoding="utf-8"))
        if not isinstance(contenido, dict) or not isinstance(
            contenido.get("data"), list
        ):
            raise ValueError("El JSON no tiene el formato esperado")
        return contenido
    except FileNotFoundError:
        logger.warning("No existe el dataset %s", ruta)
        return {"generated_at": None, "count": 0, "data": []}
    except (OSError, ValueError, json.JSONDecodeError) as error:
        logger.exception("No se pudo leer %s: %s", ruta, error)
        raise


def respuesta_dataset(nombre, transformar=None):
    try:
        contenido = leer_dataset(nombre)
        datos = contenido["data"]
        if transformar:
            datos = [transformar(item) for item in datos]
        return (
            jsonify(
                {
                    "status": "success",
                    "generated_at": contenido.get("generated_at"),
                    "count": len(datos),
                    "data": datos,
                }
            ),
            200,
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"No se pudo leer el dataset {nombre}",
                }
            ),
            500,
        )


def _archivo_con_url(item):
    contenido = dict(item)
    nombre = contenido.get("nombre_archivo")
    contenido["download_url"] = (
        f"/downloads/{quote(nombre)}" if nombre else None
    )
    return contenido


@app.get("/")
def dashboard():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/favicon.ico")
def favicon():
    return "", 204


@app.get("/api/results")
def obtener_resultados():
    return respuesta_dataset("results.json")


@app.get("/api/files")
def obtener_archivos():
    return respuesta_dataset("files.json", transformar=_archivo_con_url)


@app.get("/api/events")
def obtener_eventos():
    return respuesta_dataset("events.json")


@app.get("/api/status")
def verificar_servidor():
    datasets = {
        nombre: (DATA_DIR / nombre).is_file()
        for nombre in ("results.json", "files.json", "events.json")
    }
    return (
        jsonify(
            {
                "status": "online",
                "project": (
                    "Plataforma de Scraping, Visualizacion y Selectores LLM"
                ),
                "cycle": "IIC-2026",
                "datasets": datasets,
            }
        ),
        200,
    )


@app.get("/downloads/<path:nombre>")
def descargar_archivo(nombre):
    return send_from_directory(DOWNLOADS_DIR, nombre, as_attachment=False)


@app.errorhandler(404)
def no_encontrado(_error):
    return jsonify({"status": "error", "message": "Recurso no encontrado"}), 404


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
