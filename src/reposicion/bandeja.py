"""Genera la carga histórica y la bandeja simulada a partir de los CSV de Kaggle.

La bandeja es el sistema de origen de la empresa. Se organiza por fecha de llegada:

    data/bandeja/carga_inicial/<fuente>.csv                      lo llegado hasta el corte
    data/bandeja/<fuente>/llegada=AAAA-MM-DD/<fuente>_<clave>.csv  lo que llega cada día

Una corrección es el mismo archivo (misma fuente y clave) en una carpeta de llegada
posterior: la llegada más reciente es la válida. El manifiesto `_manifiesto.csv` registra
cada versión entregada, lo que hace la generación incremental: volver a correr con los
mismos CSV no cambia nada; si un CSV cambió, solo se entregan las versiones corregidas.

Este módulo es el único que escribe en la bandeja. El flujo de Airflow solo la lee.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from reposicion.config import cargar_params
from reposicion.entorno import raiz_proyecto

log = logging.getLogger(__name__)

COLUMNAS_KAGGLE = {
    "train": ["date", "store_nbr", "family", "sales", "onpromotion"],
    "transactions": ["date", "store_nbr", "transactions"],
    "oil": ["date", "dcoilwtico"],
    "holidays_events": ["date", "type", "locale", "locale_name", "description", "transferred"],
    "stores": ["store_nbr", "city", "state", "type", "cluster"],
}
COLUMNAS_MANIFIESTO = ["fuente", "clave", "version", "llegada", "ruta", "huella"]
CLAVE_CARGA_INICIAL = "carga_inicial"
MANIFIESTO = "_manifiesto.csv"
ESTADO_KAGGLE = "_kaggle.json"


class KaggleIncompleto(ValueError):
    """Faltan archivos o columnas de Kaggle, o sus fechas no se pueden interpretar."""


@dataclass(frozen=True)
class Entrega:
    fuente: str
    clave: str  # fecha del dato (AAAA-MM-DD) o mes (AAAA-MM) para feriados
    llegada: date
    datos: pd.DataFrame


# --------------------------------------------------------------------------- lectura


def verificar_archivos(carpeta: Path) -> None:
    faltan = [n for n in COLUMNAS_KAGGLE if not (carpeta / f"{n}.csv").is_file()]
    if faltan:
        raise KaggleIncompleto(f"Faltan en {carpeta}: {', '.join(f + '.csv' for f in faltan)}")


def leer_kaggle(carpeta: Path) -> dict[str, pd.DataFrame]:
    verificar_archivos(carpeta)
    tablas = {}
    for nombre, columnas in COLUMNAS_KAGGLE.items():
        df = pd.read_csv(carpeta / f"{nombre}.csv")
        ausentes = set(columnas) - set(df.columns)
        if ausentes:
            raise KaggleIncompleto(f"{nombre}.csv no tiene las columnas {sorted(ausentes)}")
        df = df[columnas].copy()
        if "date" in df:
            try:
                df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d").dt.strftime("%Y-%m-%d")
            except ValueError as e:
                raise KaggleIncompleto(f"{nombre}.csv tiene fechas inválidas: {e}") from e
        tablas[nombre] = df
    return tablas


def huella_archivos(carpeta: Path) -> dict[str, str]:
    """MD5 de cada CSV de Kaggle: permite saltar la generación si nada cambió."""
    huellas = {}
    for nombre in COLUMNAS_KAGGLE:
        h = hashlib.md5()
        with open(carpeta / f"{nombre}.csv", "rb") as f:
            for bloque in iter(lambda: f.read(1 << 20), b""):
                h.update(bloque)
        huellas[nombre] = h.hexdigest()
    return huellas


# --------------------------------------------------------------------------- entregas


def entregas_diarias(df: pd.DataFrame, fuente: str, desfase_dias: int) -> Iterator[Entrega]:
    """Un archivo por fecha del dato, que llega `desfase_dias` después (o antes, si < 0)."""
    for fecha, grupo in df.groupby("date", sort=True):
        llegada = date.fromisoformat(fecha) + timedelta(days=desfase_dias)
        yield Entrega(fuente, fecha, llegada, grupo.reset_index(drop=True))


def entregas_feriados(df: pd.DataFrame, dia_publicacion: int) -> Iterator[Entrega]:
    """Un archivo por mes, publicado el `dia_publicacion` del mes anterior."""
    for mes, grupo in df.groupby(df["date"].str[:7], sort=True):
        ultimo_mes_anterior = date.fromisoformat(f"{mes}-01") - timedelta(days=1)
        llegada = ultimo_mes_anterior.replace(day=dia_publicacion)
        yield Entrega("feriados", mes, llegada, grupo.reset_index(drop=True))


def construir_entregas(tablas: dict[str, pd.DataFrame], cfg: dict[str, Any]) -> list[Entrega]:
    latencias = cfg["latencias_dias"]
    train = tablas["train"]
    entregas: list[Entrega] = []
    entregas += entregas_diarias(
        train[["date", "store_nbr", "family", "sales"]], "ventas", latencias["ventas"]
    )
    entregas += entregas_diarias(
        train[["date", "store_nbr", "family", "onpromotion"]],
        "promociones",
        -cfg["promociones_anticipacion_dias"],
    )
    entregas += entregas_diarias(
        tablas["transactions"], "transacciones", latencias["transacciones"]
    )
    entregas += entregas_diarias(tablas["oil"], "petroleo", latencias["petroleo"])
    entregas += entregas_feriados(tablas["holidays_events"], cfg["feriados_dia_publicacion"])
    return entregas


# --------------------------------------------------------------------------- correcciones


def simular_error_de_entrega(
    entrega: Entrega, cfg_corr: dict[str, Any], semilla: int
) -> tuple[pd.DataFrame, int] | None:
    """Decide si una entrega de ventas llega primero con errores.

    Devuelve (versión con errores, días hasta la corrección) o None. La decisión depende
    solo de la semilla y de la fecha, no del orden de ejecución.
    """
    if entrega.fuente != "ventas" or not cfg_corr["simular"]:
        return None
    rng = np.random.default_rng([semilla, date.fromisoformat(entrega.clave).toordinal()])
    if rng.random() >= cfg_corr["fraccion_archivos"]:
        return None

    retraso = int(rng.integers(cfg_corr["retraso_min_dias"], cfg_corr["retraso_max_dias"] + 1))
    erronea = entrega.datos.copy()
    filas = rng.random(len(erronea)) < cfg_corr["fraccion_filas"]
    if not filas.any():
        filas[rng.integers(len(erronea))] = True
    factores = rng.uniform(0.6, 1.4, filas.sum())
    erronea.loc[filas, "sales"] = (erronea.loc[filas, "sales"] * factores).round(3)
    return erronea, retraso


# --------------------------------------------------------------------------- escritura


def huella(df: pd.DataFrame) -> str:
    return hashlib.md5(df.to_csv(index=False).encode()).hexdigest()


def escribir_csv(df: pd.DataFrame, ruta: Path) -> None:
    """Escritura atómica: un lector nunca ve un archivo a medias."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + ".tmp")
    df.to_csv(temporal, index=False)
    os.replace(temporal, ruta)


