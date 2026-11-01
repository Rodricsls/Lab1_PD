import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from reposicion.pedido import calcular_pedido, pedido_semanal
from reposicion.produccion import (
    commit_actual,
    dias_sin_ingerir,
    leer_registro,
    publicar,
    revisar_modelo,
    vigente_al,
)

LUNES = date(2017, 2, 6)


@pytest.fixture
def rutas(historico, tmp_path):
    return {
        "historico": historico,
        "modelos": tmp_path / "modelos",
        "publicados": tmp_path / "publicados",
    }


def contenido(carpeta: Path) -> dict[str, bytes]:
    return {str(p.relative_to(carpeta)): p.read_bytes() for p in sorted(carpeta.rglob("*.*"))}


def test_dias_sin_ingerir(historico, tmp_path):
    assert dias_sin_ingerir(tmp_path / "vacio", LUNES) == ["carga_inicial"]
    assert dias_sin_ingerir(historico, date(2017, 3, 2)) == []
    assert dias_sin_ingerir(historico, date(2017, 3, 4)) == ["2017-03-03", "2017-03-04"]


def test_primera_revision_entrena_y_registra(rutas, params_chicos):
    r = revisar_modelo(rutas["historico"], rutas["modelos"], params_chicos, LUNES)

    assert r["revisado"] is True
    entrada = leer_registro(rutas["modelos"])[0]
    assert entrada["fecha_corte"] == "2017-02-06" and entrada["anterior"] is None
    assert entrada["modelo"] in {"baseline", "lightgbm"}
    assert (rutas["modelos"] / entrada["artefacto"]).is_file()
    assert {b["modelo"] for b in entrada["backtest"]} == {"baseline", "lightgbm"}


def test_revision_respeta_la_cadencia_y_es_idempotente(rutas, params_chicos):
    params_chicos["entrenamiento"]["reentrenar_cada_semanas"] = 2
    h, m = rutas["historico"], rutas["modelos"]
    revisar_modelo(h, m, params_chicos, LUNES)

    assert revisar_modelo(h, m, params_chicos, LUNES)["revisado"] is False
    assert revisar_modelo(h, m, params_chicos, date(2017, 2, 13))["revisado"] is False
    assert revisar_modelo(h, m, params_chicos, date(2017, 2, 20))["revisado"] is True
    assert [e["fecha_corte"] for e in leer_registro(m)] == ["2017-02-06", "2017-02-20"]


def test_el_vigente_depende_solo_de_la_fecha(tmp_path):
    registro = [
        {"fecha_corte": "2017-02-06", "modelo": "a"},
        {"fecha_corte": "2017-03-06", "modelo": "b"},
    ]
    (tmp_path / "registro.json").write_text(json.dumps(registro))
    assert vigente_al(tmp_path, date(2017, 2, 1)) is None
    assert vigente_al(tmp_path, date(2017, 2, 27))["modelo"] == "a"
    assert vigente_al(tmp_path, date(2017, 3, 6))["modelo"] == "b"


def test_publicar_escribe_pronostico_pedido_y_metadata(rutas, params_chicos, tmp_path):
    revisar_modelo(rutas["historico"], rutas["modelos"], params_chicos, LUNES)
    meta = publicar(tmp_path, rutas, params_chicos, LUNES)

    destino = rutas["publicados"] / "fecha_corte=2017-02-06"
    pronostico = pd.read_parquet(destino / "pronostico.parquet")
    pedido = pd.read_parquet(destino / "pedido.parquet")
    assert len(pronostico) == 4 * 7 and set(pronostico["fecha_corte"]) == {"2017-02-06"}
    assert pedido["pedido_sugerido"].tolist() == pronostico["q0.75"].round(2).tolist()
    assert len(pd.read_csv(destino / "pedido_semana.csv")) == 4
    assert meta["cuantil_critico"] == 0.75 and meta["series"] == 4
    assert json.loads((destino / "metadata.json").read_text("utf-8")) == meta


def test_publicar_dos_veces_da_lo_mismo(rutas, params_chicos, tmp_path):
    revisar_modelo(rutas["historico"], rutas["modelos"], params_chicos, LUNES)
    publicar(tmp_path, rutas, params_chicos, LUNES)
    antes = contenido(rutas["publicados"])
    publicar(tmp_path, rutas, params_chicos, LUNES)
    assert contenido(rutas["publicados"]) == antes


def test_cambiar_costos_cambia_el_pedido_sin_tocar_codigo(params):
    pronostico = pd.DataFrame(
        {
            "fecha_corte": ["2017-02-06"] * 2,
            "store_nbr": [1, 1],
            "family": ["A", "A"],
            "date": ["2017-02-06", "2017-02-07"],
            "h": [1, 2],
            "q0.50": [10.0, 20.0],
            "q0.75": [12.0, 24.0],
        }
    )
    assert calcular_pedido(pronostico, params)["pedido_sugerido"].tolist() == [12.0, 24.0]
    params["costos"].update(quiebre_por_unidad=1.0, merma_por_unidad=1.0)  # crítico = 0.50
    assert calcular_pedido(pronostico, params)["pedido_sugerido"].tolist() == [10.0, 20.0]
    semana = pedido_semanal(calcular_pedido(pronostico, params))
    assert semana["pedido_semana"].tolist() == [30.0]


def test_commit_actual_lee_head_sin_binario_git(tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text("abc123\n")
    assert commit_actual(tmp_path) == "abc123"
    assert commit_actual(tmp_path / "otro") is None


def test_ultima_llegada_de_la_bandeja(kaggle, tmp_path, params):
    from reposicion.bandeja import generar_bandeja
    from reposicion.produccion import ultima_llegada

    assert ultima_llegada(tmp_path / "nada") is None
    generar_bandeja(kaggle, tmp_path / "b", params)
    assert ultima_llegada(tmp_path / "b") == date(2017, 2, 2)  # transacciones del 31-ene (D+2)
