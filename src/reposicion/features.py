"""Construye la tabla de modelado usando solo información disponible a la fecha de corte.

Un *origen* O es el día en que se emite un pronóstico; el horizonte h = 1..H cubre las
fechas O .. O+H-1. Para cada origen solo se usan datos que ya habían llegado en O, según
las latencias de `params.yaml`: ventas hasta O-1, transacciones hasta O-2, petróleo hasta
O-1, promociones hasta O+14 y feriados de los meses ya publicados. La misma regla se
aplica a los orígenes de entrenamiento, de modo que el modelo aprende con la misma
información que tendrá al predecir.
"""

from __future__ import annotations

import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from reposicion.ingesta import escribir_json, escribir_parquet, vista

TIPOS_FERIADO = {"Holiday", "Transfer", "Additional", "Bridge"}
CATEGORICAS = ["store_nbr", "family", "tipo_tienda", "cluster", "ciudad"]


@contextmanager
def sin_avisos_nan():
    """Silencia 'Mean of empty slice': una ventana sin datos da NaN a propósito."""
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        yield


@dataclass
class Fuentes:
    ventas: pd.DataFrame
    promociones: pd.DataFrame
    transacciones: pd.DataFrame
    petroleo: pd.DataFrame
    feriados: pd.DataFrame
    tiendas: pd.DataFrame


def cargar_fuentes(historico: Path, fecha_corte: date) -> Fuentes:
    """Lo que se sabía de cada fuente en `fecha_corte` (ver `ingesta.vista`)."""
    v = {f: vista(historico, f, fecha_corte) for f in Fuentes.__dataclass_fields__}
    if v["ventas"].empty or v["tiendas"].empty:
        raise FileNotFoundError(f"No hay ventas o tiendas ingeridas al {fecha_corte}.")
    return Fuentes(**v)


def preparar_datos(
    historico: Path, procesado: Path, params: dict[str, Any], fecha_corte: date
) -> dict[str, Any]:
    """Prepara los datos del modelo para una fecha de corte.

    Consolida la vista point-in-time de cada fuente (última versión de cada dato llegado
    hasta la fecha de corte, con las correcciones aplicadas) y la recorta a la ventana que
    usa el pronóstico. Resultado: `data/procesado/fecha_corte=<fecha>/<fuente>.parquet`.
    """
    fuentes = cargar_fuentes(historico, fecha_corte)
    desde = (fecha_corte - timedelta(days=params["preparacion"]["ventana_dias"])).isoformat()
    destino = procesado / f"fecha_corte={fecha_corte.isoformat()}"
    filas = {}
    for nombre in Fuentes.__dataclass_fields__:
        df = getattr(fuentes, nombre)
        if "date" in df and nombre != "feriados":
            df = df[df["date"] >= desde]
        escribir_parquet(df.reset_index(drop=True), destino / f"{nombre}.parquet")
        filas[nombre] = len(df)
    resumen = {
        "fecha_corte": fecha_corte.isoformat(),
        "desde": desde,
        "ultima_fecha_ventas": fuentes.ventas["date"].max(),
        "filas": filas,
    }
    escribir_json(resumen, destino / "resumen.json")
    return resumen


def cargar_preparados(procesado: Path, fecha_corte: date) -> Fuentes:
    carpeta = procesado / f"fecha_corte={fecha_corte.isoformat()}"
    if not (carpeta / "resumen.json").is_file():
        raise FileNotFoundError(f"Sin datos preparados para {fecha_corte}: falta preparar_datos.")
    nombres = Fuentes.__dataclass_fields__
    return Fuentes(**{n: pd.read_parquet(carpeta / f"{n}.parquet") for n in nombres})


def _matriz(df: pd.DataFrame, valor: str, columnas: list[str], indice_cols) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=indice_cols, dtype=float)
    m = df.pivot_table(index="date", columns=columnas, values=valor, aggfunc="first")
    m.index = pd.to_datetime(m.index)
    return m.reindex(columns=indice_cols).astype(float)


