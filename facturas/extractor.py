"""Extracción de datos de la factura con IA.

El PDF se envía completo al modelo como documento: funciona tanto con PDFs de texto
como con PDFs escaneados, sin OCR aparte. La salida se pide como JSON con el esquema
de `FacturaExtraida` (salida estructurada) y se valida con Pydantic.

Proveedores soportados (variable PROVEEDOR_IA en .env):
    gemini  -> Google Gemini (plan gratuito; requiere GEMINI_API_KEY)
    claude  -> Anthropic Claude (requiere ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import base64
import logging
import re
import time
from pathlib import Path
from typing import Protocol

from .modelos import FacturaExtraida

log = logging.getLogger("facturas")

INSTRUCCIONES = """Eres un asistente que lee facturas electrónicas de proveedores ecuatorianos.
Extrae exactamente estos datos de la factura adjunta:
- ruc_emisor: RUC de quien EMITE la factura (el proveedor), no el del cliente/comprador.
- numero_factura: número de la factura, formato 001-001-000004521 (establecimiento-punto de emisión-secuencial).
- fecha_emision: en formato YYYY-MM-DD.
- orden_compra: número de orden de compra (puede aparecer como OC, O/C, Orden de compra, PO). Null si no aparece.
- subtotal, iva, total: montos numéricos con punto decimal, sin símbolos de moneda ni separadores de miles.

Reglas:
- Si un dato no aparece o no es legible, devuelve null. Nunca inventes ni calcules un dato que no esté impreso.
- Copia los valores tal como están impresos; no corrijas errores aritméticos de la factura.
"""


class ErrorExtraccion(Exception):
    pass


class Extractor(Protocol):
    def extraer(self, pdf: Path) -> FacturaExtraida: ...


class ExtractorGemini:
    """Gemini gratuito puede responder 429 (límite) o 503 (saturado): se reintenta con espera
    creciente y, si el modelo principal sigue fallando, se prueba con los modelos de respaldo."""

    REINTENTOS = 3
    CODIGOS_TRANSITORIOS = {429, 500, 503, 504}

    def __init__(self, modelo: str, respaldos: list[str] | None = None):
        from google import genai  # import diferido: solo se necesita si se usa este proveedor
        from google.genai import errors, types

        self.types, self.errors = types, errors
        self.client = genai.Client()  # lee GEMINI_API_KEY del entorno
        self.modelos = [modelo] + [m for m in (respaldos or []) if m and m != modelo]

    def _llamar(self, modelo: str, pdf_bytes: bytes):
        types = self.types
        return self.client.models.generate_content(
            model=modelo,
            contents=[types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"), INSTRUCCIONES],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=FacturaExtraida,
                temperature=0,
            ),
        )

    def extraer(self, pdf: Path) -> FacturaExtraida:
        pdf_bytes = pdf.read_bytes()
        ultimo_error: Exception | None = None
        respuesta = None
        for modelo in self.modelos:
            for intento in range(self.REINTENTOS):
                try:
                    respuesta = self._llamar(modelo, pdf_bytes)
                    break
                except self.errors.APIError as e:
                    ultimo_error = e
                    if e.code not in self.CODIGOS_TRANSITORIOS:
                        break  # error permanente (modelo inexistente, key inválida): pasar al respaldo
                    espera = 2 ** (intento + 1)
                    log.warning("  Gemini %s respondió %s; reintento en %ss", modelo, e.code, espera)
                    time.sleep(espera)
            if respuesta is not None:
                break
            log.warning("  Gemini %s no disponible; probando respaldo", modelo)
        if respuesta is None:
            raise ErrorExtraccion(f"No se pudo leer la factura con la IA (Gemini): {ultimo_error}")
        if respuesta.parsed is None:
            try:
                return normalizar(FacturaExtraida.model_validate_json(respuesta.text or ""))
            except Exception as e:
                raise ErrorExtraccion(f"Gemini no devolvió datos válidos: {(respuesta.text or '')[:200]}") from e
        return normalizar(respuesta.parsed)


class ExtractorClaude:
    def __init__(self, modelo: str):
        import anthropic

        self.client = anthropic.Anthropic()  # lee ANTHROPIC_API_KEY del entorno
        self.modelo = modelo

    def extraer(self, pdf: Path) -> FacturaExtraida:
        datos_b64 = base64.standard_b64encode(pdf.read_bytes()).decode()
        respuesta = self.client.messages.parse(
            model=self.modelo,
            max_tokens=16000,
            output_config={"effort": "low"},
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": datos_b64},
                    },
                    {"type": "text", "text": INSTRUCCIONES},
                ],
            }],
            output_format=FacturaExtraida,
        )
        if respuesta.stop_reason == "refusal":
            raise ErrorExtraccion("El modelo de IA se negó a procesar el documento")
        if respuesta.parsed_output is None:
            raise ErrorExtraccion(f"La IA no devolvió datos válidos (stop_reason={respuesta.stop_reason})")
        return normalizar(respuesta.parsed_output)


def crear_extractor(proveedor: str, modelo: str, respaldos: list[str] | None = None) -> Extractor:
    if proveedor == "gemini":
        return ExtractorGemini(modelo, respaldos)
    if proveedor == "claude":
        return ExtractorClaude(modelo)
    raise ValueError(f"PROVEEDOR_IA desconocido: {proveedor} (use gemini o claude)")


def normalizar(f: FacturaExtraida) -> FacturaExtraida:
    """Limpieza determinística posterior a la IA (espacios, guiones, etc.)."""
    datos = f.model_dump()
    if datos["ruc_emisor"]:
        datos["ruc_emisor"] = re.sub(r"\D", "", datos["ruc_emisor"])
    if datos["numero_factura"]:
        datos["numero_factura"] = re.sub(r"\s", "", datos["numero_factura"])
    if datos["orden_compra"]:
        datos["orden_compra"] = datos["orden_compra"].strip()
    return FacturaExtraida(**datos)
