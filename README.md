# Recepción automatizada de facturas de proveedores

Flujo que recibe facturas en PDF, extrae sus datos con IA, los valida contra el ERP y:

- **si todo es correcto**, registra la factura en el ERP;
- **si hay un problema**, no la registra y envía un aviso por correo a Compras explicando el motivo.

## Flujo

```
PDF ──► extractor (IA) ────► validador ──┬─► OK ───────► ERP: POST factura
                                           └─► problemas ─► correo a Compras
```

1. **Extracción** (`facturas/extractor.py`): el PDF se envía completo al modelo de IA (Gemini por defecto, o Claude, según `PROVEEDOR_IA`) como documento, así que también funciona con PDFs escaneados. La respuesta usa *salida estructurada*: el modelo debe devolver un JSON que cumpla el esquema `FacturaExtraida`. Si un dato no está en la factura, debe venir `null` y no inventarse. Luego hay una normalización determinística (solo dígitos en el RUC, sin espacios en el número).
2. **Validación** (`facturas/validador.py`): es una lista de reglas independientes.
   - Locales: campos obligatorios, formato del RUC, formato del número `001-001-000000123`, fecha válida y no futura, subtotal + IVA = total, IVA igual a la tasa configurada.
   - Contra el ERP: el proveedor existe y está activo, la orden de compra existe, está vigente y pertenece a ese proveedor, los montos coinciden con la OC (la API no lo valida), la factura no está ya registrada.
3. **Registro** (`facturas/erp_client.py`): solo si no hubo ningún problema. Un 409 del ERP se trata como factura duplicada.
4. **Aviso** (`facturas/notificador.py`): un correo por factura, con el número de factura y el RUC en el asunto, y los motivos redactados para Compras.
5. **Idempotencia** (`main.py`): `historial.json` guarda el hash de cada PDF ya procesado. Si el script se vuelve a ejecutar, no registra dos veces ni repite avisos (`--forzar` lo omite).

Los errores técnicos (ERP caído, timeout, fallo de la IA) se distinguen de las facturas inválidas. Esa factura no se registra, se envía un aviso con el motivo técnico y el lote sigue con las demás.

## Instalación

```bash
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # y completar ERP_TOKEN, GEMINI_API_KEY, SMTP_USER, SMTP_PASSWORD
```

Las credenciales (token del ERP, API key de la IA y contraseña de aplicación de Gmail) viven solo en `.env`, que está en `.gitignore`. Nada sensible está en el código.

> La contraseña de aplicación de Gmail requiere tener activada la verificación en 2 pasos: https://myaccount.google.com/apppasswords

## Ejecución

```bash
py main.py entrada/              # todos los PDF de la carpeta
py main.py entrada/F1.pdf        # un archivo
py main.py entrada/ --dry-run    # extrae y valida; NO registra ni envía (muestra el correo que enviaría)
py -m pytest                     # pruebas de las reglas con un ERP simulado
```

Para usar el buzón de prueba, cambie el bloque de correo en `.env` (las variables comentadas). No hace falta tocar el código.

## Cómo extenderlo

- **Nueva validación**: escriba una función `regla(factura, ctx) -> list[Problema]` en `validador.py` y agréguela a `REGLAS_LOCALES` o `REGLAS_ERP`.
- **Nuevo campo a extraer**: agréguelo a `FacturaExtraida` en `modelos.py` (con su descripción) y a `INSTRUCCIONES` en `extractor.py`.
- **Cambios en la API del ERP**: las rutas y los nombres de campos están concentrados al inicio y al final de `erp_client.py`.

## Proveedor de IA

Por defecto se usa **Google Gemini** en su plan gratuito: la key se obtiene sin tarjeta en https://aistudio.google.com/apikey. Para usar Claude, ponga `PROVEEDOR_IA=claude` y `ANTHROPIC_API_KEY` en `.env`. El código no cambia.

Para ver qué modelos de Gemini tiene disponibles su key:

```bash
py -c "from dotenv import load_dotenv; load_dotenv(); from google import genai; [print(m.name) for m in genai.Client().models.list() if 'flash' in m.name]"
```

## Interfaz gráfica

```bash
.venv\Scripts\python.exe ui.py
```

Se abre en http://localhost:8501. Permite arrastrar uno o varios PDF y ver, para cada factura:

- los datos que extrajo la IA (en rojo los que no encontró);
- el resultado de cada validación, con el motivo tal como lo leerá Compras;
- el correo de aviso que se envía (o se enviaría, en modo prueba);
- el registro técnico del proceso.

El interruptor **Modo prueba** viene activado: extrae y valida, pero no registra ni envía correos. Desactívelo para la corrida real. La pestaña **Historial** muestra lo procesado y las facturas registradas en el ERP.

La interfaz (`ui.py` + `ui.html`) solo usa la biblioteca estándar de Python y llama al mismo flujo que la línea de comandos (`procesar_con_historial` en `main.py`), así que no duplica lógica.
