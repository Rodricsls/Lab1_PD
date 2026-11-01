from pathlib import Path

import pandas as pd
import pytest

from reposicion.bandeja import KaggleIncompleto, generar_bandeja, leer_manifiesto
from tests.conftest import FECHA_SIN_VENTAS, escribir_kaggle_sintetico


def archivo(bandeja: Path, fuente: str, llegada: str, clave: str) -> Path:
    return bandeja / fuente / f"llegada={llegada}" / f"{fuente}_{clave}.csv"


def contenido(bandeja: Path) -> dict[str, bytes]:
    archivos = sorted(p for p in bandeja.rglob("*") if p.is_file())
    return {str(p.relative_to(bandeja)): p.read_bytes() for p in archivos}


@pytest.fixture
def bandeja(tmp_path: Path) -> Path:
    return tmp_path / "bandeja"


# ------------------------------------------------------------------ latencias y corte


def test_cada_fuente_llega_con_su_latencia(kaggle, bandeja, params):
    generar_bandeja(kaggle, bandeja, params)

    assert archivo(bandeja, "ventas", "2017-01-06", "2017-01-05").is_file()
    assert archivo(bandeja, "transacciones", "2017-01-07", "2017-01-05").is_file()
    assert archivo(bandeja, "petroleo", "2017-01-06", "2017-01-05").is_file()
    assert archivo(bandeja, "promociones", "2017-01-06", "2017-01-20").is_file()
    assert archivo(bandeja, "feriados", "2017-01-15", "2017-02").is_file()


def test_carga_inicial_contiene_solo_lo_llegado_hasta_el_corte(kaggle, bandeja, params):
    generar_bandeja(kaggle, bandeja, params)
    inicial = bandeja / "carga_inicial"

    ventas = pd.read_csv(inicial / "ventas.csv")
    # Las ventas del 31-dic llegan el 1-ene: ya no forman parte de la carga inicial.
    assert ventas["date"].max() == "2016-12-30"
    assert archivo(bandeja, "ventas", "2017-01-01", "2016-12-31").is_file()

    promos = pd.read_csv(inicial / "promociones.csv")
    # Las promociones del 14-ene se publicaron el 31-dic.
    assert promos["date"].max() == "2017-01-14"

    assert set(pd.read_csv(inicial / "feriados.csv")["date"]) == {"2016-12-25", "2017-01-01"}
    assert len(pd.read_csv(inicial / "tiendas.csv")) == 2


def test_dias_sin_datos_no_generan_archivo_ni_error(kaggle, bandeja, params):
    generar_bandeja(kaggle, bandeja, params)

    assert not list((bandeja / "ventas").glob(f"*/ventas_{FECHA_SIN_VENTAS}.csv"))
    sabados = list((bandeja / "petroleo").glob("*/petroleo_2017-01-07.csv"))
    assert sabados == []


def test_columnas_de_ventas_no_incluyen_promociones(kaggle, bandeja, params):
    generar_bandeja(kaggle, bandeja, params)
    df = pd.read_csv(archivo(bandeja, "ventas", "2017-01-06", "2017-01-05"))
    assert list(df.columns) == ["date", "store_nbr", "family", "sales"]
    assert len(df) == 4


# ------------------------------------------------------------------ idempotencia


def test_volver_a_generar_no_cambia_nada(kaggle, bandeja, params):
    generar_bandeja(kaggle, bandeja, params)
    antes = contenido(bandeja)

    resumen = generar_bandeja(kaggle, bandeja, params)

    assert resumen["omitido"] is True
    assert contenido(bandeja) == antes


def test_sin_atajo_tampoco_cambia_nada(kaggle, bandeja, params):
    generar_bandeja(kaggle, bandeja, params)
    (bandeja / "_kaggle.json").unlink()
    antes = {k: v for k, v in contenido(bandeja).items() if k != "_kaggle.json"}

    resumen = generar_bandeja(kaggle, bandeja, params)

    assert resumen["nuevas"] == 0 and resumen["correcciones"] == 0
    assert {k: v for k, v in contenido(bandeja).items() if k != "_kaggle.json"} == antes


