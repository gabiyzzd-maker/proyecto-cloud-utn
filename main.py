import json
import logging
import os
import re
import tempfile
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

from scraper.scraper_dynamic import ejecutar_scraping_dinamico
from scraper.scraper_static import (
    descargar_y_verificar_archivo,
    ejecutar_scraping_estatico,
)


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DOWNLOADS_DIR = BASE_DIR / "downloads"
LOGS_DIR = BASE_DIR / "logs"

URL_ESTATICA_DEFAULT = (
   "https://books.toscrape.com"
)
URL_DINAMICA_DEFAULT = (
    "https://webscraper.io/test-sites/e-commerce/scroll/computers/laptops"
)


class FormateadorJson(logging.Formatter):
    """Convierte cada registro del sistema en una linea JSON."""

    CAMPOS_EXTRA = (
        "accion",
        "archivo",
        "cantidad",
        "ciclo",
        "detalle",
        "evento",
        "hash_sha256",
        "maximo",
        "perfil",
        "tipo",
        "titulo",
        "url",
        "url_origen",
        "variables",
    )

    def format(self, record):
        contenido = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for campo in self.CAMPOS_EXTRA:
            if hasattr(record, campo):
                contenido[campo] = getattr(record, campo)
        if record.exc_info:
            contenido["exception"] = self.formatException(record.exc_info)
        return json.dumps(contenido, ensure_ascii=False, default=str)


def configurar_logging():
    """Configura consola y logs/scraper.log con registros JSON."""
    raiz = logging.getLogger()
    if getattr(raiz, "_cloud_utn_configurado", False):
        return

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    formateador = FormateadorJson()

    archivo = logging.FileHandler(
        LOGS_DIR / "scraper.log",
        encoding="utf-8",
    )
    archivo.setFormatter(formateador)
    consola = logging.StreamHandler()
    consola.setFormatter(formateador)

    raiz.handlers.clear()
    raiz.setLevel(logging.INFO)
    raiz.addHandler(archivo)
    raiz.addHandler(consola)
    raiz._cloud_utn_configurado = True


configurar_logging()
logger = logging.getLogger(__name__)


