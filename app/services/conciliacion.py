"""Conciliación de pagos PENDING: pregunta a Transbank por lo que quedó sin cerrar.

Un pago queda PENDING si el navegador nunca volvió (se cerró la app, se cortó la red) o si el retorno no pudo
confirmarse. conciliar() recorre los PENDING de más de PAGO_CONCILIAR_MINUTOS (30 por defecto) y los cierra según
lo que diga Transbank; ConciliadorWorker lo corre al iniciar y luego cada cierto tiempo, igual que el aviso de
vencimiento. Es seguro correrlo varias veces (o desde dos instancias): settle_transaccion solo cambia lo que sigue
PENDING y nunca extiende dos veces la suscripción. Si Transbank no responde, el pago se deja PENDING para la
próxima pasada.

Cierres: autorizado -> APPROVED (confirma de forma idempotente); rechazado -> REJECTED; sin pagar, revertido o
desconocido para Transbank -> CANCELLED. El estado EXPIRED no existe en transaccion_pago (su CHECK solo admite
PENDING, APPROVED, REJECTED y CANCELLED): un pago vencido se cierra como CANCELLED. Nunca toca la suscripción,
salvo al confirmar un pago realmente autorizado.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ANTIGUEDAD_MINUTOS = 30.0
INTERVALO_SEGUNDOS = 900.0
LOTE = 100
ENV_ENABLED = "PAGO_CONCILIAR_ENABLED"
ENV_ANTIGUEDAD = "PAGO_CONCILIAR_MINUTOS"
ENV_INTERVALO = "PAGO_CONCILIAR_INTERVALO_SEGUNDOS"


@dataclass(frozen=True)
class Resultado:
    revisadas: int = 0
    aprobadas: int = 0
    rechazadas: int = 0
    canceladas: int = 0
    errores: int = 0  # Transbank no respondió o contestó algo inesperado: sigue PENDING


def _number(name: str, default: float, low: float, high: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number") from error
    if not low <= value <= high:
        raise RuntimeError(f"{name} must be between {low:g} and {high:g}")
    return value


def enabled() -> bool:
    return os.getenv(ENV_ENABLED, "true").strip().lower() not in {"false", "0", "no", "off"}


def antiguedad_minutos() -> float:
    return _number(ENV_ANTIGUEDAD, ANTIGUEDAD_MINUTOS, 5, 7 * 24 * 60)


def intervalo() -> float:
    return _number(ENV_INTERVALO, INTERVALO_SEGUNDOS, 60, 86400)


def conciliar(service, ahora: datetime | None = None, *, antiguedad: float | None = None, limite: int = LOTE) -> Resultado:
    """One pass over the old PENDING payments (see PagoService.conciliar_pendientes)."""
    return service.conciliar_pendientes(
        ahora or datetime.now(timezone.utc),
        antiguedad_minutos() if antiguedad is None else antiguedad, limite)


class ConciliadorWorker:
    """Daemon thread running conciliar() at start and then every interval; a failure only goes to the log."""

    def __init__(self, service, *, interval: float = INTERVALO_SEGUNDOS,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._service = service
        self._interval = interval
        self._clock = clock
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="conciliar-pagos", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def run_once(self) -> Resultado | None:
        try:
            resultado = conciliar(self._service, self._clock())
        except Exception:  # noqa: BLE001 - a failed pass is logged and retried next time
            logger.exception("Falló la conciliación de pagos pendientes")
            return None
        if resultado.revisadas:
            logger.info("Conciliación de pagos: %d revisados, %d aprobados, %d rechazados, %d cancelados, %d sin respuesta",
                        resultado.revisadas, resultado.aprobadas, resultado.rechazadas, resultado.canceladas,
                        resultado.errores)
        return resultado

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self._interval)
