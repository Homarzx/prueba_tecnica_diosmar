"""Interfaz web para subir facturas y ver el proceso paso a paso.

Ejecutar:  py ui.py   y abrir http://localhost:8501
Solo usa la biblioteca estándar de Python (sin dependencias extra) y exactamente el mismo
flujo que la línea de comandos (main.procesar_con_historial).
"""
from __future__ import annotations

import json
import logging
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote

from facturas import config, notificador
from facturas.erp_client import ClienteERP
from facturas.extractor import crear_extractor
from main import HISTORIAL, procesar_con_historial

PUERTO = 8501
ENTRADA = Path("entrada")
PAGINA = Path(__file__).with_name("ui.html")

cfg = config.cargar()
extractor = crear_extractor(cfg.proveedor_ia, cfg.modelo_ia, cfg.modelos_respaldo)
erp = ClienteERP(cfg.erp_url, cfg.erp_token, cfg.erp_timeout)
log = logging.getLogger("facturas")
log.setLevel(logging.INFO)


class CapturaLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lineas: list[str] = []
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.lineas.append(self.format(record))


def procesar_archivo(nombre: str, contenido: bytes, modo_prueba: bool, forzar: bool) -> dict:
    ENTRADA.mkdir(exist_ok=True)
    nombre = re.sub(r"[^\w.\- ]", "_", Path(nombre).name) or "factura.pdf"
    pdf = ENTRADA / nombre
    pdf.write_bytes(contenido)

    captura = CapturaLog()
    log.addHandler(captura)
    try:
        res, estado = procesar_con_historial(pdf, cfg, extractor, erp, modo_prueba, forzar)
    except Exception as e:  # nunca romper la interfaz
        log.exception("Error inesperado")
        return {"archivo": nombre, "estado": "ERROR", "error_sistema": str(e), "log": captura.lineas}
    finally:
        log.removeHandler(captura)

    salida = {"archivo": nombre, "estado": estado, "log": captura.lineas, "modo_prueba": modo_prueba}
    if res is None:
        return salida
    salida.update(
        factura=res.factura.model_dump() if res.factura else None,
        problemas=[{"codigo": p.codigo, "mensaje": p.mensaje} for p in res.problemas],
        error_sistema=res.error_sistema,
        registrada=res.registrada,
        aviso_enviado=res.aviso_enviado,
    )
    if res.problemas or res.error_sistema:
        msg = notificador.armar_aviso(res, cfg)
        salida["aviso"] = {"asunto": msg["Subject"], "para": msg["To"], "cuerpo": msg.get_content()}
    return salida


class Manejador(BaseHTTPRequestHandler):
    def _json(self, datos, codigo=200):
        cuerpo = json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            cuerpo = PAGINA.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        elif self.path == "/api/config":
            self._json({"erp": cfg.erp_url, "ia": f"{cfg.proveedor_ia} / {cfg.modelo_ia}",
                        "destino": cfg.correo_destino, "smtp": f"{cfg.smtp_host}:{cfg.smtp_port}"})
        elif self.path == "/api/historial":
            datos = json.loads(HISTORIAL.read_text(encoding="utf-8")) if HISTORIAL.exists() else {}
            self._json(list(datos.values()))
        elif self.path == "/api/erp-facturas":
            try:
                r = erp.http.get("/facturas")
                r.raise_for_status()
                self._json(r.json())
            except Exception as e:
                self._json({"error": str(e)}, 502)
        else:
            self._json({"error": "no encontrado"}, 404)

    def do_POST(self):
        if self.path != "/api/procesar":
            return self._json({"error": "no encontrado"}, 404)
        largo = int(self.headers.get("Content-Length", 0))
        contenido = self.rfile.read(largo)
        if not contenido.startswith(b"%PDF"):
            return self._json({"archivo": "?", "estado": "ERROR", "error_sistema": "El archivo no es un PDF"}, 400)
        self._json(procesar_archivo(
            unquote(self.headers.get("X-Archivo", "factura.pdf")),
            contenido,
            modo_prueba=self.headers.get("X-Modo-Prueba", "1") == "1",
            forzar=self.headers.get("X-Forzar", "0") == "1",
        ))

    def log_message(self, *args):  # silenciar el log de cada petición HTTP
        pass


if __name__ == "__main__":
    for flujo in (sys.stdout, sys.stderr):
        flujo.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    servidor = HTTPServer(("127.0.0.1", PUERTO), Manejador)
    url = f"http://localhost:{PUERTO}"
    print(f"Interfaz disponible en {url}  (Ctrl+C para salir)")
    if "--no-abrir" not in sys.argv:
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