def obtener_conexion_db():
    """Establece la conexion PostgreSQL configurada en .env."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "cloud_scraping_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD","admin"),
        connect_timeout=5,
        application_name="cloud_scraping_utn",
    )


def normalizar_precio(precio_texto):
    """Convierte formatos monetarios comunes a Decimal para compararlos."""
    if precio_texto is None:
        return None
    try:
        limpio = re.sub(r"[^\d.,]", "", str(precio_texto))
        if "," in limpio and "." in limpio:
            separador_decimal = (
                "," if limpio.rfind(",") > limpio.rfind(".") else "."
            )
            separador_miles = "." if separador_decimal == "," else ","
            limpio = limpio.replace(separador_miles, "")
            if separador_decimal == ",":
                limpio = limpio.replace(",", ".")
        elif "," in limpio or "." in limpio:
            separador = "," if "," in limpio else "."
            partes = limpio.split(separador)
            if len(partes) > 2 or len(partes[-1]) == 3:
                limpio = "".join(partes)
            elif separador == ",":
                limpio = limpio.replace(",", ".")
        return Decimal(limpio) if limpio else None
    except (InvalidOperation, ValueError):
        return None


def asegurar_tablas(cursor):
    """Crea o migra las tablas requeridas sin borrar datos existentes."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS registros_scraping (
            id SERIAL PRIMARY KEY,
            titulo VARCHAR(255) NOT NULL,
            precio VARCHAR(100),
            enlace_archivo TEXT,
            tipo_scraping VARCHAR(50) NOT NULL,
            descripcion TEXT,
            calificacion NUMERIC(5, 2),
            cantidad INTEGER,
            oferta BOOLEAN NOT NULL DEFAULT FALSE,
            pagina TEXT,
            fecha_extraccion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ultima_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        ALTER TABLE registros_scraping
            ADD COLUMN IF NOT EXISTS descripcion TEXT,
            ADD COLUMN IF NOT EXISTS calificacion NUMERIC(5, 2),
            ADD COLUMN IF NOT EXISTS cantidad INTEGER,
            ADD COLUMN IF NOT EXISTS oferta BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS pagina TEXT,
            ADD COLUMN IF NOT EXISTS ultima_actualizacion
                TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            uq_registros_scraping_origen
        ON registros_scraping (tipo_scraping, enlace_archivo)
        WHERE enlace_archivo IS NOT NULL
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_registros_scraping_fecha
        ON registros_scraping (ultima_actualizacion DESC)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS control_archivos (
            id SERIAL PRIMARY KEY,
            nombre_archivo VARCHAR(255) NOT NULL,
            ruta_local TEXT NOT NULL,
            hash_sha256 VARCHAR(64) NOT NULL,
            url_origen TEXT,
            pagina_origen TEXT,
            tipo_contenido VARCHAR(150),
            tamano_bytes BIGINT,
            ultima_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ultima_verificacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        ALTER TABLE control_archivos
            ADD COLUMN IF NOT EXISTS url_origen TEXT,
            ADD COLUMN IF NOT EXISTS pagina_origen TEXT,
            ADD COLUMN IF NOT EXISTS tipo_contenido VARCHAR(150),
            ADD COLUMN IF NOT EXISTS tamano_bytes BIGINT,
            ADD COLUMN IF NOT EXISTS ultima_verificacion
                TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_control_archivos_url
        ON control_archivos (url_origen)
        WHERE url_origen IS NOT NULL
        """
    )


def _decimal_o_none(valor):
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor))
    except InvalidOperation:
        return None


def guardar_o_actualizar_registro(cursor, item, tipo):
    """Inserta o actualiza un registro y devuelve la accion detectada."""
    titulo = str(item.get("titulo") or "").strip()
    if not titulo:
        raise ValueError("El registro no contiene un titulo valido")

    precio = item.get("precio")
    enlace = item.get("enlace_archivo")
    descripcion = item.get("descripcion")
    calificacion = _decimal_o_none(item.get("calificacion"))
    cantidad = item.get("cantidad")
    oferta = bool(item.get("oferta"))
    pagina = item.get("pagina")

    if enlace:
        cursor.execute(
            """
            SELECT * FROM registros_scraping
            WHERE enlace_archivo = %s AND tipo_scraping = %s
            """,
            (enlace, tipo),
        )
    else:
        cursor.execute(
            """
            SELECT * FROM registros_scraping
            WHERE titulo = %s AND tipo_scraping = %s
            """,
            (titulo, tipo),
        )
    existente = cursor.fetchone()

    if not existente:
        cursor.execute(
            """
            INSERT INTO registros_scraping (
                titulo, precio, enlace_archivo, tipo_scraping, descripcion,
                calificacion, cantidad, oferta, pagina
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                titulo,
                precio,
                enlace,
                tipo,
                descripcion,
                calificacion,
                cantidad,
                oferta,
                pagina,
            ),
        )
        accion = "nuevo"
    else:
        cambio = any(
            (
                titulo != existente["titulo"],
                normalizar_precio(precio)
                != normalizar_precio(existente["precio"]),
                enlace != existente["enlace_archivo"],
                descripcion != existente.get("descripcion"),
                calificacion != existente.get("calificacion"),
                cantidad != existente.get("cantidad"),
                oferta != bool(existente.get("oferta")),
                pagina != existente.get("pagina"),
            )
        )
        if not cambio:
            return "sin_cambios"

        cursor.execute(
            """
            UPDATE registros_scraping
            SET titulo = %s,
                precio = %s,
                enlace_archivo = %s,
                descripcion = %s,
                calificacion = %s,
                cantidad = %s,
                oferta = %s,
                pagina = %s,
                ultima_actualizacion = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                titulo,
                precio,
                enlace,
                descripcion,
                calificacion,
                cantidad,
                oferta,
                pagina,
                existente["id"],
            ),
        )
        accion = "modificado"

    logger.info(
        "Cambio detectado en datos estructurados",
        extra={
            "evento": "cambio_registro",
            "accion": accion,
            "titulo": titulo,
            "tipo": tipo,
        },
    )
    return accion


def _crear_evento(accion, titulo, tipo, detalle=None):
    colores = {
        "nuevo": "#198754",
        "modificado": "#0d6efd",
        "eliminado": "#dc3545",
        "restaurado": "#6f42c1",
    }
    ahora = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "title": f"{accion.capitalize()}: {titulo}",
        "start": ahora,
        "color": colores.get(accion, "#6c757d"),
        "extendedProps": {
            "accion": accion,
            "tipo": tipo,
            "detalle": detalle,
        },
    }


def _identidad_registro(item):
    enlace = item.get("enlace_archivo")
    return ("url", enlace) if enlace else ("titulo", item["titulo"].casefold())


def sincronizar_registros(cursor, items, tipo):
    """Sincroniza altas, modificaciones y eliminaciones de una fuente."""
    resumen = {"nuevos": 0, "modificados": 0, "eliminados": 0}
    eventos = []
    if not items:
        logger.warning(
            "Fuente sin registros; se omite la eliminacion preventiva",
            extra={"evento": "fuente_vacia", "tipo": tipo},
        )
        return resumen, eventos

    identidades_actuales = set()
    for item in items:
        identidades_actuales.add(_identidad_registro(item))
        accion = guardar_o_actualizar_registro(cursor, item, tipo)
        if accion == "nuevo":
            resumen["nuevos"] += 1
        elif accion == "modificado":
            resumen["modificados"] += 1
        if accion != "sin_cambios":
            eventos.append(
                _crear_evento(accion, item["titulo"], f"scraping_{tipo}")
            )

    cursor.execute(
        """
        SELECT id, titulo, enlace_archivo
        FROM registros_scraping
        WHERE tipo_scraping = %s
        """,
        (tipo,),
    )
    for existente in cursor.fetchall():
        identidad = (
            ("url", existente["enlace_archivo"])
            if existente["enlace_archivo"]
            else ("titulo", existente["titulo"].casefold())
        )
        if identidad in identidades_actuales:
            continue
        cursor.execute(
            "DELETE FROM registros_scraping WHERE id = %s",
            (existente["id"],),
        )
        resumen["eliminados"] += 1
        eventos.append(
            _crear_evento(
                "eliminado",
                existente["titulo"],
                f"scraping_{tipo}",
            )
        )
        logger.info(
            "Registro eliminado porque desaparecio del origen",
            extra={
                "evento": "cambio_registro",
                "accion": "eliminado",
                "titulo": existente["titulo"],
                "tipo": tipo,
            },
        )
    return resumen, eventos


def _ruta_relativa_proyecto(ruta):
    ruta_resuelta = Path(ruta).resolve()
    try:
        return str(ruta_resuelta.relative_to(BASE_DIR))
    except ValueError:
        return str(ruta_resuelta)


def _eliminar_descarga_segura(ruta):
    ruta_objetivo = Path(ruta)
    if not ruta_objetivo.is_absolute():
        ruta_objetivo = BASE_DIR / ruta_objetivo
    ruta_objetivo = ruta_objetivo.resolve()
    try:
        ruta_objetivo.relative_to(DOWNLOADS_DIR.resolve())
    except ValueError:
        logger.error(
            "Se rechazo una ruta fuera de downloads",
            extra={
                "evento": "ruta_descarga_invalida",
                "detalle": str(ruta_objetivo),
            },
        )
        return
    if ruta_objetivo.is_file():
        ruta_objetivo.unlink()


def sincronizar_archivos(cursor, enlaces, pagina_origen):
    """Sincroniza archivos locales, hashes y metadatos con su pagina origen."""
    resumen = {"nuevos": 0, "modificados": 0, "eliminados": 0}
    eventos = []
    urls_actuales = set(enlaces)

    for enlace in enlaces:
        cursor.execute(
            """
            SELECT * FROM control_archivos
            WHERE url_origen = %s
            """,
            (enlace,),
        )
        existente = cursor.fetchone()
        archivo_local_antes = False
        if existente:
            ruta_anterior = Path(existente["ruta_local"])
            if not ruta_anterior.is_absolute():
                ruta_anterior = BASE_DIR / ruta_anterior
            archivo_local_antes = ruta_anterior.is_file()

        metadatos = descargar_y_verificar_archivo(
            enlace,
            carpeta_destino=DOWNLOADS_DIR,
        )
        if not metadatos:
            continue

        ruta_local = _ruta_relativa_proyecto(metadatos["ruta_local"])
        if not existente:
            cursor.execute(
                """
                INSERT INTO control_archivos (
                    nombre_archivo, ruta_local, hash_sha256, url_origen,
                    pagina_origen, tipo_contenido, tamano_bytes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    metadatos["nombre_archivo"],
                    ruta_local,
                    metadatos["hash_sha256"],
                    enlace,
                    pagina_origen,
                    metadatos["tipo_contenido"],
                    metadatos["tamano_bytes"],
                ),
            )
            accion = "nuevo"
            resumen["nuevos"] += 1
        else:
            hash_cambio = existente["hash_sha256"] != metadatos["hash_sha256"]
            accion = None
            if hash_cambio:
                accion = "modificado"
                resumen["modificados"] += 1
            elif not archivo_local_antes:
                accion = "restaurado"
                resumen["modificados"] += 1

            cursor.execute(
                """
                UPDATE control_archivos
                SET nombre_archivo = %s,
                    ruta_local = %s,
                    hash_sha256 = %s,
                    pagina_origen = %s,
                    tipo_contenido = %s,
                    tamano_bytes = %s,
                    ultima_actualizacion = CASE
                        WHEN hash_sha256 IS DISTINCT FROM %s
                        THEN CURRENT_TIMESTAMP
                        ELSE ultima_actualizacion
                    END,
                    ultima_verificacion = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    metadatos["nombre_archivo"],
                    ruta_local,
                    metadatos["hash_sha256"],
                    pagina_origen,
                    metadatos["tipo_contenido"],
                    metadatos["tamano_bytes"],
                    metadatos["hash_sha256"],
                    existente["id"],
                ),
            )

        if accion:
            eventos.append(
                _crear_evento(
                    accion,
                    metadatos["nombre_archivo"],
                    "archivo",
                    metadatos["hash_sha256"],
                )
            )

    cursor.execute(
        """
        SELECT id, nombre_archivo, ruta_local, url_origen
        FROM control_archivos
        WHERE pagina_origen = %s
        """,
        (pagina_origen,),
    )
    for existente in cursor.fetchall():
        if existente["url_origen"] in urls_actuales:
            continue
        _eliminar_descarga_segura(existente["ruta_local"])
        cursor.execute(
            "DELETE FROM control_archivos WHERE id = %s",
            (existente["id"],),
        )
        resumen["eliminados"] += 1
        eventos.append(
            _crear_evento(
                "eliminado",
                existente["nombre_archivo"],
                "archivo",
            )
        )
        logger.info(
            "Archivo eliminado porque desaparecio del origen",
            extra={
                "evento": "cambio_archivo",
                "accion": "eliminado",
                "archivo": existente["nombre_archivo"],
            },
        )

    return resumen, eventos


def _serializar(valor):
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    return valor


def _escribir_json_atomico(ruta, contenido):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{ruta.name}.",
            suffix=".tmp",
            dir=str(ruta.parent),
            delete=False,
        ) as archivo:
            temporal = Path(archivo.name)
            json.dump(
                contenido,
                archivo,
                ensure_ascii=False,
                indent=2,
                default=_serializar,
            )
        os.replace(temporal, ruta)
        temporal = None
    finally:
        if temporal and temporal.exists():
            temporal.unlink()


def exportar_json(cursor, eventos_nuevos):
    """Genera los tres JSON consumidos por la API y el dashboard."""
    generado = datetime.now(timezone.utc).isoformat()

    cursor.execute(
        """
        SELECT id, titulo, precio, enlace_archivo, tipo_scraping,
               descripcion, calificacion, cantidad, oferta, pagina,
               fecha_extraccion, ultima_actualizacion
        FROM registros_scraping
        ORDER BY ultima_actualizacion DESC, id DESC
        """
    )
    resultados = [dict(fila) for fila in cursor.fetchall()]

    cursor.execute(
        """
        SELECT id, nombre_archivo, ruta_local, hash_sha256, url_origen,
               pagina_origen, tipo_contenido, tamano_bytes,
               ultima_actualizacion, ultima_verificacion
        FROM control_archivos
        ORDER BY ultima_actualizacion DESC, id DESC
        """
    )
    archivos = [dict(fila) for fila in cursor.fetchall()]

    ruta_eventos = DATA_DIR / "events.json"
    eventos_anteriores = []
    if ruta_eventos.exists():
        try:
            contenido_anterior = json.loads(
                ruta_eventos.read_text(encoding="utf-8")
            )
            eventos_anteriores = contenido_anterior.get("data", [])
        except (OSError, json.JSONDecodeError, AttributeError):
            logger.warning(
                "events.json estaba dañado y sera regenerado",
                extra={"evento": "json_eventos_regenerado"},
            )
    eventos = (eventos_anteriores + eventos_nuevos)[-300:]

    _escribir_json_atomico(
        DATA_DIR / "results.json",
        {"generated_at": generado, "count": len(resultados), "data": resultados},
    )
    _escribir_json_atomico(
        DATA_DIR / "files.json",
        {"generated_at": generado, "count": len(archivos), "data": archivos},
    )
    _escribir_json_atomico(
        ruta_eventos,
        {"generated_at": generado, "count": len(eventos), "data": eventos},
    )

    logger.info(
        "Archivos JSON generados",
        extra={
            "evento": "json_exportado",
            "cantidad": len(resultados),
        },
    )


def orquestar_pipeline():
    """Ejecuta scraping, deteccion de cambios, PostgreSQL y exportacion JSON."""
    inicio = datetime.now(timezone.utc)
    logger.info(
        "Iniciando pipeline",
        extra={"evento": "pipeline_inicio"},
    )

    url_estatica = os.getenv("STATIC_SCRAPER_URL", URL_ESTATICA_DEFAULT)
    url_dinamica = os.getenv("DYNAMIC_SCRAPER_URL", URL_DINAMICA_DEFAULT)

    datos_estaticos, enlaces_archivos = ejecutar_scraping_estatico(
        url_estatica,
        incluir_archivos=True,
    )
    datos_dinamicos = ejecutar_scraping_dinamico(url_dinamica)

    conexion = None
    cursor = None
    resumen = {
        "estatico": {},
        "dinamico": {},
        "archivos": {},
        "duracion_segundos": 0,
    }
    eventos = []
    try:
        conexion = obtener_conexion_db()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        asegurar_tablas(cursor)

        resumen["estatico"], eventos_estaticos = sincronizar_registros(
            cursor,
            datos_estaticos,
            "estatico",
        )
        resumen["dinamico"], eventos_dinamicos = sincronizar_registros(
            cursor,
            datos_dinamicos,
            "dinamico",
        )
        eventos.extend(eventos_estaticos)
        eventos.extend(eventos_dinamicos)

        if datos_estaticos or enlaces_archivos:
            resumen["archivos"], eventos_archivos = sincronizar_archivos(
                cursor,
                enlaces_archivos,
                url_estatica,
            )
            eventos.extend(eventos_archivos)
        else:
            resumen["archivos"] = {
                "nuevos": 0,
                "modificados": 0,
                "eliminados": 0,
            }
            logger.warning(
                "No se sincronizan archivos porque fallo la fuente estatica",
                extra={"evento": "archivos_sin_fuente"},
            )

        conexion.commit()
        exportar_json(cursor, eventos)
        resumen["duracion_segundos"] = round(
            (datetime.now(timezone.utc) - inicio).total_seconds(),
            2,
        )
        logger.info(
            "Pipeline completado",
            extra={
                "evento": "pipeline_fin",
                "cantidad": len(datos_estaticos) + len(datos_dinamicos),
                "detalle": resumen,
            },
        )
        return resumen
    except Exception as error:
        if conexion:
            conexion.rollback()
        logger.exception(
            "El pipeline fallo",
            extra={"evento": "pipeline_error", "detalle": str(error)},
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


if __name__ == "__main__":
    resultado = orquestar_pipeline()
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
