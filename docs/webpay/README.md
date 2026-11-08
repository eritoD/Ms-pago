# Webpay Plus en ms-pago

Revisión: 8 de noviembre de 2026. El servicio de pagos escucha en **8005**; los clientes acceden por el gateway **8000**, conservando `/api/v1/payments/*`. `WEBPAY_PUBLIC_URL` debe señalar al gateway accesible desde el navegador, por ejemplo `http://localhost:8000`, y no a un nombre interno de Docker.

## DEV sin cobros

Variables del proceso de **ms-pago** (ejemplo; esta guía no modifica archivos `.env`):

```dotenv
WEBPAY_MOCK_MODE=true
WEBPAY_ENVIRONMENT=INTEGRATION
WEBPAY_PUBLIC_URL=http://localhost:8000
WEBPAY_FRONTEND_RETURN_URL=sportmatchapp://pago
WEBPAY_WEB_RETURN_URL=http://localhost:3001/pago/resultado
WEBPAY_TIMEOUT_SECONDS=15
```

El mock no necesita código de comercio ni API key y no hace peticiones a Transbank. `get_webpay_settings()` y `build_webpay()` rechazan `WEBPAY_MOCK_MODE=true` junto con `WEBPAY_ENVIRONMENT=PRODUCTION`, incluso al construir el adaptador con un objeto de configuración inyectado.

Para probar desde la app o web, inicia sesión y elige un plan pagado del catálogo. La API toma comprador y monto del servidor:

1. Envía `POST /api/v1/payments/webpay/initiate` con `{"plan_id": 1}`; para la web añade `"client": "web"`. El ID debe existir en el catálogo. La respuesta indica `environment: "MOCK"`.
2. Abre `checkout_url`. El ticket firmado dura 15 minutos y genera el formulario POST con `token_ws`, apuntando a `/api/v1/payments/webpay/mock` a través del gateway.
3. La página indica que no hay cobro y permite **Pagar (simular pago aprobado)**, **Simular pago rechazado** y **Anular compra y volver**. La primera decisión queda fija; repetirla devuelve el mismo retorno. Elegir otro resultado requiere iniciar otro intento.
4. El navegador regresa a `/api/v1/payments/webpay/return` y después al destino móvil o web configurado por el servidor. El callback admite GET y POST y confirma sin exigir que siga vigente el login del navegador.
5. El cliente recupera el comprobante con `POST /api/v1/payments/webpay/commit` autenticado. Aprobación: HTTP 200 y `APPROVED`; rechazo/cancelación: HTTP 400 con `transaccion.status` igual a `REJECTED`/`CANCELLED`. Repetir la confirmación conserva el comprobante y no extiende dos veces la suscripción. Rechazar o cancelar conserva cualquier suscripción previa.

El mock guarda sus formularios y decisiones en memoria: reiniciarlo pierde los intentos pendientes del simulador. Úsalo en una sola instancia de desarrollo. Los tickets de checkout y tokens identifican intentos; evita compartirlos. En un teléfono, usa una IP LAN o dominio accesible para `WEBPAY_PUBLIC_URL`: `localhost` apunta al teléfono. Configura el deep link correspondiente a la compilación instalada o a Expo Go.

## Sandbox oficial de Transbank

Para recorrer el formulario de Transbank sin usar producción, configura `WEBPAY_MOCK_MODE=false` y conserva `WEBPAY_ENVIRONMENT=INTEGRATION`. La configuración usa las credenciales **públicas de integración** si `WEBPAY_COMMERCE_CODE` y `WEBPAY_API_KEY` están vacías. No introduzcas credenciales productivas para estas pruebas.

La [guía oficial de ambientes y tarjetas](https://github.com/TransbankDevelopers/transbank-developers-docs/blob/master/documentacion/como_empezar/README.md) publica estos datos de prueba:

| Resultado | Tarjeta de integración | CVV |
| --- | --- | --- |
| Aprobado, VISA | `4051 8856 0044 6623` | `123` |
| Rechazado, Mastercard | `5186 0595 5959 0568` | `123` |

Usa una fecha futura y, si aparece autenticación bancaria, RUT `11.111.111-1` y clave `123`. Para cancelación, selecciona anular compra. El código público Webpay Plus es `597055555532`; la API key pública está publicada en la misma guía y ya es el valor predeterminado de integración.

## Contrato del adaptador

Se conservan `WebpayGateway`, `WebpayCommit`, `WebpayError`, `MockWebpay`, `build_webpay`, `sign_checkout` y `read_checkout`. El adaptador HTTP usa el host de integración `https://webpay3gint.transbank.cl` o producción `https://webpay3g.transbank.cl`, timeout finito configurable, y autenticación por headers del servidor. Crea con POST, confirma con PUT y consulta estado con GET en `/rswebpaytransaction/api/webpay/v1.2/transactions`, según la [referencia REST oficial](https://github.com/TransbankDevelopers/transbank-developers-docs/blob/master/referencia/webpay/README.md).

El adaptador valida formatos de token, orden y sesión, monto positivo entero al crear y monto CLP íntegro en respuestas. Solo admite destinos de checkout del host Transbank configurado bajo `/webpayserver/`; no sigue redirecciones HTTP. Los errores públicos omiten tokens y credenciales.

La [documentación oficial de flujos](https://github.com/TransbankDevelopers/transbank-developers-docs/blob/master/documentacion/webpay-plus/README.md) distingue retorno normal con `token_ws`, cancelación con `TBK_TOKEN`, `TBK_ORDEN_COMPRA`, `TBK_ID_SESION`, y timeout con orden/sesión sin token. Solo `response_code=0` junto con `status=AUTHORIZED` permite aprobar. El servicio además exige que monto, orden y sesión coincidan con la transacción almacenada. Ante respuesta de PUT perdida consulta estado; ante error de transporte sin resultado definitivo mantiene `PENDING` para reintentar.

## Validación reproducible

Desde `ms-pago`, con sus dependencias de desarrollo instaladas:

```powershell
python -m pytest tests/test_webpay_gateway.py tests/test_webpay_integration_flow.py
```

Las pruebas del adaptador usan `httpx.MockTransport`; las de API usan `TestClient`, un repositorio financiero en memoria y directorios externos que fallan si se consultan. Cubren HTTP, errores/timeouts, retorno GET/POST, firma y expiración del checkout, idempotencia, cancelación, pérdida de respuesta, decisiones mock estables y validación de referencias. No necesitan PostgreSQL, Docker, credenciales privadas ni conexión a Transbank. No equivalen a una prueba de despliegue gateway/DB ni a certificación productiva.

Fuentes oficiales verificadas el 8 de noviembre de 2026 en el repositorio `TransbankDevelopers/transbank-developers-docs`; el portal `www.transbankdevelopers.cl` respondió 403 durante esta revisión. No se realizaron cobros ni peticiones transaccionales externas.
