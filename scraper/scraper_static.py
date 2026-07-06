import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-CR,es;q=0.9,en;q=0.8",
}
REQUEST_TIMEOUT = (5, 30)
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_BYTES", str(25 * 1024 * 1024)))

PRODUCT_PROFILES = (
    {
        "name": "webscraper_test_site",
        "items": ".card.thumbnail",
        "title": "a.title, [itemprop='name']",
        "price": ".price [itemprop='price'], .price, [itemprop='price']",
        "link": "a.title[href], a[itemprop='name'][href]",
        "description": ".description, [itemprop='description']",
        "rating": ".ratings [data-rating], [itemprop='ratingValue']",
        "quantity": ".review-count [itemprop='reviewCount'], .review-count",
        "offer": ".discount, .old-price, .precio-anterior",
    },
    {
        "name": "mercadolibre_layout",
        "items": "li.ui-search-layout__item",
        "title": ".poly-component__title, .ui-search-item__title",
        "price": (
            ".poly-price__current .andes-money-amount__fraction, "
            ".ui-search-price__second-line .andes-money-amount__fraction, "
            ".andes-money-amount__fraction"
        ),
        "link": (
            "a.poly-component__title[href], "
            "a.ui-search-link[href], "
            "a.ui-search-item__group__element[href]"
        ),
        "description": ".poly-component__headline, .ui-search-item__subtitle",
        "rating": ".poly-reviews__rating, .ui-search-reviews__rating-number",
        "quantity": ".poly-reviews__total, .ui-search-reviews__amount",
        "offer": ".andes-money-amount--previous, .poly-price__discount",
    },
    {
        "name": "mercadolibre_poly_card",
        "items": ".poly-card",
        "title": ".poly-component__title, .ui-search-item__title",
        "price": (
            ".poly-price__current .andes-money-amount__fraction, "
            ".andes-money-amount__fraction"
        ),
        "link": "a.poly-component__title[href], a[href]",
        "description": ".poly-component__headline",
        "rating": ".poly-reviews__rating",
        "quantity": ".poly-reviews__total",
        "offer": ".andes-money-amount--previous, .poly-price__discount",
    },
    {
        "name": "schema_org_product",
        "items": "[itemscope][itemtype*='schema.org/Product']",
        "title": "[itemprop='name'], h2, h3",
        "price": "[itemprop='price'], .price, .precio",
        "link": "a[itemprop='name'][href], a[href]",
        "description": "[itemprop='description'], .description",
        "rating": "[itemprop='ratingValue']",
        "quantity": "[itemprop='reviewCount']",
        "offer": ".discount, .old-price, .precio-anterior",
    },
    {
        "name": "legacy_project",
        "items": ".producto, .item-aggregator",
        "title": ".titulo, h2, h3",
        "price": ".precio, .price",
        "link": "a.download-link[href], a[href]",
        "description": ".descripcion, .description, p",
        "rating": ".rating, .calificacion",
        "quantity": ".cantidad, .stock",
        "offer": ".oferta, .discount",
    },
)

ITEM_SELECTORS = tuple(profile["items"] for profile in PRODUCT_PROFILES)
FILE_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".svg",
    ".wav",
    ".webp",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
}


def calcular_hash_sha256(ruta_archivo):
    """Calcula el hash SHA-256 de un archivo local."""
    sha256_hash = hashlib.sha256()
    with open(ruta_archivo, "rb") as archivo:
        for bloque in iter(lambda: archivo.read(64 * 1024), b""):
            sha256_hash.update(bloque)
    return sha256_hash.hexdigest()


def _texto(elemento):
    if elemento is None:
        return None

    for atributo in ("title", "content", "value", "data-rating"):
        valor = elemento.get(atributo)
        if valor and str(valor).strip():
            return str(valor).strip()

    texto = elemento.get_text(" ", strip=True)
    return texto or None


def _numero(texto, entero=False):
    if not texto:
        return None
    coincidencia = re.search(r"\d+(?:[.,]\d+)?", str(texto))
    if not coincidencia:
        return None
    valor = coincidencia.group(0).replace(",", ".")
    try:
        return int(float(valor)) if entero else float(valor)
    except ValueError:
        return None


