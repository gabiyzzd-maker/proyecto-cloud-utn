import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from psycopg2 import sql
from psycopg2.extras import RealDictCursor

import api.json_api_server as api_server
from llm.llm_selector import generar_mapa_selectores, limpiar_selector
from main import (
    asegurar_tablas,
    normalizar_precio,
    obtener_conexion_db,
    sincronizar_registros,
)
from scraper.scraper_static import (
    descargar_y_verificar_archivo,
    extraer_enlaces_archivos,
    extraer_productos,
)


HTML_PRODUCTO = """
<html>
  <body>
    <div class="card thumbnail" itemscope
         itemtype="https://schema.org/Product">
      <span class="price"><span itemprop="price">$416.99</span></span>
      <a class="title" href="/product/31" title="Packard 255 G2">
        Packard 255 G2
      </a>
      <p class="description">Laptop para pruebas</p>
      <div class="ratings">
        <p class="review-count"><span itemprop="reviewCount">2</span> reviews</p>
        <p data-rating="4"></p>
      </div>
      <img src="/files/laptop.png" alt="Laptop">
      <a href="/files/manual.pdf">Manual</a>
    </div>
  </body>
</html>
"""


class ParserTests(unittest.TestCase):
    def test_extrae_campos_estructurados(self):
        resultado = extraer_productos(
            HTML_PRODUCTO,
            "https://example.test/catalog",
            usar_llm=False,
        )

        self.assertEqual(len(resultado), 1)
        producto = resultado[0]
        self.assertEqual(producto["titulo"], "Packard 255 G2")
        self.assertEqual(producto["precio"], "$416.99")
        self.assertEqual(
            producto["enlace_archivo"],
            "https://example.test/product/31",
        )
        self.assertEqual(producto["descripcion"], "Laptop para pruebas")
        self.assertEqual(producto["calificacion"], 4.0)
        self.assertEqual(producto["cantidad"], 2)

    def test_extrae_archivos_descargables_sin_duplicados(self):
        enlaces = extraer_enlaces_archivos(
            HTML_PRODUCTO,
            "https://example.test/catalog",
        )

        self.assertEqual(
            enlaces,
            [
                "https://example.test/files/manual.pdf",
                "https://example.test/files/laptop.png",
            ],
        )

    def test_layout_actual_de_mercado_libre(self):
        html = """
        <li class="ui-search-layout__item">
          <div class="poly-card">
            <a class="poly-component__title"
               href="https://example.test/item/123">Laptop de prueba</a>
            <div class="poly-price__current">
              <span class="andes-money-amount__fraction">299.900</span>
            </div>
          </div>
        </li>
        """
        resultado = extraer_productos(
            html,
            "https://example.test",
            usar_llm=False,
        )
        self.assertEqual(resultado[0]["titulo"], "Laptop de prueba")
        self.assertEqual(resultado[0]["precio"], "299.900")


class PriceTests(unittest.TestCase):
    def test_normaliza_formatos_monetarios(self):
        casos = {
            "$1,200.50": Decimal("1200.50"),
            "1.200,50 EUR": Decimal("1200.50"),
            "₡15.990": Decimal("15990"),
            "$416.99": Decimal("416.99"),
            "299": Decimal("299"),
            None: None,
        }
        for valor, esperado in casos.items():
            with self.subTest(valor=valor):
                self.assertEqual(normalizar_precio(valor), esperado)


