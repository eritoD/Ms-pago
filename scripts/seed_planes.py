"""Siembra el catálogo de suscripciones de la app (Gratuito, Pro y Club). Idempotente y reejecutable.

    python scripts/seed_planes.py            # aplica (usa DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME)
    python scripts/seed_planes.py --dry-run  # solo muestra qué haría

No es una migración: solo INSERTA lo que falta y nunca modifica ni desactiva un plan existente.
- Gratuito: tipo USUARIO, precio 0, sin vencimiento (duracion_dias NULL) y es_base = true (el plan base que no se puede
  desactivar y que nunca se cobra por Webpay). Solo se crea si el deportista no tiene ya un plan base.
- Pro: tipo USUARIO (lo compra un deportista), $4.990, 30 días.
- Club: tipo CLUB (lo compra el representante de un club), $19.990, 30 días.
Un plan se omite si ya existe uno ACTIVO con el mismo nombre (índice único por nombre activo).
Los precios son pesos chilenos enteros, tal cual se cobran: Ms-pago no modela el IVA aparte. `descripcion` y `beneficios`
repiten los textos de la pantalla de planes de la app para que se vea idéntica.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# nombre, tipo, descripcion, precio, duracion_dias, es_base, beneficios (como los muestra la pantalla de planes)
PLANES = (
    ("Gratuito", "USUARIO", "Para empezar a entrenar en comunidad", 0, None, True,
     ["Actividades que puedes organizar al mes: 3", "Postulaciones activas a la vez: 5",
      "Filtros de búsqueda: Básicos", "Chat con tus matches"]),
    ("Pro", "USUARIO", "Para deportistas que entrenan seguido", 4990, 30, False,
     ["Actividades que puedes organizar al mes: Sin límite", "Postulaciones activas a la vez: Sin límite",
      "Filtros de búsqueda: Avanzados", "Chat con tus matches", "Insignia de perfil destacado"]),
    ("Club", "CLUB", "Para clubes, escuelas y comunidades", 19990, 30, False,
     ["Actividades que puedes organizar al mes: Sin límite", "Postulaciones activas a la vez: Sin límite",
      "Filtros de búsqueda: Avanzados", "Chat con tus matches", "Insignia de perfil destacado",
      "Panel web para el club", "Convocatorias y gestión de postulantes", "Estadísticas de postulaciones"]),
)


def sembrar(conn: psycopg.Connection, dry_run: bool = False) -> list[str]:
    """Inserta los planes que faltan y devuelve una línea por plan ('creado' u 'omitido: motivo')."""
    resultado = []
    for nombre, tipo, descripcion, precio, dias, es_base, beneficios in PLANES:
        if conn.execute("SELECT 1 FROM plan_suscripcion WHERE is_active AND lower(btrim(nombre)) = lower(%s)",
                        (nombre,)).fetchone():
            resultado.append(f"{nombre}: omitido (ya hay un plan activo con ese nombre)")
            continue
        if es_base and conn.execute("SELECT 1 FROM plan_suscripcion WHERE es_base AND tipo = %s", (tipo,)).fetchone():
            resultado.append(f"{nombre}: omitido (ya hay un plan base de {tipo})")
            continue
        if not dry_run:
            conn.execute(
                "INSERT INTO plan_suscripcion (nombre, tipo, descripcion, precio, duracion_dias, es_base, beneficios) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (nombre, tipo, descripcion, precio, dias, es_base, Jsonb(beneficios)))
        resultado.append(f"{nombre}: {'se crearía' if dry_run else 'creado'} ({tipo}, ${precio})")
    return resultado


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="muestra qué haría sin escribir")
    args = parser.parse_args()
    from app.core.config import get_database_url
    with psycopg.connect(get_database_url()) as conn:  # una transacción: todo o nada
        for linea in sembrar(conn, args.dry_run):
            print(linea)


if __name__ == "__main__":
    main()
