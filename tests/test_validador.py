"""Pruebas de las reglas de validación con un ERP simulado (no requiere red)."""
from decimal import Decimal

from facturas.config import Settings
from facturas.modelos import FacturaExtraida
from facturas.validador import Contexto, validar

CFG = Settings(
    erp_url="http://x", erp_token="x", erp_timeout=1, proveedor_ia="gemini", modelo_ia="x", modelos_respaldo=[],
    smtp_host="x", smtp_port=1, smtp_starttls=False, smtp_user=None, smtp_password=None,
    correo_remitente="a@b", correo_destino="a@b",
    tasa_iva=Decimal("0.15"), validar_tasa_iva=True, tolerancia=Decimal("0.01"), modo_monto_oc="igual",
)


class ERPFalso:
    def __init__(self, proveedores=None, ordenes=None, facturas=()):
        self.proveedores = proveedores or {}
        self.ordenes = ordenes or {}
        self.facturas = set(facturas)
    def proveedor(self, ruc):
        return self.proveedores.get(ruc)
    def orden_compra(self, numero):
        return self.ordenes.get(numero)
    def factura_existente(self, numero, ruc):
        return (numero, ruc) in self.facturas


RUC = "0991234567001"


def factura(**cambios):
    base = dict(ruc_emisor=RUC, numero_factura="001-001-000004521", fecha_emision="2026-10-01",
                orden_compra="OC-100", subtotal=100.0, iva=15.0, total=115.0)
    base.update(cambios)
    return FacturaExtraida(**base)


def oc(**cambios):
    base = dict(numero="OC-100", ruc_proveedor=RUC, fecha="2026-09-15",
                subtotal=100.0, iva=15.0, total=115.0, estado="APROBADA")
    base.update(cambios)
    return base


def erp_ok(**kw):
    defaults = dict(proveedores={RUC: {"ruc": RUC, "razon_social": "Prov S.A.", "estado": "ACTIVO"}},
                    ordenes={"OC-100": oc()})
    defaults.update(kw)
    return ERPFalso(**defaults)


def codigos(f, erp):
    return [p.codigo for p in validar(f, Contexto(cfg=CFG, erp=erp))]


def test_factura_correcta():
    assert codigos(factura(), erp_ok()) == []


def test_total_no_cuadra():
    assert "TOTAL_NO_CUADRA" in codigos(factura(total=120.0), erp_ok())


def test_iva_incorrecto():
    assert "IVA_INCORRECTO" in codigos(factura(iva=12.0, total=112.0), erp_ok())


def test_campo_faltante_no_consulta_erp():
    assert codigos(factura(orden_compra=None), None) == ["CAMPOS_FALTANTES"]


def test_proveedor_inexistente():
    assert "PROVEEDOR_NO_EXISTE" in codigos(factura(), erp_ok(proveedores={}))


def test_proveedor_inactivo():
    assert "PROVEEDOR_INACTIVO" in codigos(factura(), erp_ok(proveedores={RUC: {"estado": "INACTIVO"}}))


def test_oc_inexistente():
    assert "OC_NO_EXISTE" in codigos(factura(), erp_ok(ordenes={}))


def test_oc_de_otro_proveedor():
    assert "OC_OTRO_PROVEEDOR" in codigos(factura(), erp_ok(ordenes={"OC-100": oc(ruc_proveedor="1790000000001")}))


def test_duplicada():
    assert "DUPLICADA" in codigos(factura(), erp_ok(facturas=[("001-001-000004521", RUC)]))


def test_formato_numero():
    assert "NUMERO_INVALIDO" in codigos(factura(numero_factura="4521"), erp_ok())


def test_montos_difieren_de_oc():
    assert "MONTOS_DIFIEREN_OC" in codigos(factura(subtotal=200.0, iva=30.0, total=230.0), erp_ok())


def test_oc_no_vigente():
    assert "OC_NO_VIGENTE" in codigos(factura(), erp_ok(ordenes={"OC-100": oc(estado="ANULADA")}))