def _buscar_perfil(soup, usar_llm=False):
    for perfil in PRODUCT_PROFILES:
        elementos = soup.select(perfil["items"])
        if elementos:
            return perfil, elementos

    if not usar_llm:
        return None, []

    try:
        from llm.llm_selector import generar_mapa_selectores

        selectores = generar_mapa_selectores(str(soup)[:12000])
        if not selectores:
            return None, []

        perfil = {
            "name": "azure_openai",
            "items": selectores["contenedor"],
            "title": selectores["titulo"],
            "price": selectores["precio"],
            "link": selectores["enlace"],
            "description": selectores.get("descripcion"),
            "rating": None,
            "quantity": None,
            "offer": None,
        }
        elementos = soup.select(perfil["items"])
        return (perfil, elementos) if elementos else (None, [])
    except (KeyError, ValueError) as error:
        logger.warning(
            "Los selectores generados por IA no fueron validos",
            extra={"evento": "llm_selector_invalido", "detalle": str(error)},
        )
        return None, []


def extraer_productos(html, base_url, usar_llm=None):
    """Convierte HTML estatico o renderizado en registros normalizados."""
    if usar_llm is None:
        usar_llm = os.getenv("ENABLE_LLM_SELECTOR", "false").lower() == "true"

    soup = BeautifulSoup(html, "html.parser")
    perfil, elementos = _buscar_perfil(soup, usar_llm=usar_llm)
    if perfil is None:
        return []

    resultados = []
    vistos = set()
    for elemento in elementos:
        titulo = _texto(elemento.select_one(perfil["title"]))
        if not titulo:
            continue

        precio = _texto(elemento.select_one(perfil["price"]))
        enlace_tag = elemento.select_one(perfil["link"])
        enlace = None
        if enlace_tag and enlace_tag.get("href"):
            enlace = urljoin(base_url, enlace_tag["href"])

        descripcion_selector = perfil.get("description")
        rating_selector = perfil.get("rating")
        quantity_selector = perfil.get("quantity")
        offer_selector = perfil.get("offer")

        descripcion = (
            _texto(elemento.select_one(descripcion_selector))
            if descripcion_selector
            else None
        )
        calificacion = (
            _numero(_texto(elemento.select_one(rating_selector)))
            if rating_selector
            else None
        )
        cantidad = (
            _numero(_texto(elemento.select_one(quantity_selector)), entero=True)
            if quantity_selector
            else None
        )
        oferta = bool(offer_selector and elemento.select_one(offer_selector))

        clave = (titulo.casefold(), enlace or "")
        if clave in vistos:
            continue
        vistos.add(clave)

        resultados.append(
            {
                "titulo": titulo,
                "precio": precio,
                "enlace_archivo": enlace,
                "descripcion": descripcion,
                "calificacion": calificacion,
                "cantidad": cantidad,
                "oferta": oferta,
                "pagina": base_url,
            }
        )

    logger.info(
        "Extraccion de productos completada",
        extra={
            "evento": "productos_extraidos",
            "perfil": perfil["name"],
            "cantidad": len(resultados),
        },
    )
    return resultados


def extraer_enlaces_archivos(html, base_url):
    """Obtiene enlaces a archivos descargables presentes en una pagina HTML."""
    soup = BeautifulSoup(html, "html.parser")
    candidatos = []
    for selector, atributo in (
        ("a[href]", "href"),
        ("img[src]", "src"),
        ("source[src]", "src"),
        ("video[src]", "src"),
        ("audio[src]", "src"),
    ):
        candidatos.extend(
            elemento.get(atributo) for elemento in soup.select(selector)
        )

    enlaces = []
    vistos = set()
    for candidato in candidatos:
        if not candidato:
            continue
        enlace, _ = urldefrag(urljoin(base_url, candidato))
        if urlparse(enlace).scheme not in {"http", "https"}:
            continue
        extension = Path(unquote(urlparse(enlace).path)).suffix.lower()
        if extension not in FILE_EXTENSIONS or enlace in vistos:
            continue
        vistos.add(enlace)
        enlaces.append(enlace)
    return enlaces


def _nombre_archivo_seguro(url_archivo):
    ruta_url = unquote(urlparse(url_archivo).path).rstrip("/")
    nombre = os.path.basename(ruta_url) or "archivo_sin_nombre"
    nombre = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nombre)
    return nombre[:180] or "archivo_sin_nombre"