# ------------------------------------------------------------------ correcciones


def test_errores_simulados_se_corrigen_con_el_dato_real(kaggle, bandeja, params):
    params["bandeja"]["correcciones"].update(simular=True, fraccion_archivos=1.0)
    resumen = generar_bandeja(kaggle, bandeja, params)
    assert resumen["errores_simulados"] > 0

    man = leer_manifiesto(bandeja)
    v = man[(man.fuente == "ventas") & (man.clave == "2017-01-10")].set_index("version")
    assert list(v.index) == [1, 2]
    assert v.loc[2, "llegada"] > v.loc[1, "llegada"]

    erronea = pd.read_csv(bandeja / v.loc[1, "ruta"])
    corregida = pd.read_csv(bandeja / v.loc[2, "ruta"])
    real = pd.read_csv(kaggle / "train.csv").query("date == '2017-01-10'")
    assert corregida["sales"].tolist() == real["sales"].tolist()
    assert erronea["sales"].tolist() != real["sales"].tolist()


def test_errores_simulados_son_deterministas(kaggle, tmp_path, params):
    params["bandeja"]["correcciones"].update(simular=True, fraccion_archivos=0.3)
    generar_bandeja(kaggle, tmp_path / "a", params)
    generar_bandeja(kaggle, tmp_path / "b", params)
    assert contenido(tmp_path / "a") == contenido(tmp_path / "b")


def test_cambio_en_kaggle_se_entrega_como_correccion_posterior(kaggle, bandeja, params):
    generar_bandeja(kaggle, bandeja, params)
    original = archivo(bandeja, "ventas", "2017-01-11", "2017-01-10")
    antes = original.read_bytes()

    train = pd.read_csv(kaggle / "train.csv")
    train.loc[train.date == "2017-01-10", "sales"] += 7
    train.to_csv(kaggle / "train.csv", index=False)
    resumen = generar_bandeja(kaggle, bandeja, params)

    assert resumen["correcciones"] == 1
    assert original.read_bytes() == antes  # lo ya entregado nunca se reescribe
    corregido = archivo(bandeja, "ventas", "2017-01-14", "2017-01-10")  # llegada + 3 días
    nuevas = pd.read_csv(corregido)["sales"].tolist()
    assert nuevas == train.loc[train.date == "2017-01-10", "sales"].tolist()


def test_fechas_nuevas_en_kaggle_extienden_la_bandeja(kaggle, bandeja, params):
    generar_bandeja(kaggle, bandeja, params)
    escribir_kaggle_sintetico(kaggle, hasta="2017-02-10")

    resumen = generar_bandeja(kaggle, bandeja, params)

    assert resumen["ultima_fecha_ventas"] == "2017-02-10"
    assert archivo(bandeja, "ventas", "2017-02-11", "2017-02-10").is_file()


# ------------------------------------------------------------------ entradas inválidas


def test_falta_un_archivo_de_kaggle(kaggle, bandeja, params):
    (kaggle / "oil.csv").unlink()
    with pytest.raises(KaggleIncompleto, match="oil.csv"):
        generar_bandeja(kaggle, bandeja, params)


def test_falta_una_columna(kaggle, bandeja, params):
    pd.read_csv(kaggle / "train.csv").drop(columns="sales").to_csv(kaggle / "train.csv")
    with pytest.raises(KaggleIncompleto, match="sales"):
        generar_bandeja(kaggle, bandeja, params)


def test_fecha_con_formato_invalido(kaggle, bandeja, params):
    oil = pd.read_csv(kaggle / "oil.csv")
    oil.loc[0, "date"] = "05/01/2017"
    oil.to_csv(kaggle / "oil.csv", index=False)
    with pytest.raises(KaggleIncompleto, match="fechas"):
        generar_bandeja(kaggle, bandeja, params)
