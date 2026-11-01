from datetime import date

import pandas as pd
import pytest

from reposicion.produccion import publicar, revisar_modelo
from reposicion.seguimiento import (
    escribir_seguimiento,
    reporte_markdown,
    resumen_global,
    seguimiento_al,
)

LUNES = date(2017, 2, 6)


@pytest.fixture
def publicado(historico, tmp_path, params_chicos):
    rutas = {
        "historico": historico,
        "modelos": tmp_path / "modelos",
        "publicados": tmp_path / "publicados",
    }
    revisar_modelo(historico, rutas["modelos"], params_chicos, LUNES)
    publicar(tmp_path, rutas, params_chicos, LUNES)
    return rutas


def test_antes_del_corte_no_hay_nada_que_evaluar(publicado, params_chicos):
    df = seguimiento_al(publicado["historico"], publicado["publicados"], params_chicos,
                        date(2017, 2, 5))
    assert df.empty


def test_el_horizonte_se_completa_a_medida_que_llegan_las_ventas(publicado, params_chicos):
    h, p = publicado["historico"], publicado["publicados"]
    # Las ventas del día D llegan en D+1: el 8-feb se conocen las del 6 y el 7.
    parcial = seguimiento_al(h, p, params_chicos, date(2017, 2, 8)).iloc[0]
    assert parcial["dias_observados"] == 2 and not parcial["completa"]

    completo = seguimiento_al(h, p, params_chicos, date(2017, 2, 13)).iloc[0]
    assert completo["dias_observados"] == 7 and completo["completa"]
    assert 0 <= completo["nivel_servicio"] <= 1
    assert completo["costo_negocio"] > 0


def test_resumen_y_reporte(publicado, params_chicos, tmp_path):
    df = seguimiento_al(publicado["historico"], publicado["publicados"], params_chicos,
                        date(2017, 2, 13))
    ruta = escribir_seguimiento(publicado["publicados"], date(2017, 2, 13), df)
    assert pd.read_csv(ruta)["fecha_corte"].tolist() == ["2017-02-06"]

    glob = resumen_global(df, params_chicos)
    assert glob["publicaciones"] == 1 and glob["completas"] == 1
    assert glob["nivel_servicio_objetivo"] == 0.75
    assert "| 2017-02-06 |" in reporte_markdown(df, glob, date(2017, 2, 13))


def test_reproducir_un_pronostico_publicado(publicado, params_chicos):
    from reposicion.reproducir import reproducir

    con_guardado = reproducir(publicado, params_chicos, LUNES)
    assert con_guardado["identico"] and con_guardado["filas"] == 4 * 7

    reentrenado = reproducir(publicado, params_chicos, LUNES, reentrenar=True)
    assert reentrenado["identico"], reentrenado
