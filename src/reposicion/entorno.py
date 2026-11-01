"""Diagnóstico del entorno de ejecución: rutas, configuración y montajes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from reposicion.config import RAIZ, cargar_params, cuantil_critico


def raiz_proyecto() -> Path:
    """Raíz del repositorio; en Docker la fija LAB1_RAIZ."""
    return Path(os.environ.get("LAB1_RAIZ", RAIZ))


def contexto(raiz: Path | None = None) -> tuple[dict[str, Any], dict[str, Path]]:
    """Parámetros y rutas absolutas del proyecto, para que los DAGs no armen rutas."""
    raiz = raiz or raiz_proyecto()
    params = cargar_params(raiz / "config" / "params.yaml")
    return params, {nombre: raiz / ruta for nombre, ruta in params["rutas"].items()}


def es_escribible(carpeta: Path) -> bool:
    return carpeta.is_dir() and os.access(carpeta, os.W_OK)


def diagnostico(raiz: Path | None = None) -> dict[str, Any]:
    raiz = raiz or raiz_proyecto()
    params = cargar_params(raiz / "config" / "params.yaml")
    rutas = params["rutas"]
    bandeja = raiz / rutas["bandeja"]
    return {
        "raiz": str(raiz),
        "cuantil_critico": round(cuantil_critico(params), 4),
        "kaggle_existe": (raiz / rutas["kaggle"]).is_dir(),
        "bandeja_existe": bandeja.is_dir(),
        "bandeja_escribible": es_escribible(bandeja),
    }
