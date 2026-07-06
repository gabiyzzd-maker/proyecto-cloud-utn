import logging
import os

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from scraper.scraper_static import ITEM_SELECTORS, extraer_productos


logger = logging.getLogger(__name__)

DEFAULT_WAIT_SECONDS = 20
DEFAULT_SCROLLS = 8
BLOCK_MARKERS = (
    "/gz/account-verification",
    "account-verification-main",
    "suspicious-traffic-frontend",
    "security/suspicious_traffic",
    "para continuar, ingresa",
)


def configurar_driver():
    """Configura Chrome para una ejecucion headless estable."""
    opciones = Options()
    opciones.add_argument("--headless=new")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--window-size=1920,1080")
    opciones.add_argument("--lang=es-CR")
    opciones.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    chrome_binary = os.getenv("CHROME_BINARY")
    if chrome_binary:
        opciones.binary_location = chrome_binary

    driver = webdriver.Chrome(options=opciones)
    driver.set_page_load_timeout(DEFAULT_WAIT_SECONDS)
    return driver


def _contar_productos(driver):
    for selector in ITEM_SELECTORS:
        elementos = driver.find_elements(By.CSS_SELECTOR, selector)
        if elementos:
            return len(elementos)
    return 0


def _pagina_bloqueada(driver):
    contenido = f"{driver.current_url}\n{driver.page_source}".lower()
    return any(marcador in contenido for marcador in BLOCK_MARKERS)


def hacer_scroll_infinito(driver, max_scrolls=DEFAULT_SCROLLS, espera=3):
    """Desplaza la pagina hasta que deja de crecer durante dos ciclos."""
    ciclos_estables = 0
    cantidad_anterior = _contar_productos(driver)

    for indice in range(max_scrolls):
        altura_anterior = driver.execute_script(
            "return document.body.scrollHeight"
        )
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        try:
            WebDriverWait(driver, espera).until(
                lambda navegador: (
                    _contar_productos(navegador) > cantidad_anterior
                    or navegador.execute_script(
                        "return document.body.scrollHeight"
                    )
                    > altura_anterior
                )
            )
        except TimeoutException:
            pass

        cantidad_actual = _contar_productos(driver)
        altura_actual = driver.execute_script(
            "return document.body.scrollHeight"
        )
        logger.info(
            "Ciclo de scroll completado",
            extra={
                "evento": "scroll_dinamico",
                "ciclo": indice + 1,
                "maximo": max_scrolls,
                "cantidad": cantidad_actual,
            },
        )

        if (
            cantidad_actual <= cantidad_anterior
            and altura_actual <= altura_anterior
        ):
            ciclos_estables += 1
        else:
            ciclos_estables = 0

        if ciclos_estables >= 2:
            break
        cantidad_anterior = cantidad_actual


def ejecutar_scraping_dinamico(
    url_objetivo,
    max_scrolls=DEFAULT_SCROLLS,
    timeout=DEFAULT_WAIT_SECONDS,
):
    """Extrae datos de una pagina con scroll dinamico mediante Selenium."""
    logger.info(
        "Iniciando scraping dinamico",
        extra={"evento": "scraping_dinamico_inicio", "url": url_objetivo},
    )
    driver = None
    try:
        driver = configurar_driver()
        driver.get(url_objetivo)
        WebDriverWait(driver, timeout).until(
            lambda navegador: navegador.execute_script(
                "return document.readyState"
            )
            == "complete"
        )

        try:
            WebDriverWait(driver, min(timeout, 8)).until(
                lambda navegador: (
                    _contar_productos(navegador) > 0
                    or _pagina_bloqueada(navegador)
                )
            )
        except TimeoutException:
            logger.warning(
                "No aparecieron productos con los selectores conocidos",
                extra={
                    "evento": "selectores_dinamicos_sin_coincidencia",
                    "url": url_objetivo,
                },
            )

        if _pagina_bloqueada(driver):
            logger.error(
                "El sitio bloqueo la automatizacion",
                extra={"evento": "scraping_bloqueado", "url": url_objetivo},
            )
            return []

        hacer_scroll_infinito(driver, max_scrolls=max_scrolls)
        resultados = extraer_productos(
            driver.page_source,
            driver.current_url or url_objetivo,
        )
        logger.info(
            "Scraping dinamico completado",
            extra={
                "evento": "scraping_dinamico_fin",
                "cantidad": len(resultados),
                "url": url_objetivo,
            },
        )
        return resultados
    except (TimeoutException, WebDriverException) as error:
        logger.exception(
            "Fallo el proceso de Selenium",
            extra={
                "evento": "error_scraping_dinamico",
                "url": url_objetivo,
                "detalle": str(error),
            },
        )
        return []
    finally:
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException:
                logger.exception(
                    "Chrome no pudo cerrarse limpiamente",
                    extra={"evento": "error_cierre_driver"},
                )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    URL_PRUEBA = (
        "https://webscraper.io/test-sites/e-commerce/scroll/computers/laptops"
    )
    datos = ejecutar_scraping_dinamico(URL_PRUEBA)
    print(f"Productos: {len(datos)}")
    for registro in datos[:3]:
        print(registro)
