import logging
import os
import signal
import time
from threading import Event

from apscheduler.schedulers.background import BackgroundScheduler

from main import configurar_logging, orquestar_pipeline


configurar_logging()
logger = logging.getLogger(__name__)
detener = Event()


def tarea_programada():
    """Ejecuta el pipeline y deja el error registrado sin matar el worker."""
    try:
        orquestar_pipeline()
    except Exception:
        logger.exception(
            "Fallo la ejecucion programada",
            extra={"evento": "scheduler_error"},
        )


def manejar_apagado(signum, _frame):
    logger.info(
        "Senal de apagado recibida",
        extra={"evento": "scheduler_apagado", "detalle": signum},
    )
    detener.set()


def crear_scheduler():
    minutos = int(os.getenv("SCHEDULER_MINUTES", "30"))
    if minutos < 1:
        raise ValueError("SCHEDULER_MINUTES debe ser mayor o igual a 1")

    scheduler = BackgroundScheduler(timezone=os.getenv("TZ", "America/Costa_Rica"))
    scheduler.add_job(
        tarea_programada,
        "interval",
        minutes=minutos,
        id="pipeline_scraping",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler, minutos


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, manejar_apagado)
    signal.signal(signal.SIGINT, manejar_apagado)

    worker, intervalo = crear_scheduler()
    worker.start()
    logger.info(
        "Worker iniciado",
        extra={
            "evento": "scheduler_inicio",
            "detalle": f"Intervalo de {intervalo} minutos",
        },
    )

    tarea_programada()
    try:
        while not detener.wait(timeout=1):
            time.sleep(0)
    finally:
        worker.shutdown(wait=True)
        logger.info(
            "Worker detenido",
            extra={"evento": "scheduler_fin"},
        )
