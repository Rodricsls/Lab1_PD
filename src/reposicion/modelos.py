"""Baseline, modelo de ML, backtest temporal y criterio de promoción.

Ambos modelos exponen la misma interfaz: `ajustar(panel, fecha_corte, horizonte)` con lo
que se sabía en la fecha de corte y `predecir(panel, origen, horizonte)`, que devuelve una
fila por serie y día con una columna por cuantil (`q0.10`, `q0.50`, ...). El backtest
repite exactamente el procedimiento de producción en varias fechas de corte pasadas.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from reposicion.config import cuantil_critico
from reposicion.features import (
    CATEGORICAS,
    Panel,
    cargar_fuentes,
    origenes_entrenamiento,
    sin_avisos_nan,
)

log = logging.getLogger(__name__)

NUMERICAS = [
    "h",
    "dia_semana",
    "dia_mes",
    "dia_pago",
    "mismo_dia_ultimo",
    "mismo_dia_media_4",
    "promo",
    "feriado_nacional",
    "feriado_regional",
    "feriado_local",
    "evento_nacional",
    "ultimo",
    "media_7",
    "media_14",
    "media_28",
    "media_56",
    "std_28",
    "ceros_28",
    "tendencia",
    "promo_media_28",
    "transacciones_28",
    "petroleo",
    "petroleo_cambio_28",
]
FEATURES = CATEGORICAS + NUMERICAS
LLAVE = ["store_nbr", "family", "date"]


def cuantiles_modelo(params: dict[str, Any]) -> list[float]:
    """Cuantiles publicados más el cuantil crítico que usa el pedido."""
    return sorted(set(params["pronostico"]["cuantiles"]) | {round(cuantil_critico(params), 2)})


def col(q: float) -> str:
    return f"q{q:.2f}"


def _tabla_pronostico(panel: Panel, origen: date, h: int, valores: np.ndarray, cuantiles):
    objetivo = origen + timedelta(days=h - 1)
    df = pd.DataFrame(
        {
            "store_nbr": panel.series.get_level_values("store_nbr"),
            "family": panel.series.get_level_values("family"),
            "date": objetivo.isoformat(),
            "h": h,
        }
    )
    for i, q in enumerate(cuantiles):
        df[col(q)] = valores[:, i]
    return df


class Baseline:
    """Cuantil empírico de las últimas N ventas del mismo día de la semana."""

    nombre = "baseline"

    def __init__(self, params: dict[str, Any]):
        self.cuantiles = cuantiles_modelo(params)
        self.semanas = params["baseline"]["semanas"]

    def ajustar(self, panel: Panel, fecha_corte: date, horizonte: int) -> Baseline:
        self.entrenado_con = {"fecha_corte": fecha_corte.isoformat()}
        return self

    def predecir(self, panel: Panel, origen: date, horizonte: int) -> pd.DataFrame:
        bloques = []
        for h in range(1, horizonte + 1):
            objetivo = origen + timedelta(days=h - 1)
            historia = panel.mismo_dia(origen, objetivo, self.semanas)
            with sin_avisos_nan():
                q = np.nanquantile(historia, self.cuantiles, axis=0).T
            bloques.append(_tabla_pronostico(panel, origen, h, np.nan_to_num(q), self.cuantiles))
        return pd.concat(bloques, ignore_index=True)


class ModeloLGBM:
    """LightGBM global con regresión cuantílica sobre log1p(ventas), un modelo por cuantil."""

    nombre = "lightgbm"

    def __init__(self, params: dict[str, Any]):
        self.cuantiles = cuantiles_modelo(params)
        self.cfg = params["entrenamiento"]
        self.semilla = params["semilla"]

    def _x(self, df: pd.DataFrame) -> pd.DataFrame:
        x = df[FEATURES].copy()
        for c in CATEGORICAS:
            x[c] = pd.Categorical(x[c].astype(str), categories=self.categorias[c])
        return x

    def ajustar(self, panel: Panel, fecha_corte: date, horizonte: int) -> ModeloLGBM:
        origenes = origenes_entrenamiento(fecha_corte, panel, horizonte, self.cfg)
        df = pd.concat([panel.filas(o, horizonte, con_objetivo=True) for o in origenes])
        df = df[df["y"].notna()]
        self.categorias = {c: sorted(df[c].astype(str).unique()) for c in CATEGORICAS}
        x, y = self._x(df), np.log1p(df["y"].clip(lower=0))

        base = {
            k: v for k, v in self.cfg["lightgbm"].items() if k != "num_boost_round"
        } | {"objective": "quantile", "verbose": -1, "seed": self.semilla, "num_threads": 0}
        self.boosters = {}
        for q in self.cuantiles:
            datos = lgb.Dataset(x, y, categorical_feature=CATEGORICAS, free_raw_data=False)
            self.boosters[q] = lgb.train(
                base | {"alpha": q}, datos, num_boost_round=self.cfg["lightgbm"]["num_boost_round"]
            )
        self.entrenado_con = {
            "fecha_corte": fecha_corte.isoformat(),
            "origenes": [o.isoformat() for o in origenes],
            "filas": len(df),
        }
        log.info("LightGBM ajustado: %s", self.entrenado_con)
        return self

    def predecir(self, panel: Panel, origen: date, horizonte: int) -> pd.DataFrame:
        df = panel.filas(origen, horizonte)
        x = self._x(df)
        valores = np.column_stack(
            [np.expm1(self.boosters[q].predict(x)).clip(min=0) for q in self.cuantiles]
        )
        valores.sort(axis=1)  # los cuantiles no se cruzan
        salida = df[["store_nbr", "family", "date", "h"]].copy()
        for i, q in enumerate(self.cuantiles):
            salida[col(q)] = valores[:, i]
        return salida.reset_index(drop=True)


MODELOS = {"baseline": Baseline, "lightgbm": ModeloLGBM}


def crear(nombre: str, params: dict[str, Any]):
    return MODELOS[nombre](params)


def guardar(modelo, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + ".tmp")
    joblib.dump(modelo, temporal)
    temporal.replace(ruta)


def cargar(ruta: Path):
    return joblib.load(ruta)


# --------------------------------------------------------------------------- evaluación


def evaluar(pred: pd.DataFrame, real: pd.DataFrame, params: dict[str, Any]) -> dict[str, float]:
    """Métricas técnicas del pronóstico y costo de negocio del pedido (cuantil crítico)."""
    real = real.assign(store_nbr=real["store_nbr"].astype(int))[LLAVE + ["sales"]]
    df = pred.merge(real, on=LLAVE, how="inner")
    y = df["sales"].to_numpy()
    total = y.sum()

    cuantiles = params["pronostico"]["cuantiles"]
    pinball = 0.0
    for q in cuantiles:
        e = y - df[col(q)].to_numpy()
        pinball += np.maximum(q * e, (q - 1) * e).sum()

    costos = params["costos"]
    pedido = df[col(round(cuantil_critico(params), 2))].to_numpy()
    faltante = np.maximum(y - pedido, 0)
    sobrante = np.maximum(pedido - y, 0)
    costo = costos["quiebre_por_unidad"] * faltante + costos["merma_por_unidad"] * sobrante

    bajo, alto = df[col(min(cuantiles))], df[col(max(cuantiles))]
    return {
        "filas": int(len(df)),
        "wape_p50": float(np.abs(y - df[col(0.5)]).sum() / total),
        "pinball_rel": float(pinball / len(cuantiles) / total),
        "cobertura_intervalo": float(((y >= bajo) & (y <= alto)).mean()),
        "costo_negocio": float(costo.sum()),
        "costo_por_unidad_vendida": float(costo.sum() / total),
        "unidades_faltantes": float(faltante.sum()),
        "unidades_sobrantes": float(sobrante.sum()),
    }


def cortes_backtest(fecha_final: date, panel_final: Panel, params: dict[str, Any]) -> list[date]:
    """Fechas de corte pasadas (día de pedido) con el horizonte completo ya observado."""
    horizonte = params["calendario"]["horizonte_dias"]
    ultimo = min(panel_final.ultima_fecha_ventas, fecha_final) - timedelta(days=horizonte - 1)
    while ultimo.weekday() != params["calendario"]["dia_pedido"]:
        ultimo -= timedelta(days=1)
    cfg = params["validacion_temporal"]
    paso = timedelta(days=cfg["dias_entre_cortes"])
    return [ultimo - k * paso for k in range(cfg["n_cortes"])][::-1]


def backtest(
    historico: Path,
    params: dict[str, Any],
    fecha_final: date,
    modelos: tuple[str, ...] = ("baseline", "lightgbm"),
    vigente=None,
) -> pd.DataFrame:
    """Evalúa cada modelo en los mismos cortes, con la información disponible en cada uno.

    `vigente` (opcional) es un modelo ya entrenado que se evalúa tal cual, sin reajustar.
    """
    horizonte = params["calendario"]["horizonte_dias"]
    fuentes_final = cargar_fuentes(historico, fecha_final)
    cortes = cortes_backtest(fecha_final, Panel(fuentes_final, params), params)

    filas = []
    for corte in cortes:
        panel = Panel(cargar_fuentes(historico, corte), params)
        candidatos = {n: crear(n, params).ajustar(panel, corte, horizonte) for n in modelos}
        if vigente is not None:
            candidatos["vigente"] = vigente
        for nombre, modelo in candidatos.items():
            pred = modelo.predecir(panel, corte, horizonte)
            met = evaluar(pred, fuentes_final.ventas, params)
            filas.append({"modelo": nombre, "corte": corte.isoformat(), **met})
            log.info("Backtest %s %s: %s", nombre, corte, met)
    return pd.DataFrame(filas)


def resumir(resultados: pd.DataFrame) -> pd.DataFrame:
    return resultados.groupby("modelo").agg(
        cortes=("corte", "count"),
        wape_p50=("wape_p50", "mean"),
        pinball_rel=("pinball_rel", "mean"),
        cobertura_intervalo=("cobertura_intervalo", "mean"),
        costo_negocio=("costo_negocio", "sum"),
        costo_por_unidad_vendida=("costo_por_unidad_vendida", "mean"),
    )


def decidir_promocion(
    resumen: pd.DataFrame, candidato: str, vigente: str, mejora_minima: float
) -> tuple[bool, str]:
    """El candidato reemplaza al vigente si reduce el costo de negocio al menos `mejora_minima`
    en el mismo backtest."""
    c, v = resumen.loc[candidato, "costo_negocio"], resumen.loc[vigente, "costo_negocio"]
    mejora = 1 - c / v
    reemplaza = mejora >= mejora_minima
    motivo = (
        f"{candidato} {'reemplaza a' if reemplaza else 'no reemplaza a'} {vigente}: "
        f"costo {c:,.0f} vs {v:,.0f} (mejora {mejora:.1%}, mínimo exigido {mejora_minima:.0%})"
    )
    return bool(reemplaza), motivo


# --------------------------------------------------------------------------- reporte


def reporte_markdown(resultados: pd.DataFrame, resumen: pd.DataFrame, decision: str) -> str:
    def tabla(df: pd.DataFrame) -> str:
        df = df.reset_index()
        cab = "| " + " | ".join(df.columns) + " |"
        sep = "|" + "---|" * len(df.columns)
        filas = [
            "| " + " | ".join(f"{v:,.4f}" if isinstance(v, float) else str(v) for v in r) + " |"
            for r in df.itertuples(index=False)
        ]
        return "\n".join([cab, sep, *filas])

    return "\n\n".join(
        [
            "# Comparación de modelos (backtest temporal)",
            "## Resumen por modelo",
            tabla(resumen),
            "## Detalle por corte",
            tabla(resultados.set_index(["modelo", "corte"])),
            "## Decisión",
            decision,
        ]
    )


def main(argv: list[str] | None = None) -> None:
    from reposicion.entorno import contexto

    parser = argparse.ArgumentParser(description="Backtest y comparación de modelos.")
    parser.add_argument("--fecha-final", type=date.fromisoformat, required=True)
    parser.add_argument("--salida", type=Path, default=Path("reportes"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    params, rutas = contexto()
    resultados = backtest(rutas["historico"], params, args.fecha_final)
    resumen = resumir(resultados)
    _, decision = decidir_promocion(
        resumen, "lightgbm", "baseline", params["promocion_modelo"]["mejora_minima_costo"]
    )
    args.salida.mkdir(parents=True, exist_ok=True)
    resumen_json = {"resumen": resumen.reset_index().to_dict("records"), "decision": decision}
    (args.salida / "comparacion_modelos.json").write_text(
        json.dumps(resumen_json, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.salida / "comparacion_modelos.md").write_text(
        reporte_markdown(resultados, resumen, decision) + "\n", encoding="utf-8"
    )
    print(resumen.to_string())
    print(decision)


if __name__ == "__main__":
    main()
