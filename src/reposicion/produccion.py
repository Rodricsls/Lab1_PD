"""Operación semanal: revisar y entrenar el modelo vigente, pronosticar y publicar.

Registro de modelos (`modelos/registro.json`): una entrada por fecha de corte en que se
entrenó, con el modelo elegido y la decisión. El modelo vigente en una fecha C es la
última entrada con fecha ≤ C, así que reprocesar una fecha pasada usa el mismo modelo que
se usó entonces, sin importar el orden de ejecución.

Publicación (`publicados/fecha_corte=C/`): `pronostico.parquet`, `pedido.parquet`,
`pedido_semana.csv` y `metadata.json` (modelo, commit de Git, costos). Cada archivo se
reemplaza completo: publicar dos veces la misma fecha deja el mismo resultado.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from reposicion.bandeja import leer_manifiesto
from reposicion.config import cuantil_critico
from reposicion.features import Panel, cargar_fuentes, cargar_preparados
from reposicion.ingesta import FUENTES_DIARIAS, MARCA, escribir_json, escribir_parquet
from reposicion.modelos import backtest, cargar, crear, decidir_promocion, guardar, resumir
from reposicion.pedido import calcular_pedido, pedido_semanal

log = logging.getLogger(__name__)

REGISTRO = "registro.json"


# --------------------------------------------------------------------------- disponibilidad


def dias_sin_ingerir(historico: Path, hasta: date) -> list[str]:
    """Fechas de llegada hasta `hasta` cuya ingesta aún no terminó para todas las fuentes."""
    marca = historico / "carga_inicial" / MARCA
    if not marca.is_file():
        return ["carga_inicial"]
    corte = date.fromisoformat(json.loads(marca.read_text())["corte"])
    faltan = []
    d = corte + timedelta(days=1)
    while d <= hasta:
        carpeta = historico / "_reportes" / f"llegada={d.isoformat()}"
        if not all((carpeta / f"{f}.json").is_file() for f in FUENTES_DIARIAS):
            faltan.append(d.isoformat())
        d += timedelta(days=1)
    return faltan


def ultima_llegada(bandeja: Path) -> date | None:
    """Última fecha de llegada que existe en la bandeja (el fin del periodo con datos)."""
    manifiesto = leer_manifiesto(bandeja)
    return date.fromisoformat(manifiesto["llegada"].max()) if len(manifiesto) else None


# --------------------------------------------------------------------------- registro


def leer_registro(dir_modelos: Path) -> list[dict[str, Any]]:
    ruta = dir_modelos / REGISTRO
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.is_file() else []


def vigente_al(dir_modelos: Path, fecha_corte: date) -> dict[str, Any] | None:
    previas = [e for e in leer_registro(dir_modelos) if e["fecha_corte"] <= fecha_corte.isoformat()]
    return max(previas, key=lambda e: e["fecha_corte"]) if previas else None


def toca_revisar(vigente: dict[str, Any] | None, fecha_corte: date, semanas: int) -> bool:
    if vigente is None:
        return True
    if vigente["fecha_corte"] == fecha_corte.isoformat():
        return False  # ya se entrenó en esta fecha: reprocesar no reentrena
    edad = (fecha_corte - date.fromisoformat(vigente["fecha_corte"])).days
    return edad >= 7 * semanas


def revisar_modelo(
    historico: Path, dir_modelos: Path, params: dict[str, Any], fecha_corte: date
) -> dict[str, Any]:
    """Si toca, compara modelos en backtest, elige con el criterio de reemplazo y entrena."""
    vigente = vigente_al(dir_modelos, fecha_corte)
    if not toca_revisar(vigente, fecha_corte, params["entrenamiento"]["reentrenar_cada_semanas"]):
        return {"revisado": False, "modelo": vigente["modelo"], "desde": vigente["fecha_corte"]}

    resultados = backtest(historico, params, fecha_corte)
    resumen = resumir(resultados)
    actual = vigente["modelo"] if vigente else "baseline"
    elegido, motivos = actual, []
    for candidato in resumen.index.drop(actual):
        reemplaza, motivo = decidir_promocion(
            resumen, candidato, actual, params["promocion_modelo"]["mejora_minima_costo"]
        )
        motivos.append(motivo)
        if reemplaza and resumen.loc[candidato, "costo_negocio"] < resumen.loc[
            elegido, "costo_negocio"
        ]:
            elegido = candidato

    horizonte = params["calendario"]["horizonte_dias"]
    panel = Panel(cargar_fuentes(historico, fecha_corte), params)
    modelo = crear(elegido, params).ajustar(panel, fecha_corte, horizonte)

    carpeta = dir_modelos / f"fecha_corte={fecha_corte.isoformat()}"
    guardar(modelo, carpeta / f"{elegido}.joblib")
    escribir_parquet(resultados, carpeta / "backtest.parquet")

    entrada = {
        "fecha_corte": fecha_corte.isoformat(),
        "modelo": elegido,
        "anterior": vigente["modelo"] if vigente else None,
        "artefacto": f"fecha_corte={fecha_corte.isoformat()}/{elegido}.joblib",
        "entrenado_con": modelo.entrenado_con,
        "decision": motivos,
        "backtest": resumen.reset_index().to_dict("records"),
    }
    otras = [e for e in leer_registro(dir_modelos) if e["fecha_corte"] != entrada["fecha_corte"]]
    registro = sorted(otras + [entrada], key=lambda e: e["fecha_corte"])
    escribir_json(registro, dir_modelos / REGISTRO)
    log.info("Modelo revisado al %s: %s (%s)", fecha_corte, elegido, "; ".join(motivos))
    return {"revisado": True, "modelo": elegido, "desde": entrada["fecha_corte"]}


# --------------------------------------------------------------------------- publicación


def commit_actual(raiz: Path) -> str | None:
    """Hash del commit de Git del repositorio montado (sin depender del binario git)."""
    git = raiz / ".git"
    try:
        cabeza = (git / "HEAD").read_text().strip()
        if not cabeza.startswith("ref: "):
            return cabeza
        ref = cabeza.removeprefix("ref: ")
        if (git / ref).is_file():
            return (git / ref).read_text().strip()
        for linea in (git / "packed-refs").read_text().splitlines():
            if linea.endswith(" " + ref):
                return linea.split()[0]
    except OSError:
        pass
    return None


class PronosticoInverosimil(ValueError):
    """El pronóstico no pasa la validación de salida: no se publica."""


def validar_pronostico(pronostico: pd.DataFrame, panel: Panel, params: dict[str, Any]) -> float:
    """Compara el total de la primera semana (P50) con las ventas de los últimos 7 días.

    Un cambio de nivel mayor al permitido indica un problema del modelo o de los datos,
    no una variación de negocio; mejor detener la publicación que emitir un pedido absurdo.
    """
    cfg = params["pronostico"]["validacion_salida"]
    if pronostico.filter(like="q0.").isna().any().any():
        raise PronosticoInverosimil("El pronóstico tiene valores faltantes.")
    semana = pronostico.loc[pronostico["h"] <= 7, "q0.50"].sum()
    corte = date.fromisoformat(pronostico["fecha_corte"].iloc[0])
    reciente = float(np.nansum(panel.historia(corte)[-7:]))
    razon = semana / reciente if reciente > 0 else float("inf")
    if not cfg["razon_min"] <= razon <= cfg["razon_max"]:
        raise PronosticoInverosimil(
            f"Total P50 de la semana = {semana:,.0f} vs {reciente:,.0f} vendidos en los últimos "
            f"7 días (razón {razon:.2f}, permitido {cfg['razon_min']}–{cfg['razon_max']})."
        )
    return razon


def pronosticar(
    historico: Path,
    dir_modelos: Path,
    params: dict[str, Any],
    fecha_corte: date,
    procesado: Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Pronóstico de la fecha de corte con el modelo vigente en esa fecha.

    Con `procesado`, usa los datos que preparó `preparar_datos`; si no, arma la vista desde
    el histórico (mismo resultado).
    """
    vigente = vigente_al(dir_modelos, fecha_corte)
    if vigente is None:
        raise FileNotFoundError(f"No hay modelo vigente al {fecha_corte}.")
    modelo = cargar(dir_modelos / vigente["artefacto"])
    fuentes = (
        cargar_preparados(procesado, fecha_corte)
        if procesado is not None
        else cargar_fuentes(historico, fecha_corte)
    )
    panel = Panel(fuentes, params)
    pronostico = modelo.predecir(panel, fecha_corte, params["calendario"]["horizonte_dias"])
    pronostico.insert(0, "fecha_corte", fecha_corte.isoformat())
    pronostico.insert(1, "modelo", vigente["modelo"])
    razon = validar_pronostico(pronostico, panel, params)
    vigente = vigente | {"razon_vs_semana_previa": round(razon, 3)}
    return pronostico, vigente


