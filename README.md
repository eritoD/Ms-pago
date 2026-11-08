# ms-pago

Microservicio FastAPI independiente para catálogo de planes y convenios, administración de planes, pagos Webpay y suscripciones. Conserva `/api/v1/payments` y los cuerpos de solicitud/respuesta de Ms-club para app y web. Escucha en el puerto **8005**.

## Ejecución

Requiere Python 3.11+ y PostgreSQL con una base propia `sportmatch_payments`. El administrador debe crear la base y el rol exclusivos antes de aplicar el esquema. Este servicio no necesita permisos sobre las bases de clubes ni usuarios.

```powershell
cd ms-pago
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
# Exportar las variables documentadas en .env.example en el entorno del proceso.
# La aplicación no carga .env automáticamente.
.venv\Scripts\python -m app.database.migrate
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8005
.venv\Scripts\python -m pytest
```

`Dockerfile` incluye instalación del paquete y healthcheck. Las migraciones se ejecutan explícitamente, antes de iniciar la API; el arranque verifica conexión y no modifica datos. `db/migrations/001_payments_schema.sql` contiene el esquema final de las tres tablas; es repetible en una base nueva. No adapta instalaciones parciales antiguas ni copia datos desde Ms-club.

## API

| Método | Ruta bajo `/api/v1/payments` | Acceso |
| --- | --- | --- |
| GET | `/health/ready` | Público |
| GET | `/plans?tipo=USUARIO\|CLUB` | Público, planes activos |
| POST | `/webpay/initiate` | JWT deportista o representante; `{plan_id, client?}` |
| POST | `/webpay/commit` | JWT del iniciador; tokens de retorno Webpay |
| GET | `/subscription/me` | JWT del titular |
| GET/POST | `/webpay/return` | Retorno de navegador |
| GET | `/webpay/checkout` | Ticket firmado |
| GET/POST | `/webpay/mock` | Solo con gateway mock |
| GET | `/webpay/mock/decision` | Solo con gateway mock |
| GET/POST | `/admin/plans` | JWT con rol `admin` |
| PATCH | `/admin/plans/{id}` | JWT `admin` |
| POST | `/admin/plans/{id}/deactivate` | JWT `admin` |
| POST | `/admin/plans/{id}/reactivate` | JWT `admin` |
| GET | `/admin/plans/{id}/subscriptions` | JWT `admin` |

Los errores de validación mantienen HTTP 400. Las modificaciones administrativas exigen auditoría disponible; si falla, el repositorio revierte la escritura y responde 503. Planes base, unicidad de nombres activos, planes gratuitos, convenios y filtros conservan sus reglas.

## Límites de datos y dependencias

`PaymentRepository` y `PostgresPaymentRepository` solo acceden a `plan_suscripcion`, `transaccion_pago` y `suscripcion`. Mantienen nombres, columnas e IDs originales; `club_id` y `usuario_id` son referencias UUID externas sin FK. La transferencia de datos debe preservar IDs y snapshots, insertar planes con `OVERRIDING SYSTEM VALUE`, restaurar referencias de última transacción y ajustar la secuencia de planes. La migración de datos y el cambio de rutas en gateway/compose se coordinan fuera de este paquete.

`ClubsDirectory.managed_club(actor)` identifica el club mediante `GET {CLUBS_SERVICE_URL}/api/v1/clubs/me` reenviando `Actor.token`; no acepta un club elegido por el cliente. Sin token del actor falla con 503, sin intentar resolver propietarios a partir de IDs. Un 404 significa que aún no administra un club; fallas de red o respuesta inválida producen 503 y no crean transacciones. `names(ids)` consulta perfiles públicos por HTTP para mostrar nombres de clubes, con fallo tolerado.

Nombres de deportistas y auditoría usan HTTP a Ms_Users: `/internal/v1/accounts/names` y `/internal/v1/audit/events`, con `INTERNAL_SERVICE_TOKEN` compartido de al menos 32 bytes. Nunca se consultan tablas de usuarios. Un fallo de nombres deja el nombre vacío y no bloquea el listado. La auditoría remota y el commit local no son una transacción distribuida: si la auditoría se escribe y luego falla el commit, puede quedar un evento extra.

Las compras congelan nombre, precio, duración y beneficios. Confirmaciones repetidas solo liquidan `PENDING` una vez; el upsert SQL extiende la suscripción atómicamente y los cambios de plan se protegen con `FOR UPDATE`. El repositorio mock usa un lock y el directorio falso de pruebas vive separado del almacenamiento financiero.

`WebpaySettings` y `get_webpay_settings` se exportan desde `app.core.config`. El mock requiere configuración explícita y está prohibido en producción; los pagos reales requieren URLs y credenciales apropiadas. Véase `docs/webpay/` para el gateway extraído por el trabajador responsable.

## Pruebas

`python -m pytest` ejecuta contratos HTTP de pagos, administración, snapshots, idempotencia, fallos de auditoría y directorio HTTP usando fakes, sin bases reales ni Docker. Las pruebas PostgreSQL opcionales requieren `PAYMENTS_TEST_DATABASE_URL` apuntando a una base descartable: crean esquemas temporales únicos y los eliminan al terminar. No usar una base de producción ni las bases de clubes o usuarios.