def descargar_y_verificar_archivo(url_archivo, carpeta_destino="./downloads"):
    """Descarga un archivo y lo reemplaza solo cuando cambia su SHA-256."""
    carpeta = Path(carpeta_destino)
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre_archivo = _nombre_archivo_seguro(url_archivo)
    ruta_final = carpeta / nombre_archivo
    ruta_temporal = None

    try:
        with requests.get(
            url_archivo,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            stream=True,
        ) as respuesta:
            respuesta.raise_for_status()
            content_length = int(respuesta.headers.get("Content-Length") or 0)
            if content_length > MAX_DOWNLOAD_BYTES:
                raise ValueError(
                    f"El archivo supera el limite de {MAX_DOWNLOAD_BYTES} bytes"
                )

            total = 0
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{nombre_archivo}.",
                suffix=".tmp",
                dir=str(carpeta),
                delete=False,
            ) as archivo_temporal:
                ruta_temporal = Path(archivo_temporal.name)
                for bloque in respuesta.iter_content(chunk_size=64 * 1024):
                    if not bloque:
                        continue
                    total += len(bloque)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            f"El archivo supera el limite de {MAX_DOWNLOAD_BYTES} bytes"
                        )
                    archivo_temporal.write(bloque)

            tipo_contenido = respuesta.headers.get(
                "Content-Type", "application/octet-stream"
            ).split(";", 1)[0]

        nuevo_hash = calcular_hash_sha256(ruta_temporal)
        if ruta_final.exists():
            hash_anterior = calcular_hash_sha256(ruta_final)
            if nuevo_hash == hash_anterior:
                ruta_temporal.unlink()
                accion = "sin_cambios"
            else:
                os.replace(ruta_temporal, ruta_final)
                accion = "reemplazado"
        else:
            os.replace(ruta_temporal, ruta_final)
            accion = "nuevo"
        ruta_temporal = None

        logger.info(
            "Archivo procesado",
            extra={
                "evento": "archivo_procesado",
                "archivo": nombre_archivo,
                "accion": accion,
                "hash_sha256": nuevo_hash,
            },
        )
        return {
            "nombre_archivo": nombre_archivo,
            "ruta_local": str(ruta_final),
            "hash_sha256": nuevo_hash,
            "tamano_bytes": ruta_final.stat().st_size,
            "tipo_contenido": tipo_contenido,
            "url_origen": url_archivo,
            "accion": accion,
        }
    except (OSError, requests.RequestException, ValueError) as error:
        logger.exception(
            "No se pudo descargar el archivo",
            extra={
                "evento": "error_descarga",
                "url_origen": url_archivo,
                "detalle": str(error),
            },
        )
        if ruta_temporal and ruta_temporal.exists():
            ruta_temporal.unlink()
        return None


def ejecutar_scraping_estatico(
    url_objetivo,
    timeout=REQUEST_TIMEOUT,
    incluir_archivos=False,
):
    """Extrae productos y, opcionalmente, enlaces descargables con BeautifulSoup."""
    logger.info(
        "Iniciando scraping estatico",
        extra={"evento": "scraping_estatico_inicio", "url": url_objetivo},
    )
    try:
        with requests.get(
            url_objetivo,
            headers=HEADERS,
            timeout=timeout,
        ) as respuesta:
            respuesta.raise_for_status()
            html = respuesta.content
            url_final = respuesta.url

        resultados = extraer_productos(html, url_final)
        archivos = extraer_enlaces_archivos(html, url_final)
        return (resultados, archivos) if incluir_archivos else resultados
    except requests.RequestException as error:
        logger.exception(
            "Fallo el scraping estatico",
            extra={
                "evento": "error_scraping_estatico",
                "url": url_objetivo,
                "detalle": str(error),
            },
        )
        return ([], []) if incluir_archivos else []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    URL_PRUEBA = (
        "https://webscraper.io/test-sites/e-commerce/static/computers/laptops"
    )
    productos, archivos = ejecutar_scraping_estatico(
        URL_PRUEBA, incluir_archivos=True
    )
    print(f"Productos: {len(productos)} | Archivos: {len(archivos)}")
    for registro in productos[:3]:
        print(registro)
