"""Envío de avisos por correo (SMTP). Funciona con Gmail (587 + STARTTLS) o con el buzón de prueba (sin TLS ni usuario)."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import Settings
from .modelos import Resultado


def armar_aviso(res: Resultado, cfg: Settings) -> EmailMessage:
    f = res.factura
    numero = (f.numero_factura if f else None) or "(no identificado)"
    ruc = (f.ruc_emisor if f else None) or "(no identificado)"

    if res.problemas:
        motivos = "\n".join(f"  - {p.mensaje}" for p in res.problemas)
    else:
        motivos = f"  - {res.error_sistema}"

    msg = EmailMessage()
    msg["Subject"] = f"Factura {numero} NO registrada - RUC {ruc}"
    msg["From"] = cfg.correo_remitente
    msg["To"] = cfg.correo_destino
    msg.set_content(
        "Estimado equipo de Compras:\n\n"
        "La siguiente factura no fue registrada en el sistema.\n\n"
        f"  Número de factura: {numero}\n"
        f"  RUC del emisor:    {ruc}\n"
        f"  Orden de compra:   {(f.orden_compra if f else None) or '-'}\n"
        f"  Total:             {(f'{f.total:.2f}' if f and f.total is not None else '-')}\n"
        f"  Archivo:           {res.archivo}\n\n"
        f"Motivo(s):\n{motivos}\n\n"
        "Por favor revise la información con el proveedor y, de ser necesario, solicite una factura corregida.\n\n"
        "Este es un mensaje automático."
    )
    return msg


def enviar(msg: EmailMessage, cfg: Settings) -> None:
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
        if cfg.smtp_starttls:
            smtp.starttls()
        if cfg.smtp_user and cfg.smtp_password:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.send_message(msg)
