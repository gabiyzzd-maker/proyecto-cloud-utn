import json
import logging
import os
import re

from dotenv import load_dotenv
from openai import AzureOpenAI


load_dotenv()
logger = logging.getLogger(__name__)

SELECTOR_KEYS = ("contenedor", "titulo", "precio", "enlace")


def obtener_cliente_azure():
    """Crea el cliente Azure OpenAI solo cuando la configuracion esta completa."""
    variables = (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_KEY",
        "AZURE_OPENAI_VERSION",
    )
    faltantes = [nombre for nombre in variables if not os.getenv(nombre)]
    if faltantes:
        logger.error(
            "Faltan variables para Azure OpenAI",
            extra={
                "evento": "llm_configuracion_incompleta",
                "variables": faltantes,
            },
        )
        return None

    return AzureOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_KEY"),
        api_version=os.getenv("AZURE_OPENAI_VERSION"),
        timeout=20,
        max_retries=1,
    )


def limpiar_selector(texto):
    """Retira markdown y comillas envolventes de un selector generado."""
    if not texto:
        return texto
    limpio = texto.strip()
    limpio = re.sub(r"^```[a-zA-Z]*\s*", "", limpio)
    limpio = re.sub(r"\s*```$", "", limpio)
    return limpio.strip().strip('"').strip("'").strip()


def _contenido_respuesta(respuesta):
    if not respuesta.choices:
        raise ValueError("Azure OpenAI devolvio una respuesta sin opciones")
    contenido = respuesta.choices[0].message.content
    if not contenido:
        raise ValueError("Azure OpenAI devolvio contenido vacio")
    return contenido


def generar_selector_dinamico(html_fragmento, tipo_elemento="precio"):
    """Solicita un selector CSS unico para el elemento indicado."""
    cliente = obtener_cliente_azure()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not cliente or not deployment:
        return None

    try:
        respuesta = cliente.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Analiza HTML para web scraping. Devuelve unicamente un "
                        "selector CSS valido, estable y especifico. No uses markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Elemento requerido: {tipo_elemento}\n"
                        f"HTML:\n{html_fragmento[:12000]}"
                    ),
                },
            ],
            temperature=0,
            max_tokens=80,
        )
        selector = limpiar_selector(_contenido_respuesta(respuesta))
        logger.info(
            "Selector generado por Azure OpenAI",
            extra={"evento": "llm_selector_generado", "tipo": tipo_elemento},
        )
        return selector
    except Exception as error:
        logger.exception(
            "Azure OpenAI no pudo generar el selector",
            extra={"evento": "error_llm_selector", "detalle": str(error)},
        )
        return None


def generar_mapa_selectores(html_fragmento):
    """Genera un mapa JSON de selectores para adaptar un scraper roto."""
    cliente = obtener_cliente_azure()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not cliente or not deployment:
        return None

    try:
        respuesta = cliente.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres especialista en selectores CSS para scraping. "
                        "Devuelve solo JSON valido, sin markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Genera selectores CSS relativos y estables para productos. "
                        "Formato exacto: "
                        '{"contenedor":"...","titulo":"...","precio":"...",'
                        '"enlace":"...","descripcion":"..."}. '
                        "El selector enlace debe apuntar a un elemento con href. "
                        f"HTML:\n{html_fragmento[:12000]}"
                    ),
                },
            ],
            temperature=0,
            max_tokens=220,
            response_format={"type": "json_object"},
        )
        contenido = _contenido_respuesta(respuesta)
        mapa = json.loads(contenido)
        for clave in SELECTOR_KEYS:
            if not isinstance(mapa.get(clave), str) or not mapa[clave].strip():
                raise ValueError(f"Falta el selector requerido: {clave}")
            mapa[clave] = mapa[clave].strip()
        if mapa.get("descripcion") is not None:
            mapa["descripcion"] = str(mapa["descripcion"]).strip() or None

        logger.info(
            "Mapa de selectores generado por Azure OpenAI",
            extra={"evento": "llm_mapa_generado"},
        )
        return mapa
    except Exception as error:
        logger.exception(
            "Azure OpenAI no pudo adaptar los selectores",
            extra={"evento": "error_llm_mapa", "detalle": str(error)},
        )
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    HTML_EJEMPLO = (
        '<article class="product-card">'
        '<a class="name" href="/producto/1">Portatil</a>'
        '<span class="price">$45.99</span>'
        "</article>"
    )
    print(generar_mapa_selectores(HTML_EJEMPLO))
