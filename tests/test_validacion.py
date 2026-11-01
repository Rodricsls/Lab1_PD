import numpy as np
import pandas as pd
import pytest

from reposicion.validacion import DatosInvalidos, Referencia, validar

REF = Referencia(tiendas=frozenset({1, 2}), familias=frozenset({"BEVERAGES", "DAIRY"}))


def ventas(**cambios) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "date": ["2017-01-05"] * 4,
            "store_nbr": [1, 1, 2, 2],
            "family": ["BEVERAGES", "DAIRY"] * 2,
            "sales": [10.0, 3.5, 0.0, 7.0],
        }
    )
    for columna, valores in cambios.items():
        df[columna] = valores
    return df


def problemas(fuente, df, **kw) -> str:
    with pytest.raises(DatosInvalidos) as e:
        validar(fuente, df, "prueba.csv", **kw)
    return str(e.value)


def test_archivo_valido_se_tipa_sin_avisos():
    r = validar("ventas", ventas(), "v.csv", clave="2017-01-05", referencia=REF)
    assert r.avisos == []
    assert str(r.datos["store_nbr"].dtype) == "Int64"
    assert r.datos["sales"].dtype == float


@pytest.mark.parametrize(
    "df, esperado",
    [
        (ventas().drop(columns="sales"), "faltan columnas"),
        (ventas().iloc[0:0], "sin filas"),
        (ventas(sales=[10.0, -1.0, 0.0, 7.0]), "negativos"),
        (ventas(sales=[10.0, "abc", 0.0, 7.0]), "no numéricos"),
        (ventas(sales=[10.0, np.nan, 0.0, 7.0]), "nulos"),
        (ventas(store_nbr=[1, 1, 2, 9]), "tiendas desconocidas"),
        (ventas(family=["BEVERAGES", "DAIRY", "BEVERAGES", "TOYS"]), "familias desconocidas"),
        (ventas(family=["BEVERAGES"] * 4), "duplicada"),
        (ventas(date=["2017-01-05", "2017-01-06", "2017-01-05", "2017-01-05"]), "fecha distinta"),
        (ventas(date=["05/01/2017"] * 4), "formato inválido"),
        (ventas(store_nbr=[1, 1.5, 2, 2]), "no enteros"),
    ],
)
def test_datos_invalidos_detienen(df, esperado):
    assert esperado in problemas("ventas", df, clave="2017-01-05", referencia=REF)


def test_petroleo_faltante_es_aviso_no_error():
    df = pd.DataFrame({"date": ["2017-01-05"], "dcoilwtico": [np.nan]})
    r = validar("petroleo", df, "p.csv", clave="2017-01-05")
    assert r.avisos == ["dcoilwtico: 1 valores faltantes"]


def test_tienda_cerrada_y_combinaciones_faltantes_son_avisos():
    df = ventas(sales=[0.0, 0.0, 4.0, 7.0]).iloc[:3]
    r = validar("ventas", df, "v.csv", clave="2017-01-05", referencia=REF)
    assert any("faltan 1 combinaciones" in a for a in r.avisos)
    assert any("tiendas sin ventas" in a and "[1]" in a for a in r.avisos)


def test_feriados_admiten_varios_eventos_por_fecha_y_validan_el_mes():
    df = pd.DataFrame(
        {
            "date": ["2017-02-27", "2017-02-27"],
            "type": ["Holiday", "Event"],
            "locale": ["National"] * 2,
            "locale_name": ["Ecuador"] * 2,
            "description": ["Carnaval", "Otro"],
            "transferred": ["False", False],
        }
    )
    assert validar("feriados", df, "f.csv", clave="2017-02").avisos == []
    assert "fecha distinta" in problemas("feriados", df, clave="2017-03")
