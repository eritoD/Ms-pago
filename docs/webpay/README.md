# Webpay Plus en ms-pago

Revisión: 8 de noviembre de 2026. El servicio de pagos escucha en **8005**; los clientes acceden por el gateway **8000**, conservando `/api/v1/payments/*`. `WEBPAY_PUBLIC_URL` debe señalar al gateway accesible desde el navegador, por ejemplo `http://localhost:8000`, y no a un nombre interno de Docker.

## Probar ahora con Transbank en integración

La configuración local de `Ms_gateway/.env` (Docker) y `ms-pago/.env` (ejecución local) usa el ambiente oficial de integración. No realiza cobros bancarios reales. Los `.env.example` contienen las mismas variables de Webpay y las credenciales públicas de ese ambiente:

```dotenv
WEBPAY_MOCK_MODE=false
WEBPAY_ENVIRONMENT=INTEGRATION
WEBPAY_COMMERCE_CODE=597055555532
WEBPAY_API_KEY=579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C
WEBPAY_PUBLIC_URL=http://localhost:8000
WEBPAY_FRONTEND_RETURN_URL=http://localhost:8081/pago
WEBPAY_WEB_RETURN_URL=http://localhost:3001/pago/resultado
WEBPAY_TIMEOUT_SECONDS=15
```

Desde la raíz del workspace, levanta Docker Desktop y ejecuta:

```powershell
cd Ms_gateway
docker compose -f compose.yaml -f compose.windows.yaml up --build -d
docker compose -f compose.yaml -f compose.windows.yaml ps
```

El gateway y `ms_pago` deben quedar `healthy`. En otras dos terminales, desde la raíz, inicia los clientes si aún no están corriendo:

```powershell
cd Frontend-SportMatch-APP
npm run web -- --port 8081
```

```powershell
cd sportmatch-web
npm run dev -- --port 3001
```

### Deportista en la app web

1. Abre `http://localhost:8081` e inicia sesión con `deportista.prueba@sportmatch.cl`, contraseña `PruebaSport2026!`.
2. Entra a **Perfil → Planes y beneficios**. Debajo de la tabla comparativa está **Contratar plan Pro · $3.990 / mes**. El precio procede del catálogo; puede cambiar si lo edita el administrador.
3. En el resumen toca **Pagar con Webpay**. En Expo web se navega en la misma pestaña para evitar el bloqueo de ventanas emergentes. En Android/iOS se conserva el navegador nativo.
4. Debe aparecer el dominio `webpay3gint.transbank.cl`, el monto y la opción **Tarjetas**. El comercio de prueba compartido se muestra como **WEBPAY REST SIMULTANEA**.
5. Completa la tarjeta y la autenticación bancaria con los datos de la tabla siguiente. Al aprobar, el retorno pasa por `http://localhost:8000/api/v1/payments/webpay/return` y llega a `http://localhost:8081/pago`.
6. Verifica **Pago aprobado**, plan Pro activo y el comprobante en **Mis pagos**. Si cerraste el formulario, el resumen permite **Continuar el pago pendiente en Webpay** o **Consultar pago**.

### Club en la web

1. Abre `http://localhost:3001` e inicia sesión con `club.prueba@sportmatch.cl`, contraseña `PruebaSport2026!`.
2. Ve a **Mi suscripción**, `http://localhost:3001/suscripcion`. El catálogo contiene **Demo Club Mensual**, $14.990 por 30 días.
3. Toca **Contratar con Webpay**. Esta cuenta ya tenía una suscripción activa antes de esta revisión, por lo que muestra **Renovar con Webpay**; una aprobación agrega 30 días a su vencimiento.
4. Completa el formulario oficial. El backend recibe el retorno con `?client=web` y redirige a `http://localhost:3001/pago/resultado`.
5. Regresa a **Mi suscripción** y revisa la vigencia, el historial, el período comprado y **Descargar comprobante**.

### Panel administrador y resultado esperado

