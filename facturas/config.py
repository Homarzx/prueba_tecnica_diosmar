"""Configuración leída desde variables de entorno (.env). Nada sensible vive en el código."""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()


def _req(nombre: str) -> str:
    valor = os.getenv(nombre)
    if not valor:
        raise RuntimeError(f"Falta la variable de entorno {nombre} (revise su archivo .env)")
    return valor


def _bool(nombre: str, defecto: bool) -> bool:
    valor = os.getenv(nombre)
    if valor is None or valor == "":
        return defecto
    return valor.strip().lower() in {"1", "true", "si", "sí", "yes"}


@dataclass(frozen=True)
class Settings:
    # ERP
    erp_url: str
    erp_token: str
    erp_timeout: float
    # IA
    proveedor_ia: str
    modelo_ia: str
    modelos_respaldo: list[str]
    # Correo
    smtp_host: str
    smtp_port: int
    smtp_starttls: bool
    smtp_user: str | None
    smtp_password: str | None
    correo_remitente: str
    correo_destino: str
    # Reglas de negocio
    tasa_iva: Decimal
    validar_tasa_iva: bool
    tolerancia: Decimal
    modo_monto_oc: str  # "igual": la factura debe coincidir con la OC | "no_exceder": puede ser menor (parcial)


MODELOS_POR_DEFECTO = {"gemini": "gemini-3.8-flash", "claude": "claude-opus-5-5"}


def cargar() -> Settings:
    smtp_user = os.getenv("SMTP_USER") or None
    proveedor = os.getenv("PROVEEDOR_IA", "claude").strip().lower()
    return Settings(
        erp_url=_req("ERP_URL").rstrip("/"),
        erp_token=_req("ERP_TOKEN"),
        erp_timeout=float(os.getenv("ERP_TIMEOUT", "15")),
        proveedor_ia=proveedor,
        modelo_ia=os.getenv("MODELO_IA") or MODELOS_POR_DEFECTO.get(proveedor, ""),
        modelos_respaldo=[m.strip() for m in os.getenv("MODELOS_RESPALDO", "").split(",") if m.strip()],
        smtp_host=_req("SMTP_HOST"),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_starttls=_bool("SMTP_STARTTLS", True),
        smtp_user=smtp_user,
        smtp_password=os.getenv("SMTP_PASSWORD") or None,
        correo_remitente=os.getenv("CORREO_REMITENTE") or smtp_user or "avisos@prueba.local",
        correo_destino=_req("CORREO_DESTINO"),
        tasa_iva=Decimal(os.getenv("TASA_IVA", "0.15")),
        validar_tasa_iva=_bool("VALIDAR_TASA_IVA", True),
        tolerancia=Decimal(os.getenv("TOLERANCIA", "0.01")),
        modo_monto_oc=os.getenv("MODO_MONTO_OC", "igual").strip().lower(),
    )
