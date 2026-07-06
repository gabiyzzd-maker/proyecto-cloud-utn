# Guía de Inicio - Plataforma Cloud UTN

## 1. Preparar el equipo

Instalar Python 3.9+, Google Chrome y PostgreSQL. Desde la raíz:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Configurar variables

Crear `.env` en la raíz con las variables documentadas en `README.md`.
Verificar especialmente `DB_PASSWORD` y las cuatro variables
`AZURE_OPENAI_*`. No subir `.env` al repositorio.

## 3. Preparar PostgreSQL

Opción A, crear una base vacía; `main.py` crea/migra las tablas:

```powershell
createdb -U postgres cloud_scraping_db
python main.py
```

Opción B, restaurar los datos de evidencia:

```powershell
createdb -U postgres cloud_scraping_db
pg_restore -U postgres -d cloud_scraping_db cloud_scraping_db.backup
```

El script legible equivalente es `cloud_scraping_db.sql`.

## 4. Verificar módulos

```powershell
python -m scraper.scraper_static
python -m scraper.scraper_dynamic
```

El primero debe obtener productos y enlaces de archivos. El segundo debe abrir
Chrome en modo headless, hacer scroll y cerrarlo al terminar.

## 5. Ejecutar el sistema

Terminal 1, pipeline:

```powershell
python main.py
```

Terminal 2, API y dashboard:

```powershell
python api/json_api_server.py
```

Abrir `http://127.0.0.1:5000`. Las pestañas Resultados, Archivos y Calendario
deben mostrar la información de `data/`.

## 6. Automatización

```powershell
python scheduler.py
```

El worker ejecuta una vez al iniciar y luego cada 30 minutos. Para configurar
una hora:

```powershell
$env:SCHEDULER_MINUTES="60"
python scheduler.py
```

## 7. Pruebas

```powershell
python -m unittest discover -s tests -v
```

Para incluir PostgreSQL:

```powershell
$env:RUN_DB_TESTS="1"
python -m unittest discover -s tests -v
Remove-Item Env:RUN_DB_TESTS
```

La integración crea un esquema temporal dentro de una transacción y luego lo
revierte.

## 8. Probar detección de archivos

1. Ejecutar `python main.py`.
2. Borrar manualmente un archivo dentro de `downloads/`.
3. Ejecutar `python main.py` de nuevo.
4. Confirmar que el archivo se restaura y aparece un evento `Restaurado`.

Las pruebas automatizadas también simulan contenido nuevo, sin cambios y
contenido modificado para verificar los hashes.

## 9. Evidencia de entrega

Antes de grabar el video:

1. Ejecutar las pruebas con PostgreSQL.
2. Ejecutar `python main.py`.
3. Iniciar API/dashboard y recorrer las tres pestañas.
4. Mostrar `logs/scraper.log`, los tres JSON y `downloads/`.
5. Mostrar la ejecución de `scheduler.py`.
6. Regenerar los respaldos después de la última extracción.
