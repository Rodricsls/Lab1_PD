import copy
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from reposicion.bandeja import generar_bandeja
from reposicion.features import Fuentes, Panel, cargar_fuentes, origenes_entrenamiento
from reposicion.ingesta import FUENTES_DIARIAS, ingerir_carga_inicial, ingerir_dia
from reposicion.modelos import (
    Baseline,
    ModeloLGBM,
    backtest,
    cortes_backtest,
    cuantiles_modelo,
    decidir_promocion,
    evaluar,
    resumir,
)
from tests.conftest import escribir_kaggle_sintetico

ORIGEN = date(2017, 1, 16)


@pytest.fixture
def params_chicos(params):
    p = copy.deepcopy(params)
    p["calendario"]["horizonte_dias"] = 7
    p["baseline"]["semanas"] = 4
    p["entrenamiento"].update(origenes=3, dias_entre_origenes=7)
    p["entrenamiento"]["lightgbm"].update(num_boost_round=20, min_data_in_leaf=5, num_leaves=7)
    p["validacion_temporal"].update(n_cortes=2, dias_entre_cortes=7)
    return p


@pytest.fixture
def fuentes(tmp_path) -> Fuentes:
    """Fuentes armadas directo desde Kaggle sintético (sin pasar por la bandeja)."""
    k = tmp_path / "k"
    escribir_kaggle_sintetico(k, desde="2016-09-01", hasta="2017-02-28")
    train = pd.read_csv(k / "train.csv")
    return Fuentes(
        ventas=train[["date", "store_nbr", "family", "sales"]],
        promociones=train[["date", "store_nbr", "family", "onpromotion"]],
        transacciones=pd.read_csv(k / "transactions.csv"),
        petroleo=pd.read_csv(k / "oil.csv"),
        feriados=pd.read_csv(k / "holidays_events.csv"),
        tiendas=pd.read_csv(k / "stores.csv"),
    )


# ------------------------------------------------------------------ features point-in-time


def test_features_no_usan_ventas_del_origen_en_adelante(fuentes, params_chicos):
    panel = Panel(fuentes, params_chicos)
    antes = panel.filas(ORIGEN, 7)

    panel.ventas.loc[pd.Timestamp(ORIGEN) :] = 9999.0
    despues = panel.filas(ORIGEN, 7)

    pd.testing.assert_frame_equal(antes, despues)


def test_transacciones_respetan_su_latencia(fuentes, params_chicos):
    panel = Panel(fuentes, params_chicos)
    antes = panel.filas(ORIGEN, 7)["transacciones_28"]
    panel.trans.loc[pd.Timestamp(ORIGEN - timedelta(days=1))] = 1e9  # aún no llegaba (D+2)
    assert panel.filas(ORIGEN, 7)["transacciones_28"].equals(antes)


def test_promociones_mas_alla_de_su_anticipacion_son_desconocidas(fuentes, params_chicos):
    filas = Panel(fuentes, params_chicos).filas(ORIGEN, 20)
    assert filas.loc[filas["h"] <= 15, "promo"].notna().all()
    assert filas.loc[filas["h"] > 15, "promo"].isna().all()


def test_feriados_no_publicados_son_desconocidos(fuentes, params_chicos):
    panel = Panel(fuentes, params_chicos)
    # Feriados de marzo se publican el 15 de febrero: desde el 10-feb aún no se conocen.
    filas = panel.filas(date(2017, 2, 10), 25)
    marzo = filas["date"] >= "2017-03-01"
    assert filas.loc[marzo, "feriado_nacional"].isna().all()
    assert filas.loc[~marzo, "feriado_nacional"].notna().all()
    navidad = panel.filas(date(2016, 12, 20), 7)
    assert (navidad.loc[navidad["date"] == "2016-12-25", "feriado_nacional"] == 1).all()


def test_origenes_de_entrenamiento_solo_ven_horizontes_completos(fuentes, params_chicos):
    panel = Panel(fuentes, params_chicos)
    origenes = origenes_entrenamiento(ORIGEN, panel, 7, params_chicos["entrenamiento"])
    assert len(origenes) == 3
    assert max(origenes) + timedelta(days=6) <= ORIGEN - timedelta(days=1)