class Panel:
    """Las fuentes como matrices fecha × serie, listas para cortar por origen."""

    def __init__(self, fuentes: Fuentes, params: dict[str, Any]):
        self.latencias = params["bandeja"]["latencias_dias"]
        self.anticipacion_promos = params["bandeja"]["promociones_anticipacion_dias"]
        self.dia_publicacion_feriados = params["bandeja"]["feriados_dia_publicacion"]

        tiendas = fuentes.tiendas.copy()
        tiendas["store_nbr"] = tiendas["store_nbr"].astype(int)
        self.tiendas = tiendas.set_index("store_nbr").sort_index()
        familias = sorted(fuentes.ventas["family"].unique())
        self.series = pd.MultiIndex.from_product(
            [self.tiendas.index, familias], names=["store_nbr", "family"]
        )

        ventas = fuentes.ventas.assign(store_nbr=fuentes.ventas["store_nbr"].astype(int))
        self.ventas = _matriz(ventas, "sales", ["store_nbr", "family"], self.series)
        self.ventas = self.ventas.asfreq("D")  # días sin datos (25-dic) quedan como NaN
        # Una tienda que vende 0 en todas sus familias estuvo cerrada (1-ene, cierres, aún sin
        # abrir): no es demanda cero, es un día sin dato.
        por_tienda = self.ventas.T.groupby(level="store_nbr").sum(min_count=1).T
        cerrada = (por_tienda == 0).reindex(columns=self.series.get_level_values(0))
        self.ventas = self.ventas.mask(cerrada.to_numpy())

        promos = fuentes.promociones
        if not promos.empty:
            promos = promos.assign(store_nbr=promos["store_nbr"].astype(int))
        self.promos = _matriz(promos, "onpromotion", ["store_nbr", "family"], self.series)

        trans = fuentes.transacciones
        if not trans.empty:
            trans = trans.assign(store_nbr=trans["store_nbr"].astype(int))
        self.trans = _matriz(trans, "transactions", ["store_nbr"], self.tiendas.index)

        oil = fuentes.petroleo
        self.oil = (
            pd.Series(oil["dcoilwtico"].astype(float).values, index=pd.to_datetime(oil["date"]))
            .sort_index()
            if not oil.empty
            else pd.Series(dtype=float)
        )
        self.feriados = fuentes.feriados.copy()

        tienda_de_serie = self.series.get_level_values("store_nbr")
        self.ciudad = self.tiendas.loc[tienda_de_serie, "city"].to_numpy()
        self.estado = self.tiendas.loc[tienda_de_serie, "state"].to_numpy()

    @property
    def ultima_fecha_ventas(self) -> date:
        return self.ventas.dropna(how="all").index.max().date()

    # ----------------------------------------------------------------- consultas puntuales

    def historia(self, origen: date) -> np.ndarray:
        """Ventas conocidas en `origen` (filas = días, columnas = series)."""
        limite = pd.Timestamp(origen - timedelta(days=self.latencias["ventas"]))
        return self.ventas.loc[:limite].to_numpy()

    def mismo_dia(self, origen: date, objetivo: date, n: int) -> np.ndarray:
        """Las últimas `n` ventas conocidas del mismo día de la semana que `objetivo`."""
        limite = origen - timedelta(days=self.latencias["ventas"])
        salto = max(1, -(-(objetivo - limite).days // 7))
        fechas = [pd.Timestamp(objetivo - timedelta(days=7 * (salto + j))) for j in range(n)]
        return self.ventas.reindex(fechas).to_numpy()

    def _feriados_en(self, origen: date, objetivo: date) -> tuple[np.ndarray, ...]:
        """Indicadores de feriado nacional/regional/local y evento, si ya estaban publicados."""
        s = len(self.series)
        mes = date(objetivo.year, objetivo.month, 1)
        publicacion = (mes - timedelta(days=1)).replace(day=self.dia_publicacion_feriados)
        if publicacion > origen or self.feriados.empty:
            nan = np.full(s, np.nan)
            return nan, nan, nan, nan
        dia = self.feriados[self.feriados["date"] == objetivo.isoformat()]
        feriado = dia[dia["type"].isin(TIPOS_FERIADO) & ~dia["transferred"].astype(bool)]
        nacional = (feriado["locale"] == "National").any()
        regional = np.isin(self.estado, feriado.loc[feriado["locale"] == "Regional", "locale_name"])
        local = np.isin(self.ciudad, feriado.loc[feriado["locale"] == "Local", "locale_name"])
        evento = ((dia["type"] == "Event") & (dia["locale"] == "National")).any()
        return (
            np.full(s, float(nacional)),
            regional.astype(float),
            local.astype(float),
            np.full(s, float(evento)),
        )

    # ----------------------------------------------------------------- tabla de modelado

    def filas(self, origen: date, horizonte: int, con_objetivo: bool = False) -> pd.DataFrame:
        """Una fila por serie y día del horizonte, con features calculadas en `origen`."""
        h_ = self.historia(origen)
        if len(h_) < 56:
            raise ValueError(f"Historia insuficiente al {origen}: {len(h_)} días.")
        with sin_avisos_nan():
            base = {
                "ultimo": h_[-1],
                "media_7": np.nanmean(h_[-7:], axis=0),
                "media_14": np.nanmean(h_[-14:], axis=0),
                "media_28": np.nanmean(h_[-28:], axis=0),
                "media_56": np.nanmean(h_[-56:], axis=0),
                "std_28": np.nanstd(h_[-28:], axis=0),
                "ceros_28": np.nanmean(h_[-28:] == 0, axis=0),
            }
            base["tendencia"] = base["media_7"] / (base["media_28"] + 1)

        limite = pd.Timestamp(origen - timedelta(days=self.latencias["ventas"]))
        promo_hist = self.promos.loc[:limite].tail(28).to_numpy()
        base["promo_media_28"] = (
            np.nanmean(promo_hist, axis=0) if len(promo_hist) else np.full(len(self.series), np.nan)
        )

        lim_trans = pd.Timestamp(origen - timedelta(days=self.latencias["transacciones"]))
        trans = self.trans.loc[:lim_trans].tail(28).mean()
        base["transacciones_28"] = trans.reindex(self.series.get_level_values(0)).to_numpy()

        lim_oil = pd.Timestamp(origen - timedelta(days=self.latencias["petroleo"]))
        oil = self.oil.loc[:lim_oil].dropna()
        oil_ultimo = oil.iloc[-1] if len(oil) else np.nan
        oil_previo = oil.loc[: lim_oil - pd.Timedelta(days=28)]
        base["petroleo"] = np.full(len(self.series), oil_ultimo)
        base["petroleo_cambio_28"] = np.full(
            len(self.series), oil_ultimo / oil_previo.iloc[-1] - 1 if len(oil_previo) else np.nan
        )

        estaticas = pd.DataFrame(
            {
                "store_nbr": self.series.get_level_values("store_nbr"),
                "family": self.series.get_level_values("family"),
                "tipo_tienda": self.tiendas.loc[self.series.get_level_values(0), "type"].values,
                "cluster": self.tiendas.loc[self.series.get_level_values(0), "cluster"].values,
                "ciudad": self.ciudad,
            }
        )

        bloques = []
        for h in range(1, horizonte + 1):
            objetivo = origen + timedelta(days=h - 1)
            mismo = self.mismo_dia(origen, objetivo, 4)
            with sin_avisos_nan():
                mismo_media = np.nanmean(mismo, axis=0)
            promo_conocida = (objetivo - origen).days <= self.anticipacion_promos
            promo = (
                self.promos.reindex([pd.Timestamp(objetivo)]).to_numpy()[0]
                if promo_conocida
                else np.full(len(self.series), np.nan)
            )
            nacional, regional, local, evento = self._feriados_en(origen, objetivo)
            ultimo_dia_mes = (objetivo + timedelta(days=1)).day == 1
            bloque = estaticas.assign(
                date=objetivo.isoformat(),
                h=h,
                dia_semana=objetivo.weekday(),
                dia_mes=objetivo.day,
                dia_pago=float(objetivo.day == 15 or ultimo_dia_mes),
                mismo_dia_ultimo=mismo[0],
                mismo_dia_media_4=mismo_media,
                promo=promo,
                feriado_nacional=nacional,
                feriado_regional=regional,
                feriado_local=local,
                evento_nacional=evento,
                **base,
            )
            if con_objetivo:
                bloque["y"] = self.ventas.reindex([pd.Timestamp(objetivo)]).to_numpy()[0]
            bloques.append(bloque)

        return pd.concat(bloques, ignore_index=True).assign(fecha_corte=origen.isoformat())


def origenes_entrenamiento(
    fecha_corte: date, panel: Panel, horizonte: int, cfg: dict[str, Any]
) -> list[date]:
    """Orígenes pasados cuyo horizonte completo ya era conocido en `fecha_corte`."""
    conocido = min(
        fecha_corte - timedelta(days=panel.latencias["ventas"]), panel.ultima_fecha_ventas
    )
    ultimo = conocido - timedelta(days=horizonte - 1)
    paso = timedelta(days=cfg["dias_entre_origenes"])
    return [ultimo - k * paso for k in range(cfg["origenes"])][::-1]
