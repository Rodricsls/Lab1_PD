"""Convierte el pronóstico por cuantiles en un pedido sugerido según los costos."""

from __future__ import annotations

from typing import Any

import pandas as pd

from reposicion.config import cuantil_critico
from reposicion.modelos import col


def calcular_pedido(pronostico: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Pedido diario = pronóstico en el cuantil crítico Cu / (Cu + Co) (newsvendor)."""
    q = round(cuantil_critico(params), 2)
    pedido = pronostico[["fecha_corte", "store_nbr", "family", "date", "h"]].copy()
    pedido["cuantil_critico"] = q
    pedido["pedido_sugerido"] = pronostico[col(q)].round(2)
    return pedido


def pedido_semanal(pedido: pd.DataFrame) -> pd.DataFrame:
    """Total por tienda y familia para la semana del pedido (los primeros 7 días)."""
    semana = pedido[pedido["h"] <= 7]
    return (
        semana.groupby(["fecha_corte", "store_nbr", "family"], as_index=False)["pedido_sugerido"]
        .sum()
        .rename(columns={"pedido_sugerido": "pedido_semana"})
    )
