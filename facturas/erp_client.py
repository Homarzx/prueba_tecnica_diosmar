"""Cliente de la API del ERP (rutas y campos verificados contra /openapi.json).

Endpoints:
    GET  /health                    sin token
    GET  /proveedores/{ruc}         -> Proveedor {ruc, razon_social, estado} | 404
    GET  /ordenes-compra/{numero}   -> OrdenCompra {numero, ruc_proveedor, fecha, subtotal, iva, total, estado} | 404
    GET  /facturas                  -> lista de facturas registradas con este token
    POST /facturas                  -> 201 FacturaRegistrada | 409 ya registrada | 422 datos inválidos
"""
from __future__ import annotations

from typing import Any

import httpx

from .modelos import FacturaExtraida

RUTA_PROVEEDOR = "/proveedores/{ruc}"
RUTA_ORDEN_COMPRA = "/ordenes-compra/{numero}"
RUTA_FACTURAS = "/facturas"

# Estados de la OC que permiten facturar; cualquier otro se rechaza (se compara en minúsculas)
ESTADOS_OC_VALIDOS = {"aprobada"}


class ErrorERP(Exception):
    """Fallo técnico del ERP (red, 5xx, 401...). No significa que la factura sea inválida."""


class FacturaYaRegistrada(Exception):
    """El ERP respondió 409 al registrar: la factura ya existe."""


class ClienteERP:
    def __init__(self, url: str, token: str, timeout: float = 15):
        self.http = httpx.Client(
            base_url=url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def _get(self, ruta: str) -> Any | None:
        try:
            r = self.http.get(ruta)
        except httpx.HTTPError as e:
            raise ErrorERP(f"No se pudo conectar con el ERP: {e}") from e
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise ErrorERP(f"ERP respondió {r.status_code} en GET {ruta}: {r.text[:300]}")
        return r.json()

    # --- Consultas -----------------------------------------------------------------------
    def proveedor(self, ruc: str) -> dict | None:
        return self._get(RUTA_PROVEEDOR.format(ruc=ruc))

    def orden_compra(self, numero: str) -> dict | None:
        return self._get(RUTA_ORDEN_COMPRA.format(numero=numero))

    def factura_existente(self, numero: str, ruc: str) -> bool:
        datos = self._get(RUTA_FACTURAS) or []
        if isinstance(datos, dict):  # por si la respuesta viene envuelta: {"items": [...]}
            datos = datos.get("items") or datos.get("data") or []
        return any(f.get("numero_factura") == numero and f.get("ruc") == ruc for f in datos)

    # --- Registro ------------------------------------------------------------------------
    def registrar_factura(self, f: FacturaExtraida) -> dict:
        cuerpo = {  # esquema FacturaEntrada
            "ruc": f.ruc_emisor,
            "numero_factura": f.numero_factura,
            "fecha_emision": f.fecha_emision,
            "orden_compra": f.orden_compra,
            "subtotal": f.subtotal,
            "iva": f.iva,
            "total": f.total,
        }
        try:
            r = self.http.post(RUTA_FACTURAS, json=cuerpo)
        except httpx.HTTPError as e:
            raise ErrorERP(f"No se pudo conectar con el ERP: {e}") from e
        if r.status_code == 409:
            raise FacturaYaRegistrada(r.text[:300])
        if r.status_code >= 400:
            raise ErrorERP(f"ERP rechazó el registro ({r.status_code}): {r.text[:300]}")
        return r.json() if r.content else {}


# --- Interpretación de respuestas ---------------------------------------------------------
def proveedor_activo(p: dict) -> bool:
    return str(p.get("estado", "")).strip().lower() in {"activo", "activa", "active", "a"}


def ruc_de_orden(oc: dict) -> str | None:
    return str(oc["ruc_proveedor"]) if oc.get("ruc_proveedor") else None


def orden_vigente(oc: dict) -> bool:
    return str(oc.get("estado", "")).strip().lower() in ESTADOS_OC_VALIDOS
