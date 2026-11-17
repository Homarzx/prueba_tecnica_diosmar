"""Modelos de datos compartidos."""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class FacturaExtraida(BaseModel):
    """Lo que la IA devuelve. Todo es opcional: si un dato no está en el PDF debe venir null, nunca inventado."""

    ruc_emisor: str | None = Field(description="RUC del emisor (proveedor), 13 dígitos, solo números")
    numero_factura: str | None = Field(description="Número de factura con formato 001-001-000004521")
    fecha_emision: str | None = Field(description="Fecha de emisión en formato ISO YYYY-MM-DD")
    orden_compra: str | None = Field(description="Número de orden de compra tal como aparece en la factura")
    subtotal: float | None = Field(description="Subtotal antes de IVA, número con punto decimal")
    iva: float | None = Field(description="Valor del IVA, número con punto decimal")
    total: float | None = Field(description="Valor total a pagar, número con punto decimal")


@dataclass
class Problema:
    """Un motivo por el que la factura no se registra. `mensaje` debe entenderlo alguien de Compras."""

    codigo: str
    mensaje: str


@dataclass
class Resultado:
    archivo: str
    factura: FacturaExtraida | None = None
    problemas: list[Problema] = field(default_factory=list)
    registrada: bool = False
    aviso_enviado: bool = False
    error_sistema: str | None = None  # fallos técnicos (red, API caída), distintos de una factura inválida