def publicar(
    raiz: Path, rutas: dict[str, Path], params: dict[str, Any], fecha_corte: date
) -> dict[str, Any]:
    pronostico, vigente = pronosticar(
        rutas["historico"], rutas["modelos"], params, fecha_corte, rutas["procesado"]
    )
    pedido = calcular_pedido(pronostico, params)
    semana = pedido_semanal(pedido)

    destino = rutas["publicados"] / f"fecha_corte={fecha_corte.isoformat()}"
    escribir_parquet(pronostico, destino / "pronostico.parquet")
    escribir_parquet(pedido, destino / "pedido.parquet")
    semana.to_csv(destino / "pedido_semana.csv.tmp", index=False)
    (destino / "pedido_semana.csv.tmp").replace(destino / "pedido_semana.csv")

    metadata = {
        "fecha_corte": fecha_corte.isoformat(),
        "modelo": vigente["modelo"],
        "modelo_entrenado": vigente["fecha_corte"],
        "artefacto": vigente["artefacto"],
        "commit": commit_actual(raiz),
        "cuantil_critico": round(cuantil_critico(params), 2),
        "costos": params["costos"],
        "horizonte_dias": params["calendario"]["horizonte_dias"],
        "series": int(pronostico[["store_nbr", "family"]].drop_duplicates().shape[0]),
        "filas": len(pronostico),
        "pedido_semana_total": round(float(semana["pedido_semana"].sum()), 2),
        "razon_vs_semana_previa": vigente["razon_vs_semana_previa"],
    }
    escribir_json(metadata, destino / "metadata.json")
    log.info("Publicado %s: %s", fecha_corte, metadata)
    return metadata
