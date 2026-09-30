import shutil
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(SKILL))


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    """Cada teste roda num RINO_ROOT isolado com os datasets vazios (cabeçalhos) copiados da skill."""
    shutil.copytree(SKILL / "datasets", tmp_path / "datasets")
    (tmp_path / "output").mkdir()
    monkeypatch.setenv("RINO_ROOT", str(tmp_path))
    return tmp_path


def make_fetcher(table):
    """table: url -> (status, location|None, text). URL ausente = 404. Conta chamadas em .calls."""
    calls = []

    def f(url):
        calls.append(url)
        st, loc, txt = table.get(url, (404, None, ""))
        return {"status": st, "location": loc, "text": txt, "error": None}
    f.calls = calls
    return f