def ruta_entrega(fuente: str, clave: str, llegada: date) -> str:
    return f"{fuente}/llegada={llegada.isoformat()}/{fuente}_{clave}.csv"


def leer_manifiesto(bandeja: Path) -> pd.DataFrame:
    ruta = bandeja / MANIFIESTO
    if not ruta.is_file():
        return pd.DataFrame(columns=COLUMNAS_MANIFIESTO)
    return pd.read_csv(ruta, dtype={"clave": str, "llegada": str, "huella": str})


class Generador:
    def __init__(self, bandeja: Path, manifiesto: pd.DataFrame):
        self.bandeja = bandeja
        self.registros = manifiesto.to_dict("records")
        self.ultima = {}
        for r in self.registros:
            k = (r["fuente"], r["clave"])
            if k not in self.ultima or r["version"] > self.ultima[k]["version"]:
                self.ultima[k] = r
        self.conteo = {"nuevas": 0, "errores_simulados": 0, "correcciones": 0, "sin_cambios": 0}

    def entregar(self, fuente: str, clave: str, llegada: date, datos: pd.DataFrame) -> None:
        previa = self.ultima.get((fuente, clave))
        version = 1 if previa is None else int(previa["version"]) + 1
        ruta = ruta_entrega(fuente, clave, llegada)
        escribir_csv(datos, self.bandeja / ruta)
        registro = {
            "fuente": fuente,
            "clave": clave,
            "version": version,
            "llegada": llegada.isoformat(),
            "ruta": ruta,
            "huella": huella(datos),
        }
        self.registros.append(registro)
        self.ultima[(fuente, clave)] = registro

    def procesar(self, entrega: Entrega, cfg_corr: dict[str, Any], semilla: int) -> None:
        previa = self.ultima.get((entrega.fuente, entrega.clave))
        if previa is None:
            error = simular_error_de_entrega(entrega, cfg_corr, semilla)
            if error is None:
                self.entregar(entrega.fuente, entrega.clave, entrega.llegada, entrega.datos)
                self.conteo["nuevas"] += 1
            else:
                erronea, retraso = error
                self.entregar(entrega.fuente, entrega.clave, entrega.llegada, erronea)
                correccion = entrega.llegada + timedelta(days=retraso)
                self.entregar(entrega.fuente, entrega.clave, correccion, entrega.datos)
                self.conteo["errores_simulados"] += 1
        elif previa["huella"] != huella(entrega.datos):
            # El dato cambió en la fuente: se entrega una corrección, nunca se sobrescribe.
            ultima_llegada = date.fromisoformat(previa["llegada"])
            llegada = max(
                ultima_llegada + timedelta(days=1),
                entrega.llegada + timedelta(days=cfg_corr["retraso_deteccion_dias"]),
            )
            self.entregar(entrega.fuente, entrega.clave, llegada, entrega.datos)
            self.conteo["correcciones"] += 1
        else:
            self.conteo["sin_cambios"] += 1

    def guardar_manifiesto(self) -> None:
        df = pd.DataFrame(self.registros, columns=COLUMNAS_MANIFIESTO)
        df = df.sort_values(["fuente", "clave", "version"]).reset_index(drop=True)
        escribir_csv(df, self.bandeja / MANIFIESTO)


