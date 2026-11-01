import shutil
from pathlib import Path

from reposicion.config import RAIZ
from reposicion.entorno import diagnostico


def test_diagnostico_sobre_repo_temporal(tmp_path: Path):
    (tmp_path / "config").mkdir()
    shutil.copy(RAIZ / "config" / "params.yaml", tmp_path / "config" / "params.yaml")
    (tmp_path / "data" / "bandeja").mkdir(parents=True)

    resultado = diagnostico(tmp_path)

    assert resultado["raiz"] == str(tmp_path)
    assert resultado["bandeja_existe"] is True
    assert resultado["kaggle_existe"] is False
    assert 0 < resultado["cuantil_critico"] < 1


def test_bandeja_ausente_no_es_escribible(tmp_path: Path):
    (tmp_path / "config").mkdir()
    shutil.copy(RAIZ / "config" / "params.yaml", tmp_path / "config" / "params.yaml")

    assert diagnostico(tmp_path)["bandeja_escribible"] is False
