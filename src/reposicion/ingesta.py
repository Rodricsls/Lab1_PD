"""Lee la bandeja (solo lectura) y registra cada archivo con su fecha de llegada.

El histórico refleja la bandeja ya validada, particionado igual que ella:

    data/historico/carga_inicial/<fuente>.parquet
    data/historico/<fuente>/llegada=AAAA-MM-DD/<fuente>_<clave>.parquet
    data/historico/_reportes/llegada=AAAA-MM-DD/<fuente>.json

Cada ingesta diaria procesa solo la carpeta `llegada=<fecha>` de una fuente y reemplaza
completa su partición: repetirla da el mismo resultado y los días son independientes, de
modo que un backfill puede correr fechas en paralelo. `vista` reconstruye lo que se sabía
en una fecha de corte: solo particiones con llegada ≤ corte y, por clave, la más reciente.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from reposicion.bandeja import CLAVE_CARGA_INICIAL, leer_manifiesto
from reposicion.validacion import DatosInvalidos, Referencia, validar

log = logging.getLogger(__name__)

FUENTES_DIARIAS = ["ventas", "transacciones", "petroleo", "promociones", "feriados"]
CARGA_INICIAL = "carga_inicial"
MARCA = "_ok.json"
# Fuentes con una entrega por fecha del dato y su latencia en params (aviso si no llega).
CON_LATENCIA = ["ventas", "transacciones", "petroleo"]


# --------------------------------------------------------------------------- utilidades


def escribir_parquet(df: pd.DataFrame, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(f"{ruta.name}.{uuid.uuid4().hex}.tmp")
    df.to_parquet(temporal, index=False)
    os.replace(temporal, ruta)


def escribir_json(datos: Any, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(f"{ruta.name}.{uuid.uuid4().hex}.tmp")
    temporal.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporal, ruta)


def reemplazar_particion(archivos: dict[str, pd.DataFrame], destino: Path) -> None:
    """Escribe la partición completa en una carpeta temporal y la pone en su lugar."""
    temporal = destino.with_name(f"{destino.name}.{uuid.uuid4().hex}.tmp")
    temporal.mkdir(parents=True)
    for nombre, df in archivos.items():
        df.to_parquet(temporal / nombre, index=False)
    if destino.exists():
        shutil.rmtree(destino)
    os.replace(temporal, destino)


def clave_de(fuente: str, fechas: pd.Series) -> pd.Series:
    return fechas.str[:7] if fuente == "feriados" else fechas


# --------------------------------------------------------------------------- carga inicial


def fechas_de_llegada(cruda: pd.DataFrame, fuente: str, corte: str) -> pd.Series:
    """Fecha de llegada original de cada registro de la carga inicial (≤ corte)."""
    if "fecha_llegada" not in cruda:
        return pd.Series(corte, index=cruda.index)
    llegada = pd.to_datetime(cruda["fecha_llegada"], format="%Y-%m-%d", errors="coerce")
    malas = llegada.isna() | (llegada > pd.Timestamp(corte))
    if malas.any():
        raise DatosInvalidos(
            fuente, "carga_inicial", [f"fecha_llegada: {malas.sum()} valores inválidos o > {corte}"]
        )
    return llegada.dt.strftime("%Y-%m-%d")


def ingerir_carga_inicial(bandeja: Path, historico: Path) -> dict[str, Any]:
    """Valida y copia la carga inicial. Se omite si ya se ingirió esa misma entrega."""
    manifiesto = leer_manifiesto(bandeja)
    entregada = manifiesto[manifiesto["clave"] == CLAVE_CARGA_INICIAL]
    if entregada.empty:
        raise FileNotFoundError(f"No hay carga inicial en {bandeja}: ¿corrió bandeja-init?")
    huellas = dict(zip(entregada["fuente"], entregada["huella"], strict=True))
    corte = entregada["llegada"].iloc[0]

    destino = historico / CARGA_INICIAL
    marca = destino / MARCA
    if marca.is_file() and json.loads(marca.read_text())["huellas"] == huellas:
        return {"omitido": True, "corte": corte}

    origen = bandeja / CLAVE_CARGA_INICIAL
    tiendas = validar("tiendas", pd.read_csv(origen / "tiendas.csv"), "carga_inicial").datos
    crudas = {"ventas": pd.read_csv(origen / "ventas.csv")}
    ventas = validar("ventas", crudas["ventas"], "carga_inicial").datos
    referencia = Referencia(
        tiendas=frozenset(int(t) for t in tiendas["store_nbr"]),
        familias=frozenset(ventas["family"]),
    )
    tablas = {"ventas": ventas}
    for fuente in FUENTES_DIARIAS:
        if fuente != "ventas" and (origen / f"{fuente}.csv").is_file():
            crudas[fuente] = pd.read_csv(origen / f"{fuente}.csv")
            tablas[fuente] = validar(
                fuente, crudas[fuente], "carga_inicial", referencia=referencia
            ).datos

    # Las tiendas son datos maestros: se consideran conocidas desde el inicio.
    escribir_parquet(tiendas, destino / "tiendas.parquet")
    filas = {"tiendas": len(tiendas)}
    for fuente, df in tablas.items():
        llegada = fechas_de_llegada(crudas[fuente], fuente, corte)
        df = df.assign(_clave=clave_de(fuente, df["date"]), fecha_llegada=llegada)
        escribir_parquet(df, destino / f"{fuente}.parquet")
        filas[fuente] = len(df)

    escribir_json(
        {
            "corte": corte,
            "huellas": huellas,
            "tiendas": sorted(referencia.tiendas),
            "familias": sorted(referencia.familias),
        },
        marca,
    )
    log.info("Carga inicial ingerida: %s", filas)
    return {"omitido": False, "corte": corte, "filas": filas}


def cargar_referencia(historico: Path) -> Referencia:
    marca = historico / CARGA_INICIAL / MARCA
    if not marca.is_file():
        raise FileNotFoundError("La carga inicial no se ha ingerido todavía.")
    datos = json.loads(marca.read_text())
    return Referencia(tiendas=frozenset(datos["tiendas"]), familias=frozenset(datos["familias"]))


# --------------------------------------------------------------------------- ingesta diaria


def ya_llego(historico: Path, fuente: str, clave: str, hasta: date) -> bool:
    """¿Alguna partición con llegada ≤ `hasta` (o la carga inicial) trae esta clave?"""
    for particion in (historico / fuente).glob(f"llegada=*/{fuente}_{clave}.parquet"):
        if date.fromisoformat(particion.parent.name.removeprefix("llegada=")) <= hasta:
            return True
    return False


def ingerir_dia(
    fuente: str, fecha: date, bandeja: Path, historico: Path, params: dict[str, Any]
) -> dict[str, Any]:
    """Ingiere lo que llegó en `fecha` para una fuente.

    Los archivos válidos se registran aunque otro archivo del mismo día sea inválido; al
    final, si hubo inválidos, se lanza `DatosInvalidos` para que la tarea quede en rojo.
    """
    referencia = cargar_referencia(historico)
    origen = bandeja / fuente / f"llegada={fecha.isoformat()}"
    destino = historico / fuente / f"llegada={fecha.isoformat()}"
    reporte: dict[str, Any] = {
        "fuente": fuente,
        "llegada": fecha.isoformat(),
        "archivos": [],
        "correcciones": [],
        "invalidos": [],
        "avisos": [],
    }

    validos: dict[str, pd.DataFrame] = {}
    for archivo in sorted(origen.glob("*.csv")) if origen.is_dir() else []:
        clave = archivo.stem.removeprefix(f"{fuente}_")
        try:
            resultado = validar(fuente, pd.read_csv(archivo), archivo.name, clave, referencia)
        except DatosInvalidos as e:
            reporte["invalidos"].append({"archivo": archivo.name, "problemas": e.problemas})
            continue
        reporte["archivos"].append(archivo.name)
        reporte["avisos"] += [f"{archivo.name}: {a}" for a in resultado.avisos]
        if ya_llego(historico, fuente, clave, fecha - timedelta(days=1)):
            reporte["correcciones"].append(clave)
        validos[f"{fuente}_{clave}.parquet"] = resultado.datos.assign(
            _clave=clave, fecha_llegada=fecha.isoformat()
        )

    if not reporte["archivos"] and not reporte["invalidos"]:
        reporte["avisos"].append("no llegó ningún archivo")

    if fuente in CON_LATENCIA:
        esperada = fecha - timedelta(days=params["bandeja"]["latencias_dias"][fuente])
        habil = fuente != "petroleo" or esperada.weekday() < 5
        if habil and not ya_llego(historico, fuente, esperada.isoformat(), fecha) and (
            f"{fuente}_{esperada.isoformat()}.parquet" not in validos
        ):
            reporte["avisos"].append(f"aún no llegan datos del {esperada.isoformat()}")

    if validos:
        reemplazar_particion(validos, destino)
    elif destino.exists():
        shutil.rmtree(destino)  # la partición refleja exactamente lo que hay en la bandeja

    carpeta_reporte = historico / "_reportes" / f"llegada={fecha.isoformat()}"
    escribir_json(reporte, carpeta_reporte / f"{fuente}.json")
    for aviso in reporte["avisos"]:
        log.warning("%s %s · %s", fuente, fecha, aviso)

    if reporte["invalidos"]:
        detalle = [f"{i['archivo']}: {', '.join(i['problemas'])}" for i in reporte["invalidos"]]
        raise DatosInvalidos(fuente, f"llegada={fecha.isoformat()}", detalle)

    return {
        "archivos": len(reporte["archivos"]),
        "filas": int(sum(len(df) for df in validos.values())),
        "correcciones": len(reporte["correcciones"]),
        "avisos": len(reporte["avisos"]),
    }


def resumir_dia(fecha: date, historico: Path) -> dict[str, Any]:
    carpeta = historico / "_reportes" / f"llegada={fecha.isoformat()}"
    resumen = {}
    for ruta in sorted(carpeta.glob("*.json")) if carpeta.is_dir() else []:
        r = json.loads(ruta.read_text(encoding="utf-8"))
        resumen[r["fuente"]] = {
            "archivos": len(r["archivos"]),
            "correcciones": len(r["correcciones"]),
            "invalidos": len(r["invalidos"]),
            "avisos": len(r["avisos"]),
        }
    return resumen


# --------------------------------------------------------------------------- lectura


def vista(historico: Path, fuente: str, fecha_corte: date) -> pd.DataFrame:
    """Lo que se sabía de una fuente en `fecha_corte`: la última versión de cada clave."""
    partes = []
    inicial = historico / CARGA_INICIAL / f"{fuente}.parquet"
    if inicial.is_file():
        df = pd.read_parquet(inicial)
        if "fecha_llegada" in df:
            df = df[df["fecha_llegada"] <= fecha_corte.isoformat()]
        partes.append(df)
    for particion in sorted((historico / fuente).glob("llegada=*")):
        if not particion.is_dir() or particion.name.endswith(".tmp"):
            continue
        if date.fromisoformat(particion.name.removeprefix("llegada=")) <= fecha_corte:
            partes += [pd.read_parquet(p) for p in sorted(particion.glob("*.parquet"))]

    if not partes:
        return pd.DataFrame()
    df = pd.concat(partes, ignore_index=True)
    if "_clave" in df:
        ultima = df.groupby("_clave")["fecha_llegada"].transform("max")
        df = df[df["fecha_llegada"] == ultima].drop(columns="_clave")
    return df.reset_index(drop=True)
