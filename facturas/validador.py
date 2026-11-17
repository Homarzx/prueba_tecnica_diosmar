"""Reglas de validación.

Cada regla es una función pequeña que recibe la factura (y el contexto) y devuelve una
lista de `Problema`. Para agregar una regla nueva (p. ej. en el "cambio en vivo") basta
con escribir la función y añadirla a REGLAS_LOCALES o REGLAS_ERP.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable

from .config import Settings
from .erp_client import ClienteERP, orden_vigente, proveedor_activo, ruc_de_orden
from .modelos import FacturaExtraida, Problema

PATRON_NUMERO = re.compile(r"^\d{3}-\d{3}-\d{9}$")

CAMPOS = {
    "ruc_emisor": "RUC del emisor",
    "numero_factura": "número de factura",
    "fecha_emision": "fecha de emisión",
    "orden_compra": "orden de compra",
    "subtotal": "subtotal",
    "iva": "IVA",
    "total": "total",
}


@dataclass
class Contexto:
    cfg: Settings
    erp: ClienteERP | None = None
    proveedor: dict | None = None
    orden: dict | None = None


Regla = Callable[[FacturaExtraida, Contexto], list[Problema]]


def _d(x: float | None) -> Decimal:
    return Decimal(str(x)) if x is not None else Decimal("0")


# --- Reglas locales (no necesitan el ERP) -------------------------------------------------
def campos_obligatorios(f: FacturaExtraida, ctx: Contexto) -> list[Problema]:
    faltan = [nombre for campo, nombre in CAMPOS.items() if getattr(f, campo) in (None, "")]
    if not faltan:
        return []
    return [Problema("CAMPOS_FALTANTES",
                     f"No se pudo identificar en la factura: {', '.join(faltan)}. "
                     "Solicite al proveedor una factura que incluya esta información.")]


def formato_ruc(f: FacturaExtraida, ctx: Contexto) -> list[Problema]:
    ruc = f.ruc_emisor
    if not ruc:
        return []
    provincia = int(ruc[:2]) if len(ruc) >= 2 and ruc[:2].isdigit() else -1
    if len(ruc) != 13 or not ruc.isdigit() or not (1 <= provincia <= 24 or provincia == 30) or ruc.endswith("000"):
        return [Problema("RUC_INVALIDO", f"El RUC del emisor ({ruc}) no tiene un formato válido de 13 dígitos.")]
    return []


def formato_numero(f: FacturaExtraida, ctx: Contexto) -> list[Problema]:
    if f.numero_factura and not PATRON_NUMERO.match(f.numero_factura):
        return [Problema("NUMERO_INVALIDO",
                         f"El número de factura '{f.numero_factura}' no tiene el formato esperado (001-001-000000123).")]
    return []


def fecha_valida(f: FacturaExtraida, ctx: Contexto) -> list[Problema]:
    if not f.fecha_emision:
        return []
    try:
        fecha = date.fromisoformat(f.fecha_emision)
    except ValueError:
        return [Problema("FECHA_INVALIDA", f"La fecha de emisión '{f.fecha_emision}' no es una fecha válida.")]
    if fecha > date.today():
        return [Problema("FECHA_FUTURA", f"La fecha de emisión ({fecha:%d/%m/%Y}) es posterior a hoy.")]
    return []


def montos_cuadran(f: FacturaExtraida, ctx: Contexto) -> list[Problema]:
    if None in (f.subtotal, f.iva, f.total):
        return []
    sub, iva, total = _d(f.subtotal), _d(f.iva), _d(f.total)
    problemas = []
    if min(sub, iva, total) < 0:
        problemas.append(Problema("MONTO_NEGATIVO", "La factura tiene montos negativos."))
    if abs(sub + iva - total) > ctx.cfg.tolerancia:
        problemas.append(Problema(
            "TOTAL_NO_CUADRA",
            f"Los valores no cuadran: subtotal {sub:.2f} + IVA {iva:.2f} = {sub + iva:.2f}, "
            f"pero la factura indica un total de {total:.2f}."))
    if ctx.cfg.validar_tasa_iva:
        esperado = (sub * ctx.cfg.tasa_iva).quantize(Decimal("0.01"))
        if abs(esperado - iva) > ctx.cfg.tolerancia:
            problemas.append(Problema(
                "IVA_INCORRECTO",
                f"El IVA facturado ({iva:.2f}) no corresponde al {ctx.cfg.tasa_iva * 100:.0f}% "
                f"del subtotal ({esperado:.2f})."))
    return problemas


# --- Reglas contra el ERP -----------------------------------------------------------------
def proveedor_existe_activo(f: FacturaExtraida, ctx: Contexto) -> list[Problema]:
    ctx.proveedor = ctx.erp.proveedor(f.ruc_emisor)
    if ctx.proveedor is None:
        return [Problema("PROVEEDOR_NO_EXISTE",
                         f"El proveedor con RUC {f.ruc_emisor} no está registrado en el sistema.")]
    if not proveedor_activo(ctx.proveedor):
        nombre = ctx.proveedor.get("razon_social") or ctx.proveedor.get("nombre") or f.ruc_emisor
        return [Problema("PROVEEDOR_INACTIVO",
                         f"El proveedor {nombre} (RUC {f.ruc_emisor}) está inactivo en el sistema.")]
    return []


def orden_compra_existe(f: FacturaExtraida, ctx: Contexto) -> list[Problema]:
    ctx.orden = ctx.erp.orden_compra(f.orden_compra)
    if ctx.orden is None:
        return [Problema("OC_NO_EXISTE", f"La orden de compra {f.orden_compra} no existe en el sistema.")]
    ruc_oc = ruc_de_orden(ctx.orden)
    if ruc_oc and ruc_oc != f.ruc_emisor:
        return [Problema("OC_OTRO_PROVEEDOR",
                         f"La orden de compra {f.orden_compra} pertenece a otro proveedor (RUC {ruc_oc}), "
                         f"no al emisor de la factura (RUC {f.ruc_emisor}).")]
    if not orden_vigente(ctx.orden):
        return [Problema("OC_NO_VIGENTE",
                         f"La orden de compra {f.orden_compra} está en estado '{ctx.orden.get('estado')}' "
                         "y no admite facturación.")]
    return []


def montos_vs_orden(f: FacturaExtraida, ctx: Contexto) -> list[Problema]:
    """La API NO valida montos contra la OC, así que lo hacemos aquí."""
    if ctx.orden is None or None in (f.subtotal, f.iva, f.total):
        return []
    diferencias = []
    for campo, nombre in (("subtotal", "subtotal"), ("iva", "IVA"), ("total", "total")):
        en_factura, en_oc = _d(getattr(f, campo)), _d(ctx.orden.get(campo))
        if ctx.cfg.modo_monto_oc == "no_exceder":
            fuera = en_factura - en_oc > ctx.cfg.tolerancia
        else:
            fuera = abs(en_factura - en_oc) > ctx.cfg.tolerancia
        if fuera:
            diferencias.append(f"{nombre}: factura {en_factura:.2f} vs. orden {en_oc:.2f}")
    if not diferencias:
        return []
    verbo = "superan" if ctx.cfg.modo_monto_oc == "no_exceder" else "no coinciden con"
    return [Problema("MONTOS_DIFIEREN_OC",
                     f"Los montos de la factura {verbo} los de la orden de compra {f.orden_compra} "
                     f"({'; '.join(diferencias)}).")]


def no_duplicada(f: FacturaExtraida, ctx: Contexto) -> list[Problema]:
    if ctx.erp.factura_existente(f.numero_factura, f.ruc_emisor):
        return [Problema("DUPLICADA",
                         f"La factura {f.numero_factura} de este proveedor ya fue registrada anteriormente.")]
    return []


REGLAS_LOCALES: list[Regla] = [campos_obligatorios, formato_ruc, formato_numero, fecha_valida, montos_cuadran]
REGLAS_ERP: list[Regla] = [proveedor_existe_activo, orden_compra_existe, montos_vs_orden, no_duplicada]


def validar(f: FacturaExtraida, ctx: Contexto) -> list[Problema]:
    problemas: list[Problema] = []
    for regla in REGLAS_LOCALES:
        problemas += regla(f, ctx)
    # Solo se consulta el ERP si los datos clave son utilizables
    if ctx.erp and not any(p.codigo in {"CAMPOS_FALTANTES", "RUC_INVALIDO"} for p in problemas):
        for regla in REGLAS_ERP:
            problemas += regla(f, ctx)
    return problemas
