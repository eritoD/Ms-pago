"""Límites de uso por plan (HU-54). Único lugar donde se definen: los otros servicios los leen de
GET /internal/v1/entitlements/{usuario|club}/{id} y nunca los copian.

Reglas decididas el 2026-11-08 (docs/PLAN-REESTRUCTURACION-PAGOS.md, sección 3; reemplazan las de Jira):

- ``maximo``: cuántas veces se puede hacer la acción; ``None`` = sin límite.
- ``ventana_horas``: ventana móvil en horas (cuenta lo hecho en las últimas N horas);
  ``None`` = se cuenta lo que existe a la vez (convocatorias abiertas, canales).
"""
from __future__ import annotations

from typing import Final

HORAS_DIA: Final = 24
# "3 al mes": ventana móvil de 30 días, igual que las de 24 h (no depende de la zona horaria).
HORAS_MES: Final = 24 * 30

PLAN_GRATIS: Final = "GRATIS"
PLAN_PRO: Final = "PRO"

# Recurso -> (maximo, ventana_horas)
LIMITES: Final[dict[str, dict[str, dict[str, tuple[int | None, int | None]]]]] = {
    "usuario": {
        PLAN_GRATIS: {
            "solicitudes_match": (6, HORAS_DIA),
            "postulaciones_actividades": (5, HORAS_DIA),
            "postulaciones_convocatorias": (5, HORAS_DIA),
            "actividades_organizadas": (3, HORAS_MES),
        },
        PLAN_PRO: {
            "solicitudes_match": (None, HORAS_DIA),
            "postulaciones_actividades": (None, HORAS_DIA),
            "postulaciones_convocatorias": (None, HORAS_DIA),
            "actividades_organizadas": (None, HORAS_MES),
        },
    },
    "club": {
        PLAN_GRATIS: {
            "convocatorias_abiertas": (2, None),
            "comunicados": (4, HORAS_DIA),
            "canales": (1, None),
        },
        PLAN_PRO: {
            "convocatorias_abiertas": (None, None),
            "comunicados": (None, HORAS_DIA),
            # Tope técnico actual de Ms-club (MAX_CANALES).
            "canales": (20, None),
        },
    },
}

# Beneficios que no son conteos (perfil/club destacado, filtros avanzados): solo con plan pagado.
BENEFICIOS: Final[dict[str, tuple[str, ...]]] = {
    PLAN_GRATIS: (),
    PLAN_PRO: ("destacado", "filtros_avanzados"),
}


def limites(tipo: str, plan: str) -> dict[str, dict[str, int | None]]:
    return {
        recurso: {"maximo": maximo, "ventana_horas": ventana}
        for recurso, (maximo, ventana) in LIMITES[tipo][plan].items()
    }