def escribir_carga_inicial(
    gen: Generador, iniciales: list[Entrega], tiendas: pd.DataFrame, corte: date
) -> None:
    por_fuente: dict[str, list[pd.DataFrame]] = {}
    for e in iniciales:
        por_fuente.setdefault(e.fuente, []).append(e.datos)
    tablas = {f: pd.concat(partes, ignore_index=True) for f, partes in por_fuente.items()}
    tablas["tiendas"] = tiendas
    for fuente, df in sorted(tablas.items()):
        ruta = f"{CLAVE_CARGA_INICIAL}/{fuente}.csv"
        escribir_csv(df, gen.bandeja / ruta)
        gen.registros.append(
            {
                "fuente": fuente,
                "clave": CLAVE_CARGA_INICIAL,
                "version": 1,
                "llegada": corte.isoformat(),
                "ruta": ruta,
                "huella": huella(df),
            }
        )


# --------------------------------------------------------------------------- principal


def generar_bandeja(kaggle: Path, bandeja: Path, params: dict[str, Any]) -> dict[str, Any]:
    cfg = params["bandeja"]
    corte = date.fromisoformat(str(cfg["corte_historico"]))
    bandeja.mkdir(parents=True, exist_ok=True)

    verificar_archivos(kaggle)
    huellas = huella_archivos(kaggle)
    estado = bandeja / ESTADO_KAGGLE
    if estado.is_file() and json.loads(estado.read_text()) == huellas:
        log.info("Los CSV de Kaggle no cambiaron desde la última generación: nada que hacer.")
        return {"omitido": True}

    tablas = leer_kaggle(kaggle)
    entregas = construir_entregas(tablas, cfg)
    gen = Generador(bandeja, leer_manifiesto(bandeja))

    if not any(clave == CLAVE_CARGA_INICIAL for _, clave in gen.ultima):
        iniciales = [e for e in entregas if e.llegada <= corte]
        escribir_carga_inicial(gen, iniciales, tablas["stores"], corte)
    else:
        log.info("La carga inicial ya fue entregada; no se modifica.")

    for entrega in entregas:
        if entrega.llegada > corte:
            gen.procesar(entrega, cfg["correcciones"], params["semilla"])

    gen.guardar_manifiesto()
    estado.write_text(json.dumps(huellas, indent=2))

    resumen = {
        "omitido": False,
        "corte_historico": corte.isoformat(),
        "ultima_fecha_ventas": tablas["train"]["date"].max(),
        **gen.conteo,
    }
    log.info("Bandeja generada: %s", resumen)
    return resumen


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raiz", type=Path, default=None, help="raíz del repositorio")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raiz = args.raiz or raiz_proyecto()
    params = cargar_params(raiz / "config" / "params.yaml")
    resumen = generar_bandeja(
        raiz / params["rutas"]["kaggle"], raiz / params["rutas"]["bandeja"], params
    )
    print(json.dumps(resumen, ensure_ascii=False))


if __name__ == "__main__":
    main()
