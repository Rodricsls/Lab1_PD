import copy

import pytest

from reposicion.config import ConfigInvalida, cargar_params, cuantil_critico, validar_params


@pytest.fixture
def params():
    return cargar_params()


def test_params_del_repo_son_validos(params):
    assert params["calendario"]["horizonte_dias"] >= 1


def test_cuantil_critico_newsvendor(params):
    p = copy.deepcopy(params)
    p["costos"] = {"quiebre_por_unidad": 3.0, "merma_por_unidad": 1.0}
    assert cuantil_critico(p) == pytest.approx(0.75)


@pytest.mark.parametrize(
    "seccion, clave, valor",
    [
        ("costos", "merma_por_unidad", 0),
        ("pronostico", "cuantiles", [0.9, 0.1]),
        ("pronostico", "cuantiles", [0.0, 0.5]),
        ("calendario", "horizonte_dias", 0),
    ],
)
def test_config_invalida_se_rechaza(params, seccion, clave, valor):
    p = copy.deepcopy(params)
    p[seccion][clave] = valor
    with pytest.raises(ConfigInvalida):
        validar_params(p)
