"""Recepción automatizada de facturas de proveedores.

Uso:
    py main.py entrada/                 # procesa todos los PDF de la carpeta
    py main.py entrada/F1.pdf entrada/F2.pdf
    py main.py entrada/ --dry-run       # extrae y valida, pero NO registra ni envía correos
    py main.py entrada/ --forzar        # reprocesa aunque ya figuren en el historial
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from facturas import config, notificador
from facturas.erp_client import ClienteERP, ErrorERP, FacturaYaRegistrada
from facturas.extractor import ErrorExtraccion, Extractor, crear_extractor
from facturas.modelos import Problema, Resultado
from facturas.validador import Contexto, validar

HISTORIAL = Path("historial.json")
log = logging.getLogger("facturas")


def _hash(pdf: Path) -> str:
    return hashlib.sha256(pdf.read_bytes()).hexdigest()


def _leer_historial() -> dict:
    if HISTORIAL.exists():
        return json.loads(HISTORIAL.read_text(encoding="utf-8"))
    return {}


def _guardar_historial(h: dict) -> None:
    HISTORIAL.write_text(json.dumps(h, indent=2, ensure_ascii=False), encoding="utf-8")


def procesar(pdf: Path, cfg, extractor: Extractor, erp: ClienteERP, dry_run: bool) -> Resultado:
    res = Resultado(archivo=pdf.name)
    try:
        res.factura = extractor.extraer(pdf)
        log.info("  Datos extraídos: %s", res.factura.model_dump())

        res.problemas = validar(res.factura, Contexto(cfg=cfg, erp=erp))
        if res.problemas:
            for p in res.problemas:
                log.warning("  [%s] %s", p.codigo, p.mensaje)
        elif dry_run:
            log.info("  OK (dry-run: no se registra)")
        else:
            respuesta = erp.registrar_factura(res.factura)
            res.registrada = True
            log.info("  REGISTRADA en el ERP: %s", respuesta)
    except FacturaYaRegistrada:
        res.problemas.append(Problema(
            "DUPLICADA", f"La factura {res.factura.numero_factura} ya estaba registrada en el sistema."))
        log.warning("  [DUPLICADA] el ERP respondió 409")
    except (ErrorERP, ErrorExtraccion) as e:
        res.error_sistema = str(e)
        log.error("  Error: %s", e)
    except Exception as e:  # cualquier fallo inesperado no debe detener el lote
        res.error_sistema = f"Error inesperado al procesar el archivo: {e}"
        log.exception("  Error inesperado")

    if not res.registrada and (res.problemas or res.error_sistema):
        msg = notificador.armar_aviso(res, cfg)
        if dry_run:
            log.info("  (dry-run) Aviso que se enviaría:\n%s", msg.get_content())
        else:
            try:
                notificador.enviar(msg, cfg)
                res.aviso_enviado = True
                log.info("  Aviso enviado a %s", cfg.correo_destino)
            except Exception as e:
                log.error("  No se pudo enviar el aviso: %s", e)
    return res


def procesar_con_historial(pdf: Path, cfg, extractor: Extractor, erp: ClienteERP,
                           dry_run: bool, forzar: bool) -> tuple[Resultado | None, str]:
    """Procesa un PDF respetando el historial (no repite registros ni avisos). Usado por la CLI y la interfaz web."""
    log.info("Procesando %s", pdf.name)
    historial = _leer_historial()
    clave = _hash(pdf)
    previo = historial.get(clave)
    if previo and not forzar and (previo["registrada"] or previo["aviso_enviado"]):
        log.info("  Ya procesada el %s (use --forzar para repetir)", previo["fecha"])
        return None, "OMITIDA (ya procesada)"

    res = procesar(pdf, cfg, extractor, erp, dry_run)
    estado = "REGISTRADA" if res.registrada else ("RECHAZADA" if res.problemas else "ERROR")
    if dry_run and estado == "RECHAZADA":
        estado = "RECHAZARÍA"
    elif dry_run and not res.problemas and not res.error_sistema:
        estado = "VÁLIDA (sin registrar)"

    if not dry_run:
        historial[clave] = {
            "archivo": pdf.name,
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "numero_factura": res.factura.numero_factura if res.factura else None,
            "registrada": res.registrada,
            "aviso_enviado": res.aviso_enviado,
            "motivos": [p.codigo for p in res.problemas] or ([res.error_sistema] if res.error_sistema else []),
        }
        _guardar_historial(historial)
    return res, estado


def main() -> int:
    ap = argparse.ArgumentParser(description="Procesa facturas PDF de proveedores")
    ap.add_argument("rutas", nargs="+", type=Path, help="Archivos PDF o carpetas")
    ap.add_argument("--dry-run", action="store_true", help="No registra ni envía correos")
    ap.add_argument("--forzar", action="store_true", help="Reprocesa facturas ya procesadas")
    args = ap.parse_args()

    for flujo in (sys.stdout, sys.stderr):  # acentos correctos en la consola de Windows
        flujo.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = config.cargar()
    extractor = crear_extractor(cfg.proveedor_ia, cfg.modelo_ia, cfg.modelos_respaldo)
    log.info("IA: %s / %s (respaldo: %s)", cfg.proveedor_ia, cfg.modelo_ia, ", ".join(cfg.modelos_respaldo) or "ninguno")
    erp = ClienteERP(cfg.erp_url, cfg.erp_token, cfg.erp_timeout)

    pdfs: list[Path] = []
    for r in args.rutas:
        pdfs += sorted(p for p in r.iterdir() if p.suffix.lower() == ".pdf") if r.is_dir() else [r]
    if not pdfs:
        log.error("No se encontraron PDFs")
        return 1

    resumen = []
    for pdf in pdfs:
        res, estado = procesar_con_historial(pdf, cfg, extractor, erp, args.dry_run, args.forzar)
        resumen.append((pdf.name, estado))

    print("\nResumen:")
    for nombre, estado in resumen:
        print(f"  {nombre:<30} {estado}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
