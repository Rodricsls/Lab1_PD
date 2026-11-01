"""Compara pronósticos publicados contra las ventas reales a medida que se conocen.

En la fecha D se evalúa cada publicación con fecha de corte ≤ D usando la vista de ventas
en D: solo los días ya observados y con las correcciones que hayan llegado hasta D. Así el
desempeño de una publicación se va completando día a día y se ajusta si llega una
corrección. Resultado por fecha: `publicados/_seguimiento/al=D/resumen.csv`.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from reposicion.config import cuantil_critico
from reposicion.ingesta import vista
from reposicion.modelos import evaluar

log = logging.getLogger(__name__)

CARPETA = "_seguimiento"


def publicaciones(publicados: Path, hasta: date) -> list[tuple[date, Path]]:
    salida = []
    for carpeta in sorted(publicados.glob("fecha_corte=*")):
        corte = date.fromisoformat(carpeta.name.removeprefix("fecha_corte="))
        if corte <= hasta and (carpeta / "pronostico.parquet").is_file():
            salida.append((corte, carpeta))
    return salida


def seguimiento_al(
    historico: Path, publicados: Path, params: dict[str, Any], fecha: date
) -> pd.DataFrame:
    """Una fila por publicación: cuánto de su horizonte ya se observó y qué tan bien le fue."""
    ventas = vista(historico, "ventas", fecha)
    filas = []
    for corte, carpeta in publicaciones(publicados, fecha):
        pronostico = pd.read_parquet(carpeta / "pronostico.parquet")
        observadas = pronostico.merge(
            ventas[["store_nbr", "family", "date"]].assign(
                store_nbr=lambda d: d["store_nbr"].astype(int)
            ),
            on=["store_nbr", "family", "date"],
        )
        fila = {
            "fecha_corte": corte.isoformat(),
            "modelo": pronostico["modelo"].iloc[0],
            "dias_observados": int(observadas["date"].nunique()),
            "dias_horizonte": int(pronostico["date"].nunique()),
        }
        if len(observadas):
            fila |= evaluar(pronostico, ventas, params)
        filas.append(fila)
    df = pd.DataFrame(filas)
    if len(df):
        df.insert(0, "al", fecha.isoformat())
        df["completa"] = df["dias_observados"] >= df["dias_horizonte"]
    return df


def escribir_seguimiento(publicados: Path, fecha: date, df: pd.DataFrame) -> Path:
    ruta = publicados / CARPETA / f"al={fecha.isoformat()}" / "resumen.csv"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name("resumen.csv.tmp")
    df.to_csv(temporal, index=False)
    temporal.replace(ruta)
    return ruta


def resumen_global(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Totales de las publicaciones con al menos un día observado."""
    con_datos = df[df["dias_observados"] > 0] if len(df) else df
    if con_datos.empty:
        return {"publicaciones": 0}
    vendidas = (con_datos["costo_negocio"] / con_datos["costo_por_unidad_vendida"]).sum()
    return {
        "publicaciones": int(len(con_datos)),
        "completas": int(con_datos["completa"].sum()),
        "wape_p50_medio": round(float(con_datos["wape_p50"].mean()), 4),
        "cobertura_intervalo_media": round(float(con_datos["cobertura_intervalo"].mean()), 4),
        "nivel_servicio_medio": round(float(con_datos["nivel_servicio"].mean()), 4),
        "nivel_servicio_objetivo": round(cuantil_critico(params), 2),
        "costo_negocio_total": round(float(con_datos["costo_negocio"].sum()), 2),
        "costo_por_unidad_vendida": round(float(con_datos["costo_negocio"].sum() / vendidas), 4),
    }


def reporte_markdown(df: pd.DataFrame, glob: dict[str, Any], fecha: date) -> str:
    columnas = [
        "fecha_corte",
        "modelo",
        "dias_observados",
        "wape_p50",
        "cobertura_intervalo",
        "nivel_servicio",
        "costo_negocio",
        "unidades_faltantes",
        "unidades_sobrantes",
    ]
    tabla = df[[c for c in columnas if c in df]].copy()
    lineas = ["| " + " | ".join(tabla.columns) + " |", "|" + "---|" * len(tabla.columns)]
    for fila in tabla.itertuples(index=False):
        celdas = [f"{v:,.3f}" if isinstance(v, float) else str(v) for v in fila]
        lineas.append("| " + " | ".join(celdas) + " |")
    indicadores = "\n".join(f"- **{k}**: {v}" for k, v in glob.items())
    return "\n\n".join(
        [
            f"# Seguimiento del desempeño (al {fecha.isoformat()})",
            "Cada pronóstico publicado comparado con las ventas reales conocidas a esa fecha "
            "(incluidas las correcciones). *Nivel de servicio*: fracción de días en que el "
            "pedido sugerido cubrió la venta; el objetivo es el cuantil crítico.",
            "## Indicadores globales",
            indicadores,
            "## Por publicación",
            "\n".join(lineas),
        ]
    )


def main(argv: list[str] | None = None) -> None:
    from reposicion.entorno import contexto

    parser = argparse.ArgumentParser(description="Reporte de seguimiento del desempeño.")
    parser.add_argument("--fecha", type=date.fromisoformat, required=True)
    parser.add_argument("--salida", type=Path, default=Path("reportes"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    params, rutas = contexto()
    df = seguimiento_al(rutas["historico"], rutas["publicados"], params, args.fecha)
    glob = resumen_global(df, params)
    args.salida.mkdir(parents=True, exist_ok=True)
    (args.salida / "seguimiento.md").write_text(
        reporte_markdown(df, glob, args.fecha) + "\n", encoding="utf-8"
    )
    print(glob)


if __name__ == "__main__":
    main()
