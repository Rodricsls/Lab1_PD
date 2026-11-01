import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from reposicion.config import cargar_params

FECHA_SIN_VENTAS = "2017-01-15"  # simula un día de tiendas cerradas


def escribir_kaggle_sintetico(carpeta: Path, desde="2016-12-01", hasta="2017-01-31") -> None:
    """Versión diminuta de los 5 CSV de Kaggle: 2 tiendas × 2 familias."""
    carpeta.mkdir(parents=True, exist_ok=True)
    fechas = [d for d in pd.date_range(desde, hasta).strftime("%Y-%m-%d") if d != FECHA_SIN_VENTAS]
    tiendas, familias = [1, 2], ["BEVERAGES", "DAIRY"]
    rng = np.random.default_rng(0)

    train = pd.DataFrame(
        [(f, t, fa) for f in fechas for t in tiendas for fa in familias],
        columns=["date", "store_nbr", "family"],
    )
    train.insert(0, "id", range(len(train)))
    train["sales"] = rng.integers(0, 500, len(train)).astype(float)
    train["onpromotion"] = rng.integers(0, 5, len(train))
    train.to_csv(carpeta / "train.csv", index=False)

    trans = pd.DataFrame([(f, t) for f in fechas for t in tiendas], columns=["date", "store_nbr"])
    trans["transactions"] = rng.integers(500, 2000, len(trans))
    trans.to_csv(carpeta / "transactions.csv", index=False)

    habiles = pd.bdate_range(desde, hasta).strftime("%Y-%m-%d")
    oil = pd.DataFrame({"date": habiles, "dcoilwtico": rng.uniform(40, 60, len(habiles))})
    oil.loc[3, "dcoilwtico"] = np.nan
    oil.to_csv(carpeta / "oil.csv", index=False)

    pd.DataFrame(
        {
            "date": ["2016-12-25", "2017-01-01", "2017-02-27"],
            "type": ["Holiday", "Holiday", "Holiday"],
            "locale": ["National"] * 3,
            "locale_name": ["Ecuador"] * 3,
            "description": ["Navidad", "Primer dia del ano", "Carnaval"],
            "transferred": [False] * 3,
        }
    ).to_csv(carpeta / "holidays_events.csv", index=False)

    pd.DataFrame(
        {
            "store_nbr": tiendas,
            "city": ["Quito", "Guayaquil"],
            "state": ["Pichincha", "Guayas"],
            "type": ["D", "A"],
            "cluster": [13, 4],
        }
    ).to_csv(carpeta / "stores.csv", index=False)


@pytest.fixture
def kaggle(tmp_path: Path) -> Path:
    carpeta = tmp_path / "kaggle"
    escribir_kaggle_sintetico(carpeta)
    return carpeta


@pytest.fixture
def params() -> dict:
    p = copy.deepcopy(cargar_params())
    p["bandeja"]["corte_historico"] = "2016-12-31"
    p["bandeja"]["correcciones"]["simular"] = False
    return p
