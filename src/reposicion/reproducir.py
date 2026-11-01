"""Reproduce un pronóstico publicado y lo compara con lo que se publicó.

Con el código de un commit y los datos que DVC asocia a ese commit (histórico, modelos y
publicados), se vuelve a calcular el pronóstico de una fecha de corte:

- por defecto con el modelo guardado en el registro para esa fecha;
- con `--reentrenar`, entrenando de nuevo el modelo desde el histórico, en la fecha en que
  se entrenó originalmente (LightGBM es determinista con la configuración del proyecto).

    python -m reposicion.reproducir --fecha-corte 2017-07-03 [--reentrenar]
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from reposicion.features import Panel, cargar_fuentes
from reposicion.modelos import cargar, crear
from reposicion.produccion import vigente_al

LLAVE = ["store_nbr", "family", "date"]


def reproducir(
    rutas: dict[str, Path], params: dict[str, Any], fecha_corte: date, reentrenar: bool = False
) -> dict[str, Any]:
    carpeta = rutas["publicados"] / f"fecha_corte={fecha_corte.isoformat()}"
    publicado = pd.read_parquet(carpeta / "pronostico.parquet")
    metadata = json.loads((carpeta / "metadata.json").read_text(encoding="utf-8"))
    entrada = vigente_al(rutas["modelos"], fecha_corte)
    horizonte = params["calendario"]["horizonte_dias"]

    if reentrenar:
        fecha_modelo = date.fromisoformat(entrada["fecha_corte"])
        panel_modelo = Panel(cargar_fuentes(rutas["historico"], fecha_modelo), params)
        modelo = crear(entrada["modelo"], params).ajustar(panel_modelo, fecha_modelo, horizonte)
    else:
        modelo = cargar(rutas["modelos"] / entrada["artefacto"])

    panel = Panel(cargar_fuentes(rutas["historico"], fecha_corte), params)
    nuevo = modelo.predecir(panel, fecha_corte, horizonte)

    columnas = [c for c in publicado.columns if c.startswith("q0.")]
    a = publicado.sort_values(LLAVE).reset_index(drop=True)
    b = nuevo.sort_values(LLAVE).reset_index(drop=True)
    mismas_llaves = len(a) == len(b) and a[LLAVE].equals(b[LLAVE])
    diferencia = (
        float(np.abs(a[columnas].to_numpy() - b[columnas].to_numpy()).max())
        if mismas_llaves
        else float("inf")
    )
    return {
        "fecha_corte": fecha_corte.isoformat(),
        "commit_publicacion": metadata["commit"],
        "modelo": entrada["modelo"],
        "modelo_entrenado": entrada["fecha_corte"],
        "reentrenado": reentrenar,
        "filas": len(b),
        "mismas_llaves": mismas_llaves,
        "max_diferencia_abs": diferencia,
        "identico": mismas_llaves and diferencia < 1e-9,
    }


def main(argv: list[str] | None = None) -> None:
    from reposicion.entorno import contexto

    parser = argparse.ArgumentParser(description="Reproduce un pronóstico publicado.")
    parser.add_argument("--fecha-corte", type=date.fromisoformat, required=True)
    parser.add_argument("--reentrenar", action="store_true")
    args = parser.parse_args(argv)
    params, rutas = contexto()
    print(json.dumps(reproducir(rutas, params, args.fecha_corte, args.reentrenar), indent=2))


if __name__ == "__main__":
    main()
