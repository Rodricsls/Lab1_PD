"""Carga y validación de config/params.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parents[2]
RUTA_PARAMS = RAIZ / "config" / "params.yaml"


class ConfigInvalida(ValueError):
    """La configuración no cumple las reglas mínimas del producto."""


def cargar_params(ruta: Path | str = RUTA_PARAMS) -> dict[str, Any]:
    with open(ruta, encoding="utf-8") as f:
        params = yaml.safe_load(f)
    validar_params(params)
    return params


def validar_params(params: dict[str, Any]) -> None:
    costos = params["costos"]
    if costos["quiebre_por_unidad"] <= 0 or costos["merma_por_unidad"] <= 0:
        raise ConfigInvalida("Los costos de quiebre y merma deben ser positivos.")

    cuantiles = params["pronostico"]["cuantiles"]
    if not cuantiles or any(not 0 < q < 1 for q in cuantiles):
        raise ConfigInvalida("Los cuantiles deben estar en el intervalo (0, 1).")
    if cuantiles != sorted(cuantiles):
        raise ConfigInvalida("Los cuantiles deben estar ordenados.")

    if params["calendario"]["horizonte_dias"] < 1:
        raise ConfigInvalida("El horizonte debe ser de al menos un día.")

    bandeja = params["bandeja"]
    if any(v < 0 for v in bandeja["latencias_dias"].values()):
        raise ConfigInvalida("Las latencias no pueden ser negativas.")
    corr = bandeja["correcciones"]
    if not (0 <= corr["fraccion_archivos"] <= 1 and 0 < corr["fraccion_filas"] <= 1):
        raise ConfigInvalida("Las fracciones de corrección deben estar en [0, 1].")
    if not 1 <= corr["retraso_min_dias"] <= corr["retraso_max_dias"]:
        raise ConfigInvalida("Retrasos de corrección: se requiere 1 <= mínimo <= máximo.")
    if corr["retraso_deteccion_dias"] < 1:
        raise ConfigInvalida("El retraso de detección debe ser de al menos un día.")


def cuantil_critico(params: dict[str, Any]) -> float:
    """Nivel de servicio óptimo del problema newsvendor: Cu / (Cu + Co)."""
    cu = params["costos"]["quiebre_por_unidad"]
    co = params["costos"]["merma_por_unidad"]
    return cu / (cu + co)
