"""HU-55: notice before a subscription ends (3 days before and on the day itself).

decidir() is the rule, a pure function of the subscription and the time given. revisar() applies it once:
it writes each notice into the aviso_pendiente outbox, whose unique (suscripcion, tipo, fecha_fin) makes a
second pass, or a second instance, harmless. AvisoVencimientoWorker runs revisar() at start and then every
interval (hourly by default, so the day-of-expiry notice is not missed when the plan ends early in the day)
in a daemon thread; a failure only goes to the log. The gateway publishes the outbox through
/internal/v1/avisos (see app/api/v1/avisos_internos.py).

Days are counted in calendar days of AVISO_VENCIMIENTO_ZONA (America/Santiago by default). When the
subscription is renewed its fecha_fin moves, so the new period gets its own notices and the pending ones of
the old period are discarded. An ended subscription (EXPIRED) is never notified: the account goes back to
the free plan without losing data, as before.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from app.repositories.avisos import AvisoNuevo, AvisoRepository, SuscripcionPorVencer

logger = logging.getLogger(__name__)

DIAS_ANTES = 3
INTERVALO_SEGUNDOS = 3600.0
ZONA = "America/Santiago"
ENV_ENABLED = "AVISO_VENCIMIENTO_ENABLED"
ENV_INTERVALO = "AVISO_VENCIMIENTO_INTERVALO_SEGUNDOS"
ENV_ZONA = "AVISO_VENCIMIENTO_ZONA"
KIND = "suscripcion"

Tipo = Literal["PREVIO", "HOY"]


def dias_restantes(fecha_fin: datetime, ahora: datetime, zona: ZoneInfo) -> int:
    """Calendar days from today to the day the subscription ends (0 = it ends today)."""
    return (fecha_fin.astimezone(zona).date() - ahora.astimezone(zona).date()).days


def decidir(suscripcion: SuscripcionPorVencer, ahora: datetime, zona: ZoneInfo) -> Tipo | None:
    """Which notice the subscription needs at ahora, if any.

    HOY: it ends today and is still active. PREVIO: it ends within the next 3 days (3 normally; 2 or 1 only
    if the pass of the 3rd day was missed), and the period is longer than those 3 days, so a payment just
    made is not answered with "your plan is ending"."""
    if suscripcion.fecha_fin <= ahora:
        return None
    dias = dias_restantes(suscripcion.fecha_fin, ahora, zona)
    if dias == 0:
        return "HOY"
    if 1 <= dias <= DIAS_ANTES and suscripcion.fecha_fin - suscripcion.fecha_inicio > timedelta(days=DIAS_ANTES):
        return "PREVIO"
    return None


def destino(suscripcion: SuscripcionPorVencer) -> dict[str, str]:
    """Where the notice opens: the payment of the same plan (/pago?plan=<id>); the catalog if the plan was
    withdrawn. A club also gets the page of its subscription in the web panel."""
    data: dict[str, str]
    if suscripcion.plan_activo:
        data = {"screen": "pago", "planId": str(suscripcion.plan_id), "ruta": f"/pago?plan={suscripcion.plan_id}"}
    else:
        data = {"screen": "planes"}
    data["suscripcionId"] = str(suscripcion.id)
    if suscripcion.club_id is not None:
        data["clubId"] = str(suscripcion.club_id)
        data["rutaWeb"] = "/suscripcion"
    return data


def mensaje(suscripcion: SuscripcionPorVencer, tipo: Tipo, ahora: datetime, zona: ZoneInfo) -> tuple[str, str]:
    fin = suscripcion.fecha_fin.astimezone(zona)
    quien = "de tu club " if suscripcion.club_id is not None else ""
    plan = f"Tu plan {quien}{suscripcion.plan_nombre}"
    renovar = ("Renuévalo para no perder los beneficios." if suscripcion.plan_activo
               else "Ese plan ya no se ofrece: elige otro para seguir con los beneficios.")
    if tipo == "HOY":
        return ("Tu plan vence hoy",
                f"{plan} vence hoy a las {fin:%H:%M}. {renovar} Si no, pasarás al plan gratuito sin perder tus datos.")
    dias = dias_restantes(suscripcion.fecha_fin, ahora, zona)
    cuando = "mañana" if dias == 1 else f"en {dias} días"
    return "Tu plan vence pronto", f"{plan} vence {cuando}, el {fin:%d-%m-%Y}. {renovar}"


@dataclass(frozen=True)
class Resultado:
    avisados: int = 0
    descartados: int = 0
    sin_destinatario: int = 0


def revisar(repository: AvisoRepository, ahora: datetime, zona: ZoneInfo | None = None) -> Resultado:
    """One pass: writes the notices due at ahora and discards the stale pending ones."""
    zona = zona or ZoneInfo(ZONA)
    descartados = repository.descartar_avisos_obsoletos(ahora)
    avisados = sin_destinatario = 0
    # One extra day covers any time zone offset; decidir() keeps only what is due.
    for suscripcion in repository.suscripciones_por_vencer(ahora, ahora + timedelta(days=DIAS_ANTES + 1)):
        tipo = decidir(suscripcion, ahora, zona)
        if tipo is None:
            continue
        if suscripcion.destinatario is None:
            sin_destinatario += 1
            continue
        title, body = mensaje(suscripcion, tipo, ahora, zona)
        if repository.registrar_aviso(AvisoNuevo(
                suscripcion_id=suscripcion.id, tipo=tipo, fecha_fin=suscripcion.fecha_fin,
                user_id=suscripcion.destinatario, plan_id=suscripcion.plan_id, kind=KIND,
                title=title, body=body, data=destino(suscripcion))):
            avisados += 1
    return Resultado(avisados=avisados, descartados=descartados, sin_destinatario=sin_destinatario)


def enabled() -> bool:
    """On unless AVISO_VENCIMIENTO_ENABLED is false/0/no/off."""
    return os.getenv(ENV_ENABLED, "true").strip().lower() not in {"false", "0", "no", "off"}


def intervalo() -> float:
    raw = os.getenv(ENV_INTERVALO, "").strip()
    if not raw:
        return INTERVALO_SEGUNDOS
    try:
        value = float(raw)
    except ValueError as error:
        raise RuntimeError(f"{ENV_INTERVALO} must be a number of seconds") from error
    if not 60 <= value <= 86400:
        raise RuntimeError(f"{ENV_INTERVALO} must be between 60 and 86400 seconds")
    return value


def zona() -> ZoneInfo:
    name = os.getenv(ENV_ZONA, ZONA).strip() or ZONA
    try:
        return ZoneInfo(name)
    except Exception as error:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError...
        raise RuntimeError(f"{ENV_ZONA} must be an IANA time zone such as {ZONA}") from error


class AvisoVencimientoWorker:
    """Daemon thread running revisar() at start and then every interval."""

    def __init__(
        self,
        repository: AvisoRepository,
        *,
        interval: float = INTERVALO_SEGUNDOS,
        zona: ZoneInfo | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._interval = interval
        self._zona = zona or ZoneInfo(ZONA)
        self._clock = clock
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="aviso-vencimiento", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def run_once(self) -> Resultado | None:
        try:
            resultado = revisar(self._repository, self._clock(), self._zona)
        except Exception:  # noqa: BLE001 - a failed pass is logged and retried next time
            logger.exception("Falló la revisión de suscripciones por vencer")
            return None
        if resultado.avisados or resultado.descartados:
            logger.info("Avisos de vencimiento: %d nuevos, %d descartados",
                        resultado.avisados, resultado.descartados)
        if resultado.sin_destinatario:
            logger.warning("%d suscripciones por vencer sin cuenta a quien avisar", resultado.sin_destinatario)
        return resultado

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self._interval)