Inicia sesión en la web con `admin.prueba@sportmatch.cl`, contraseña `PruebaSport2026!`. Abre **Pagos**, la sección de pagos de la plataforma, en `http://localhost:3001/pagos`. Busca la misma orden que muestra el historial del comprador. Un pago aprobado debe indicar **Aprobado**, monto, autorización y período de suscripción. Repetir el retorno o recargar el comprobante no debe extender otra vez la vigencia.

Para probar un rechazo usa Mastercard; para una anulación toca **Anular compra y volver** en Transbank. Ambos casos conservan la suscripción anterior y dejan su intento en el historial y en el panel admin. Un timeout retorna orden/sesión sin token; el backend consulta su estado sin confirmar el pago.

Los valores de `localhost` corresponden a este PC. Para un teléfono físico configura una dirección accesible desde el teléfono en `WEBPAY_PUBLIC_URL` y el deep link de la app instalada en `WEBPAY_FRONTEND_RETURN_URL`; reconstruye `ms_pago` después de modificar el `.env` del gateway.

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
| Aprobado, AMEX | `3700 0000 0002 032` | `1234` |
| Aprobado, débito Redcompra | `4051 8842 3993 7763` | No aplica |
| Rechazado, Mastercard | `5186 0595 5959 0568` | `123` |

Usa una fecha futura y, si aparece autenticación bancaria, RUT `11.111.111-1` y clave `123`. Para cancelación, selecciona anular compra. El código público Webpay Plus es `597055555532`; la API key pública está publicada en la misma guía y ya es el valor predeterminado de integración.

## Contrato del adaptador

