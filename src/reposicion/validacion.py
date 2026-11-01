"""Reglas de validación: datos inválidos detienen el flujo; eventos esperados no.

- **Inválido** (lanza `DatosInvalidos`): faltan columnas, tipos que no se pueden
  interpretar, nulos donde no se admiten, fechas que no corresponden al archivo, llaves
  duplicadas, valores negativos, tiendas o familias desconocidas, archivo vacío.
- **Esperado** (se registra como aviso y se continúa): precio del petróleo faltante, tienda
  con ventas en cero (cerrada), combinaciones tienda × familia ausentes en un archivo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Tipo de cada columna por fuente: fecha, entero, numero, texto, booleano.
ESQUEMAS: dict[str, dict[str, str]] = {
    "ventas": {"date": "fecha", "store_nbr": "entero", "family": "texto", "sales": "numero"},
    "transacciones": {"date": "fecha", "store_nbr": "entero", "transactions": "entero"},
    "petroleo": {"date": "fecha", "dcoilwtico": "numero"},
    "promociones": {
        "date": "fecha",
        "store_nbr": "entero",
        "family": "texto",
        "onpromotion": "entero",
    },
    "feriados": {
        "date": "fecha",
        "type": "texto",
        "locale": "texto",
        "locale_name": "texto",
        "description": "texto",
        "transferred": "booleano",
    },
    "tiendas": {
        "store_nbr": "entero",
        "city": "texto",
        "state": "texto",
        "type": "texto",
        "cluster": "entero",
    },
}
LLAVES = {
    "ventas": ["date", "store_nbr", "family"],
    "transacciones": ["date", "store_nbr"],
    "petroleo": ["date"],
    "promociones": ["date", "store_nbr", "family"],
    "tiendas": ["store_nbr"],
    # feriados: una fecha puede tener varios eventos legítimos.
}
NO_NEGATIVOS = {
    "ventas": ["sales"],
    "transacciones": ["transactions"],
    "petroleo": ["dcoilwtico"],
    "promociones": ["onpromotion"],
}
NULOS_ADMITIDOS = {"petroleo": ["dcoilwtico"]}


class DatosInvalidos(ValueError):
    def __init__(self, fuente: str, origen: str, problemas: list[str]):
        self.fuente, self.origen, self.problemas = fuente, origen, problemas
        super().__init__(f"{fuente} · {origen}: " + "; ".join(problemas))


@dataclass(frozen=True)
class Referencia:
    """Llaves conocidas, tomadas de la carga inicial ya validada."""

    tiendas: frozenset[int]
    familias: frozenset[str]


@dataclass
class Resultado:
    datos: pd.DataFrame
    avisos: list[str] = field(default_factory=list)


def _convertir(df: pd.DataFrame, columna: str, tipo: str, problemas: list[str]) -> pd.Series:
    serie = df[columna]
    if tipo == "fecha":
        convertida = pd.to_datetime(serie, format="%Y-%m-%d", errors="coerce")
        malas = convertida.isna() & serie.notna()
        if malas.any():
            problemas.append(f"{columna}: {malas.sum()} fechas con formato inválido")
        return convertida.dt.strftime("%Y-%m-%d").where(convertida.notna())
    if tipo in ("entero", "numero"):
        convertida = pd.to_numeric(serie, errors="coerce")
        malas = convertida.isna() & serie.notna()
        if malas.any():
            problemas.append(f"{columna}: {malas.sum()} valores no numéricos")
        if tipo == "entero":
            no_enteros = convertida.notna() & (convertida % 1 != 0)
            if no_enteros.any():
                problemas.append(f"{columna}: {no_enteros.sum()} valores no enteros")
            return convertida.astype("Int64") if not no_enteros.any() else convertida
        return convertida.astype(float)
    if tipo == "booleano":
        mapa = {True: True, False: False, "True": True, "False": False}
        convertida = serie.map(mapa)
        malas = convertida.isna() & serie.notna()
        if malas.any():
            problemas.append(f"{columna}: {malas.sum()} valores no booleanos")
        return convertida.astype("boolean")
    return serie.astype("string")


def validar(
    fuente: str,
    df: pd.DataFrame,
    origen: str,
    clave: str | None = None,
    referencia: Referencia | None = None,
) -> Resultado:
    """Valida y tipa una tabla. `clave` es la fecha (o mes) que el archivo dice contener."""
    esquema = ESQUEMAS[fuente]
    faltan = [c for c in esquema if c not in df.columns]
    if faltan:
        raise DatosInvalidos(fuente, origen, [f"faltan columnas {faltan}"])
    if df.empty:
        raise DatosInvalidos(fuente, origen, ["archivo sin filas"])

    problemas: list[str] = []
    avisos: list[str] = []
    datos = pd.DataFrame({c: _convertir(df, c, t, problemas) for c, t in esquema.items()})

    admitidos = NULOS_ADMITIDOS.get(fuente, [])
    for columna in esquema:
        nulos = int(datos[columna].isna().sum())
        if nulos and columna in admitidos:
            avisos.append(f"{columna}: {nulos} valores faltantes")
        elif nulos and not any(p.startswith(columna) for p in problemas):
            problemas.append(f"{columna}: {nulos} valores nulos")

    if clave is not None and "date" in datos:
        fuera = ~datos["date"].fillna("").str.startswith(clave)
        if fuera.any():
            problemas.append(f"{fuera.sum()} filas con fecha distinta a {clave}")

    llave = LLAVES.get(fuente)
    if llave:
        duplicadas = datos.duplicated(llave, keep=False)
        if duplicadas.any():
            problemas.append(f"{duplicadas.sum()} filas con llave {llave} duplicada")

    for columna in NO_NEGATIVOS.get(fuente, []):
        negativos = datos[columna].fillna(0) < 0
        if negativos.any():
            problemas.append(f"{columna}: {negativos.sum()} valores negativos")

    if referencia is not None:
        if "store_nbr" in datos:
            desconocidas = {int(t) for t in datos["store_nbr"].dropna()} - referencia.tiendas
            if desconocidas:
                problemas.append(f"tiendas desconocidas {sorted(desconocidas)}")
        if "family" in datos:
            desconocidas = set(datos["family"].dropna()) - referencia.familias
            if desconocidas:
                problemas.append(f"familias desconocidas {sorted(desconocidas)}")

    if problemas:
        raise DatosInvalidos(fuente, origen, problemas)

    if referencia is not None and fuente in ("ventas", "promociones"):
        esperadas = len(referencia.tiendas) * len(referencia.familias) * datos["date"].nunique()
        if len(datos) < esperadas:
            avisos.append(f"faltan {esperadas - len(datos)} combinaciones tienda × familia")
    if fuente == "ventas":
        por_tienda = datos.groupby("store_nbr")["sales"].sum()
        cerradas = sorted(int(t) for t in por_tienda[por_tienda == 0].index)
        if cerradas and datos["date"].nunique() == 1:
            avisos.append(f"tiendas sin ventas (cerradas o sin abrir): {cerradas}")

    return Resultado(datos, avisos)