# ------------------------------------------------------------------ modelos


def test_cuantiles_incluyen_el_critico(params):
    assert cuantiles_modelo(params) == [0.1, 0.5, 0.75, 0.9]


def test_baseline_reproduce_una_serie_constante(fuentes, params_chicos):
    fuentes.ventas["sales"] = 42.0
    pred = Baseline(params_chicos).ajustar(None, ORIGEN, 7).predecir(
        Panel(fuentes, params_chicos), ORIGEN, 7
    )
    assert len(pred) == 4 * 7
    assert np.allclose(pred[["q0.10", "q0.50", "q0.75", "q0.90"]], 42.0)


def test_lightgbm_predice_cuantiles_ordenados_y_no_negativos(fuentes, params_chicos):
    panel = Panel(fuentes, params_chicos)
    pred = ModeloLGBM(params_chicos).ajustar(panel, ORIGEN, 7).predecir(panel, ORIGEN, 7)
    q = pred[["q0.10", "q0.50", "q0.75", "q0.90"]].to_numpy()
    assert len(pred) == 4 * 7 and set(pred["h"]) == set(range(1, 8))
    assert (np.diff(q, axis=1) >= 0).all() and (q >= 0).all()


def test_evaluar_calcula_el_costo_de_negocio(params):
    pred = pd.DataFrame(
        {
            "store_nbr": [1, 1],
            "family": ["A", "A"],
            "date": ["2017-01-01", "2017-01-02"],
            "q0.10": [5.0, 5.0],
            "q0.50": [10.0, 10.0],
            "q0.75": [12.0, 12.0],
            "q0.90": [15.0, 15.0],
        }
    )
    real = pd.DataFrame(
        {"store_nbr": [1, 1], "family": ["A", "A"], "date": ["2017-01-01", "2017-01-02"],
         "sales": [20.0, 10.0]}
    )
    m = evaluar(pred, real, params)  # costos 3 (quiebre) y 1 (merma), pedido = q0.75
    assert m["costo_negocio"] == pytest.approx(3 * 8 + 1 * 2)
    assert m["wape_p50"] == pytest.approx(10 / 30)
    assert m["cobertura_intervalo"] == pytest.approx(0.5)


def test_criterio_de_promocion():
    resumen = pd.DataFrame({"costo_negocio": [100.0, 96.0, 98.0]}, index=["a", "b", "c"])
    assert decidir_promocion(resumen, "b", "a", 0.03)[0] is True
    assert decidir_promocion(resumen, "c", "a", 0.03)[0] is False


# ------------------------------------------------------------------ backtest de punta a punta


@pytest.fixture
def historico(tmp_path, params_chicos):
    kaggle, bandeja, historico = tmp_path / "k", tmp_path / "b", tmp_path / "h"
    escribir_kaggle_sintetico(kaggle, desde="2016-09-01", hasta="2017-02-28")
    generar_bandeja(kaggle, bandeja, params_chicos)
    ingerir_carga_inicial(bandeja, historico)
    for d in pd.date_range("2017-01-01", "2017-03-02"):
        for fuente in FUENTES_DIARIAS:
            ingerir_dia(fuente, d.date(), bandeja, historico, params_chicos)
    return historico


def test_backtest_compara_modelos_en_los_mismos_cortes(historico, params_chicos):
    final = date(2017, 3, 2)
    resultados = backtest(historico, params_chicos, final)

    cortes = cortes_backtest(final, Panel(cargar_fuentes(historico, final), params_chicos),
                             params_chicos)
    assert all(c.weekday() == params_chicos["calendario"]["dia_pedido"] for c in cortes)
    for modelo in ("baseline", "lightgbm"):
        assert resultados.loc[resultados.modelo == modelo, "corte"].tolist() == [
            c.isoformat() for c in cortes
        ]
    assert set(resumir(resultados).index) == {"baseline", "lightgbm"}