Se conservan `WebpayGateway`, `WebpayCommit`, `WebpayError`, `MockWebpay`, `build_webpay`, `sign_checkout` y `read_checkout`. El adaptador HTTP usa el host de integración `https://webpay3gint.transbank.cl` o producción `https://webpay3g.transbank.cl`, timeout finito configurable, y autenticación por headers del servidor. Crea con POST, confirma con PUT y consulta estado con GET en `/rswebpaytransaction/api/webpay/v1.2/transactions`, según la [referencia REST oficial](https://github.com/TransbankDevelopers/transbank-developers-docs/blob/master/referencia/webpay/README.md).

El adaptador valida formatos de token, orden y sesión, monto positivo entero al crear y monto CLP íntegro en respuestas. Solo admite destinos de checkout del host Transbank configurado bajo `/webpayserver/`; no sigue redirecciones HTTP. Los errores públicos omiten tokens y credenciales.

La [documentación oficial de flujos](https://github.com/TransbankDevelopers/transbank-developers-docs/blob/master/documentacion/webpay-plus/README.md) distingue retorno normal con `token_ws`, cancelación con `TBK_TOKEN`, `TBK_ORDEN_COMPRA`, `TBK_ID_SESION`, y timeout con orden/sesión sin token. Solo `response_code=0` junto con `status=AUTHORIZED` permite aprobar. El servicio además exige que monto, orden y sesión coincidan con la transacción almacenada. Ante respuesta de PUT perdida consulta estado; ante error de transporte sin resultado definitivo mantiene `PENDING` para reintentar.

Las anulaciones y timeouts también consultan estado por GET, sin emitir PUT. Una autorización ya existente se recupera y activa la suscripción; `INITIALIZED`, `FAILED` o `REVERSED` permiten cerrar el intento como anulado. Una consulta fallida, referencias incompatibles o un estado desconocido conservan `PENDING`. El mock implementa la misma consulta para intentos inicializados y sigue disponible mediante `WEBPAY_MOCK_MODE=true`.

Las órdenes generadas tienen 22 caracteres, dentro del máximo 26; la sesión contiene tipo de comprador y UUID y queda bajo el máximo 61. El ticket del puente dura 15 minutos, pero no alarga la vigencia del token de Transbank: inicia el formulario dentro de los 5 minutos siguientes a crear el intento. Si vence, inicia otro pago. El timeout del formulario de integración puede devolver el control después de 10 minutos.

## Validación reproducible

Desde `ms-pago`, con sus dependencias de desarrollo instaladas:

```powershell
python -m pytest tests/test_webpay_gateway.py tests/test_webpay_integration_flow.py
```

Las pruebas del adaptador usan `httpx.MockTransport`; las de API usan `TestClient`, un repositorio financiero en memoria y directorios externos que fallan si se consultan. Cubren HTTP, errores/timeouts, retorno GET/POST, firma y expiración del checkout, idempotencia, cancelación, pérdida de respuesta, decisiones mock estables y validación de referencias. No necesitan PostgreSQL, Docker, credenciales privadas ni conexión a Transbank. No equivalen a una prueba de despliegue gateway/DB ni a certificación productiva.

## Diagnóstico y prueba de despliegue del 8 de noviembre de 2026

- Al iniciar esta tarea Docker Desktop estaba detenido y los frontends no escuchaban en 8081/3001. Se iniciaron Docker y ambos clientes. Los `.env` del gateway y ms-pago seguían con `WEBPAY_MOCK_MODE=true`; se cambiaron a integración oficial y se reconstruyó el backend.
- El catálogo **no estaba vacío**: PostgreSQL `sportmatch_payments` y `GET /api/v1/payments/plans` devolvieron dos planes activos: ID 1, **Demo Deportista Mensual**, $3.990; ID 2, **Demo Club Mensual**, $14.990. No se sembraron ni cambiaron planes.
- En Expo web el pago se abría con `window.open` después de esperar la API y guardar el pendiente. El navegador podía bloquearlo sin que Expo informara el error. `ContratarPlanModal` ahora navega en la misma pestaña. Se comprobó por navegador una única pestaña desde Perfil → Planes → contratar Pro → Pagar con Webpay hasta el formulario oficial.
- La creación real a través del gateway respondió **201**, `environment=INTEGRATION` y URL oficial. Una consulta REST directa a Transbank respondió **200**, `INITIALIZED`, con monto, orden y sesión coincidentes.
- Se recorrió también Mi suscripción → Renovar con Webpay en la web del club hasta el formulario oficial por $14.990.
- Se anuló desde el propio formulario de Transbank en ambos clientes. Los retornos llegaron a `/pago` (app) y `/pago/resultado` (web), mostraron cancelación y registraron **CANCELLED** en PostgreSQL, historial y panel admin. Órdenes verificadas: `SM58226682a239c499f6a9` (deportista) y `SM8120e57e24dced26e1d4` (club). Repetir `/webpay/commit` conservó el estado sin cambiar la suscripción. El panel web `/pagos` mostró ambas filas como **Anulado**.
- Pasaron **411 pruebas de ms-pago**, incluidas las de PostgreSQL habilitadas con `PAYMENTS_TEST_DATABASE_URL` sobre la base separada `sportmatch_payments_webpay_test`. Las pruebas crean y eliminan sus esquemas aislados. Incluyen activación, historial, panel admin, atomicidad, renovaciones concurrentes, idempotencia y los nuevos casos de consulta en retornos abandonados. También pasaron 10 pruebas de pagos y 7 de catálogo de la app, `tsc --noEmit` y 66 pruebas de la web.

**Alcance de la prueba real:** se validaron creación, consulta de estado, apertura del formulario y anulación con Transbank. No se ingresaron tarjetas ni se autorizó un pago: ese paso corresponde al usuario en el navegador. La activación tras una aprobación y su presencia en historial/admin se verificaron con pruebas automatizadas y PostgreSQL aislado; falta completar esa comprobación con una autorización del ambiente oficial. Los intentos iniciados solo para abrir/consultar pueden permanecer pendientes y no activan beneficios.

Fuentes oficiales verificadas el 8 de noviembre de 2026 en el repositorio `TransbankDevelopers/transbank-developers-docs`; el portal `www.transbankdevelopers.cl` respondió 403 durante esta revisión. No se realizaron cobros productivos. No se hicieron commits ni push.