class RespuestaDescarga:
    def __init__(self, contenido, tipo="application/pdf"):
        self.contenido = contenido
        self.headers = {
            "Content-Length": str(len(contenido)),
            "Content-Type": tipo,
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        yield self.contenido


class FileChangeTests(unittest.TestCase):
    @patch("scraper.scraper_static.requests.get")
    def test_detecta_archivo_nuevo_igual_y_modificado(self, request_get):
        request_get.side_effect = [
            RespuestaDescarga(b"version-1"),
            RespuestaDescarga(b"version-1"),
            RespuestaDescarga(b"version-2"),
        ]

        with tempfile.TemporaryDirectory() as carpeta:
            nuevo = descargar_y_verificar_archivo(
                "https://example.test/manual.pdf",
                carpeta,
            )
            igual = descargar_y_verificar_archivo(
                "https://example.test/manual.pdf",
                carpeta,
            )
            modificado = descargar_y_verificar_archivo(
                "https://example.test/manual.pdf",
                carpeta,
            )

            self.assertEqual(nuevo["accion"], "nuevo")
            self.assertEqual(igual["accion"], "sin_cambios")
            self.assertEqual(modificado["accion"], "reemplazado")
            self.assertNotEqual(
                nuevo["hash_sha256"],
                modificado["hash_sha256"],
            )
            self.assertEqual(
                Path(modificado["ruta_local"]).read_bytes(),
                b"version-2",
            )


class LlmTests(unittest.TestCase):
    def test_limpia_selector(self):
        self.assertEqual(
            limpiar_selector('```css\n".product .price"\n```'),
            ".product .price",
        )

    @patch.dict(os.environ, {"AZURE_OPENAI_DEPLOYMENT": "gpt-4o-mini"})
    @patch("llm.llm_selector.obtener_cliente_azure")
    def test_genera_mapa_json(self, obtener_cliente):
        contenido = json.dumps(
            {
                "contenedor": ".product",
                "titulo": ".title",
                "precio": ".price",
                "enlace": "a.title",
                "descripcion": ".description",
            }
        )
        respuesta = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=contenido)
                )
            ]
        )
        cliente = MagicMock()
        cliente.chat.completions.create.return_value = respuesta
        obtener_cliente.return_value = cliente

        mapa = generar_mapa_selectores("<div class='product'></div>")

        self.assertEqual(mapa["precio"], ".price")
        cliente.chat.completions.create.assert_called_once()


class ApiTests(unittest.TestCase):
    def test_endpoints_json(self):
        with tempfile.TemporaryDirectory() as carpeta:
            directorio = Path(carpeta)
            for nombre in ("results.json", "files.json", "events.json"):
                (directorio / nombre).write_text(
                    json.dumps(
                        {
                            "generated_at": "2026-07-05T12:00:00+00:00",
                            "count": 1,
                            "data": [{"nombre_archivo": "test.pdf"}],
                        }
                    ),
                    encoding="utf-8",
                )

            with patch.object(api_server, "DATA_DIR", directorio):
                cliente = api_server.app.test_client()
                for endpoint in (
                    "/api/status",
                    "/api/results",
                    "/api/files",
                    "/api/events",
                ):
                    respuesta = cliente.get(endpoint)
                    self.assertEqual(respuesta.status_code, 200, endpoint)

                archivo = cliente.get("/api/files").get_json()["data"][0]
                self.assertEqual(
                    archivo["download_url"],
                    "/downloads/test.pdf",
                )


@unittest.skipUnless(
    os.getenv("RUN_DB_TESTS") == "1",
    "Defina RUN_DB_TESTS=1 para probar PostgreSQL",
)
class DatabaseChangeTests(unittest.TestCase):
    def setUp(self):
        self.conexion = obtener_conexion_db()
        self.cursor = self.conexion.cursor(cursor_factory=RealDictCursor)
        self.schema = f"test_cloud_{os.getpid()}"
        self.cursor.execute(
            sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema))
        )
        self.cursor.execute(
            sql.SQL("SET search_path TO {}").format(
                sql.Identifier(self.schema)
            )
        )
        asegurar_tablas(self.cursor)

    def tearDown(self):
        self.conexion.rollback()
        self.cursor.close()
        self.conexion.close()

    def test_detecta_altas_cambio_y_eliminacion(self):
        items = [
            {
                "titulo": "Producto A",
                "precio": "$10",
                "enlace_archivo": "https://example.test/a",
                "descripcion": "A",
                "calificacion": 4,
                "cantidad": 2,
                "oferta": False,
                "pagina": "https://example.test",
            },
            {
                "titulo": "Producto B",
                "precio": "$20",
                "enlace_archivo": "https://example.test/b",
                "descripcion": "B",
                "calificacion": 3,
                "cantidad": 1,
                "oferta": False,
                "pagina": "https://example.test",
            },
        ]
        primer_resumen, _ = sincronizar_registros(
            self.cursor,
            items,
            "prueba",
        )
        self.assertEqual(primer_resumen["nuevos"], 2)

        items[0]["precio"] = "$11"
        segundo_resumen, _ = sincronizar_registros(
            self.cursor,
            items[:1],
            "prueba",
        )
        self.assertEqual(segundo_resumen["modificados"], 1)
        self.assertEqual(segundo_resumen["eliminados"], 1)

        self.cursor.execute(
            "SELECT COUNT(*) AS total FROM registros_scraping"
        )
        self.assertEqual(self.cursor.fetchone()["total"], 1)


if __name__ == "__main__":
    unittest.main()
