import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from reposicion.bandeja import generar_bandeja, leer_manifiesto
from reposicion.ingesta import ingerir_carga_inicial, ingerir_dia, resumir_dia, vista
from reposicion.validacion import DatosInvalidos
from tests.conftest import FECHA_SIN_VENTAS


@pytest.fixture
def entorno(kaggle, tmp_path, params):
    """Bandeja generada desde Kaggle sintético y carga inicial ya ingerida."""
    bandeja, historico = tmp_path / "bandeja", tmp_path / "historico"
    generar_bandeja(kaggle, bandeja, params)
    ingerir_carga_inicial(bandeja, historico)
    return bandeja, historico, params


def ingerir_rango(entorno, fuente, desde, hasta):
    bandeja, historico, params = entorno
    for d in pd.date_range(desde, hasta):
        ingerir_dia(fuente, d.date(), bandeja, historico, params)


def contenido(carpeta: Path) -> dict[str, bytes]:
    return {str(p.relative_to(carpeta)): p.read_bytes() for p in sorted(carpeta.rglob("*.parquet"))}


# ------------------------------------------------------------------ carga inicial


def test_carga_inicial_se_ingiere_una_sola_vez(entorno):
    bandeja, historico, _ = entorno
    ventas = pd.read_parquet(historico / "carga_inicial" / "ventas.parquet")
    assert ventas["date"].max() == "2016-12-30"
    # Cada registro conserva su llegada original (ventas: D+1), no la fecha del corte.
    llegada_esperada = (pd.to_datetime(ventas["date"]) + pd.Timedelta(days=1)).dt.strftime(
        "%Y-%m-%d"
    )
    assert (ventas["fecha_llegada"] == llegada_esperada).all()

    assert ingerir_carga_inicial(bandeja, historico)["omitido"] is True


def test_vista_anterior_al_corte_usa_la_llegada_original(entorno):
    _, historico, _ = entorno
    v = vista(historico, "ventas", date(2016, 12, 10))
    assert v["date"].max() == "2016-12-09"
    assert not vista(historico, "tiendas", date(2016, 12, 10)).empty


# ------------------------------------------------------------------ ingesta diaria


def test_ingesta_diaria_registra_fecha_de_llegada(entorno):
    bandeja, historico, params = entorno
    r = ingerir_dia("ventas", date(2017, 1, 6), bandeja, historico, params)

    assert r == {"archivos": 1, "filas": 4, "correcciones": 0, "avisos": 0}
    df = pd.read_parquet(historico / "ventas" / "llegada=2017-01-06" / "ventas_2017-01-05.parquet")
    assert set(df["fecha_llegada"]) == {"2017-01-06"}


def test_ingesta_es_idempotente(entorno):
    bandeja, historico, params = entorno
    ingerir_dia("ventas", date(2017, 1, 6), bandeja, historico, params)
    antes = contenido(historico)
    ingerir_dia("ventas", date(2017, 1, 6), bandeja, historico, params)
    assert contenido(historico) == antes


def test_dia_sin_archivos_es_aviso_no_falla(entorno):
    bandeja, historico, params = entorno
    dia_siguiente = date.fromisoformat(FECHA_SIN_VENTAS).replace(day=16)
    r = ingerir_dia("ventas", dia_siguiente, bandeja, historico, params)

    assert r["archivos"] == 0
    reporte = json.loads(
        (historico / "_reportes" / f"llegada={dia_siguiente}" / "ventas.json").read_text("utf-8")
    )
    assert "no llegó ningún archivo" in reporte["avisos"]
    assert f"aún no llegan datos del {FECHA_SIN_VENTAS}" in reporte["avisos"]


def test_fin_de_semana_sin_petroleo_no_genera_aviso_de_atraso(entorno):
    bandeja, historico, params = entorno
    ingerir_dia("petroleo", date(2017, 1, 8), bandeja, historico, params)  # dato del sábado
    reporte = json.loads(
        (historico / "_reportes" / "llegada=2017-01-08" / "petroleo.json").read_text("utf-8")
    )
    assert not any("aún no llegan" in a for a in reporte["avisos"])


def test_archivo_invalido_falla_sin_contaminar_y_el_resto_entra(entorno, tmp_path):
    bandeja, historico, params = entorno
    llegada = bandeja / "ventas" / "llegada=2017-01-06"
    # Un segundo archivo, inválido, en la misma llegada (escribimos sobre la copia de prueba).
    malo = pd.read_csv(llegada / "ventas_2017-01-05.csv").assign(date="2017-01-04")
    malo.loc[0, "sales"] = -5
    malo.to_csv(llegada / "ventas_2017-01-04.csv", index=False)

    with pytest.raises(DatosInvalidos, match="negativos"):
        ingerir_dia("ventas", date(2017, 1, 6), bandeja, historico, params)

    particion = historico / "ventas" / "llegada=2017-01-06"
    assert sorted(p.name for p in particion.iterdir()) == ["ventas_2017-01-05.parquet"]
    assert resumir_dia(date(2017, 1, 6), historico)["ventas"]["invalidos"] == 1


# ------------------------------------------------------------------ vista en el tiempo


def test_vista_solo_ve_lo_llegado_hasta_el_corte(entorno):
    ingerir_rango(entorno, "ventas", "2017-01-01", "2017-01-20")
    _, historico, _ = entorno

    v = vista(historico, "ventas", date(2017, 1, 10))
    assert v["date"].max() == "2017-01-09"  # lo del 10 llega el 11
    assert v["date"].min() == "2016-12-01"  # incluye la carga inicial
    assert not v.duplicated(["date", "store_nbr", "family"]).any()


def test_vista_aplica_la_correccion_solo_desde_que_llega(kaggle, tmp_path, params):
    params["bandeja"]["correcciones"].update(simular=True, fraccion_archivos=1.0)
    bandeja, historico = tmp_path / "bandeja", tmp_path / "historico"
    generar_bandeja(kaggle, bandeja, params)
    ingerir_carga_inicial(bandeja, historico)
    entorno = (bandeja, historico, params)
    ingerir_rango(entorno, "ventas", "2017-01-01", "2017-01-25")

    man = leer_manifiesto(bandeja)
    v = man[(man.fuente == "ventas") & (man.clave == "2017-01-10")].set_index("version")
    llegada_error = date.fromisoformat(v.loc[1, "llegada"])
    llegada_buena = date.fromisoformat(v.loc[2, "llegada"])
    real = pd.read_csv(kaggle / "train.csv").query("date == '2017-01-10'")["sales"].tolist()

    def ventas_del_10(corte):
        df = vista(historico, "ventas", corte)
        return df[df["date"] == "2017-01-10"].sort_values(["store_nbr", "family"])["sales"].tolist()

    assert ventas_del_10(llegada_error) != real
    assert ventas_del_10(llegada_buena) == real

    reporte = json.loads(
        (historico / "_reportes" / f"llegada={llegada_buena}" / "ventas.json").read_text("utf-8")
    )
    assert "2017-01-10" in reporte["correcciones"]
