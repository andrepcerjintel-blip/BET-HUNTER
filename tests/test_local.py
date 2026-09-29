"""Preparação para execução local: rede robusta, ambiente opcional, .env, log em arquivo, primeira execução."""
import json
import os
import sqlite3
import time

import pytest
import requests

from bethunter import db, envcheck, net, pipeline, sources, util, visual
from bethunter.web import create_app


class FakeResp:
    status_code = 200
    url = "https://x"
    headers = {}
    encoding = "utf-8"
    def iter_content(self, n): yield b"ola"
    def close(self): pass


@pytest.fixture
def realnet(monkeypatch, env):
    """Restaura net.fetch real (o conftest o substitui) com limites rápidos."""
    import importlib
    src = importlib.util.find_spec("bethunter.net")
    fresh = importlib.util.module_from_spec(src); src.loader.exec_module(fresh)
    fresh.CFG.update(min_interval=0, max_retries=2, timeout=3)
    monkeypatch.setattr(fresh.time, "sleep", lambda s: None)
    return fresh


def test_retry_then_success_and_short_error(realnet, monkeypatch):
    calls = []
    def req(method, url, **kw):
        calls.append(kw["timeout"])
        if len(calls) < 3:
            raise requests.ConnectionError("HTTPSConnectionPool(host='x', port=443): Max retries exceeded ... Tunnel connection failed")
        return FakeResp()
    monkeypatch.setattr(realnet.requests, "request", req)
    r = realnet.fetch("https://exemplo.com/")
    assert r["status"] == 200 and r["text"] == "ola" and len(calls) == 3        # 1 + MAX_RETRIES(2)
    assert calls[0] == (3, 3)                                                    # timeout sempre aplicado
    calls.clear()
    monkeypatch.setattr(realnet.requests, "request", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout("read timed out")))
    r = realnet.fetch("https://exemplo.com/")
    assert r["status"] is None and r["error"] == "tempo esgotado"                # sem stack/URL gigante para o usuário
    monkeypatch.setattr(realnet.requests, "request", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("Tunnel connection failed: 403 " + "x" * 500)))
    e = realnet.fetch("https://exemplo.com/")["error"]
    assert "falha de conexão" in e and len(e) < 120


def test_no_retry_on_http_block_and_bounded_attempts(realnet, monkeypatch):
    n = []
    class R429(FakeResp): status_code = 429
    monkeypatch.setattr(realnet.requests, "request", lambda *a, **k: (n.append(1), R429())[1])
    assert realnet.fetch("https://exemplo.com/")["status"] == 429 and len(n) == 1   # bloqueio não é contornado nem repetido
    n.clear()
    realnet.configure(max_retries=1)
    monkeypatch.setattr(realnet.requests, "request", lambda *a, **k: (n.append(1), (_ for _ in ()).throw(requests.Timeout()))[1])
    realnet.fetch("https://exemplo.com/")
    assert len(n) == 2
    realnet.configure(max_retries=99)
    assert realnet.CFG["max_retries"] == 5                                           # limite duro: sem loop infinito


def test_rate_limit_spacing(realnet, monkeypatch):
    slept = []
    realnet.CFG["min_interval"] = 0.5
    monkeypatch.setattr(realnet.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(realnet.requests, "request", lambda *a, **k: FakeResp())
    realnet.fetch("https://a.com/"); realnet.fetch("https://b.com/")
    assert slept and 0 < slept[0] <= 0.5


def test_private_hosts_blocked(realnet):
    assert realnet.fetch("http://127.0.0.1:8080/")["error"] == "host privado bloqueado"


def test_one_source_failure_keeps_partial_results_and_logs(env, monkeypatch, tmp_path):
    monkeypatch.setenv("BETHUNTER_LOG_DIR", str(tmp_path / "logs"))
    util.setup_logging()
    def rs(source, q):
        if source == "bing":
            raise RuntimeError("boom " * 50)
        if source == "ddg":
            return ([{"username": "ok_user", "profile_url": "https://www.tiktok.com/@ok_user", "video_url": "", "text": "Fortune Tiger link na bio cadastre-se",
                      "source": "DuckDuckGo", "query": q, "source_url": "x"}], None, "x")
        return [], "HTTP 403 (bloqueio ou limite de requisições da fonte)", "x"
    monkeypatch.setattr(sources, "run_source", rs)
    with db.connect() as c:
        s = db.get_settings(c)
    a = pipeline.run_query('"link na bio" "saque"', "bing", s)
    b = pipeline.run_query('"link na bio" "saque"', "ddg", s)
    t = pipeline.run_query('"link na bio" "saque"', "tiktok", s)
    assert a["error"] and "boom" not in a["error"] and "logs/bethunter.log" in a["error"]   # sem stack para o usuário
    assert b["new"] == 1 and t["error"].startswith("HTTP 403")
    with db.connect() as c:
        rows = c.execute("SELECT ts, source, query, error_msg, errors FROM search_log ORDER BY id").fetchall()
        assert [r["errors"] for r in rows] == [1, 0, 1] and all(r["ts"] and r["query"] for r in rows)
        assert c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1                # parcial preservado
    logtxt = open(tmp_path / "logs" / "bethunter.log", encoding="utf-8").read()
    assert "FONTE=Bing" in logtxt and "CONSULTA=" in logtxt and "Traceback" in logtxt        # detalhe técnico só no arquivo
    assert "FONTE=TikTok Search" in logtxt and "HTTP 403" in logtxt


def test_env_static_status_optional_everything(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(visual, "tesseract_path", lambda: None)
    monkeypatch.setattr(sources, "ytdlp_cmd", lambda: None)
    monkeypatch.setattr(visual, "engine", lambda: None)
    st = envcheck.static_status()
    assert st["ANTHROPIC"]["estado"] == "NÃO CONFIGURADO" and st["TESSERACT"]["estado"] == "NÃO INSTALADO"
    assert st["YT-DLP"]["estado"] == "NÃO INSTALADO" and st["BANCO"]["estado"] == "OK" and st["VISUAL_ANALYSIS"]["estado"] == "NÃO DISPONÍVEL"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-secreta  ")
    monkeypatch.setattr(visual, "engine", lambda: "multimodal")
    st = envcheck.static_status()
    assert st["ANTHROPIC"]["estado"] == "CONFIGURADO" and "sk-secreta" not in json.dumps(st)   # chave nunca é exibida
    monkeypatch.setattr(sources, "ytdlp_cmd", lambda: ["yt-dlp"])
    assert envcheck.static_status()["YT-DLP"]["estado"] == "INSTALADO"


def test_env_test_endpoint_states(env, monkeypatch, tmp_path):
    def fake(url, **kw):
        assert kw.get("timeout") and kw.get("throttle") is False                 # teste rápido, com timeout, sem fila de ritmo
        if "cloudflare" in url: return {"status": 200, "text": "ok", "error": None, "url": url, "location": None}
        if "duckduckgo" in url: return {"status": 202, "text": "", "error": None, "url": url, "location": None}
        if "bing" in url: return {"status": None, "text": "", "error": "tempo esgotado", "url": url, "location": None}
        return {"status": 200, "text": "<html>sem dados</html>", "error": None, "url": url, "location": None}
    monkeypatch.setattr(net, "fetch", fake)
    c = create_app(str(tmp_path / "e.db")).test_client()
    r = c.post("/api/env/test").json
    assert r["INTERNET"]["estado"] == "OK" and r["DUCKDUCKGO"]["estado"] == "BLOQUEADO"
    assert r["BING"] == {"estado": "ERRO", "detalhe": "tempo esgotado"} and r["TIKTOK"]["estado"] == "BLOQUEADO"
    for k in ("YT-DLP", "ANTHROPIC", "TESSERACT", "BANCO"):
        assert k in r
    assert c.get("/api/env").status_code == 200 and c.get("/").status_code == 200   # app segue de pé com tudo negativo


def test_dotenv_never_overrides_and_key_not_persisted(env, monkeypatch, tmp_path):
    f = tmp_path / ".env"
    f.write_text('# c\nANTHROPIC_API_KEY=\nBETHUNTER_X="abc"\nBETHUNTER_Y=1\n', encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False); monkeypatch.delenv("BETHUNTER_X", raising=False)
    monkeypatch.setenv("BETHUNTER_Y", "manter")
    assert util.load_dotenv(str(f))
    assert "ANTHROPIC_API_KEY" not in os.environ and os.environ["BETHUNTER_X"] == "abc" and os.environ["BETHUNTER_Y"] == "manter"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-NAO-GRAVAR")
    c = create_app(str(tmp_path / "k.db")).test_client()
    c.put("/api/settings", json={"goal": 150}); c.get("/api/env"); c.post("/api/investigate", json={"target": "@k_user"})
    raw = open(tmp_path / "k.db", "rb").read() + (open(str(tmp_path / "k.db") + "-wal", "rb").read() if os.path.exists(str(tmp_path / "k.db") + "-wal") else b"")
    assert b"sk-NAO-GRAVAR" not in raw
    src = "".join(open(p, encoding="utf-8").read() for p in ("bethunter/web.py", "bethunter/visual.py", "bethunter/config.py", ".env.example"))
    assert "sk-" not in src.replace("sk-secreta", "")
    assert open(".env.example").read().count("ANTHROPIC_API_KEY=\n") == 1


def test_first_run_creates_everything_and_restart_preserves(tmp_path, monkeypatch):
    p = tmp_path / "novo" / "sub" / "primeira.db"               # pasta e banco inexistentes
    monkeypatch.setenv("BETHUNTER_DB", str(p))
    monkeypatch.setenv("BETHUNTER_LOG_DIR", str(tmp_path / "lg"))
    monkeypatch.setenv("BETHUNTER_EXPORT_DIR", str(tmp_path / "ex"))
    app = create_app(str(p)); c = app.test_client()
    assert p.exists()
    tables = {r[0] for r in sqlite3.connect(p).execute("select name from sqlite_master where type='table'")}
    assert {"candidates", "evidences", "links", "discoveries", "hunts", "search_log", "settings", "visuals", "cluster_members"} <= tables
    s = c.get("/api/settings").json
    assert s["thresholds"] == {"alta": 70, "revisar": 45} and s["auto_discard_low"] is False and len(c.get("/api/hunts").json) == 14
    # dados do analista
    c.put("/api/settings", json={"weights": {"cta": 33}, "games": ["Meu Jogo"], "thresholds": {"alta": 80}})
    c.post("/api/import", json={"text": "@um\n@dois"})
    c.post("/api/manual", json={"username": "tres", "text": "Fortune Tiger cadastre-se link na bio", "link": "https://xyzbet.bet.br/?aff=7"})
    tid = c.get("/api/candidates?view=results").json["items"][0]["id"]
    c.post(f"/api/candidates/{tid}/status", json={"status": "CONFIRMADO", "note": "guardar"})
    c.put("/api/hunts/1", json={"queries": ["minha consulta"], "enabled": False})
    open(tmp_path / "lg" / "x.txt", "w").write("log")
    # reinício ("atualização"): nada é apagado nem redefinido
    app2 = create_app(str(p)); c2 = app2.test_client()
    s2 = c2.get("/api/settings").json
    assert s2["weights"]["cta"] == 33 and s2["games"] == ["Meu Jogo"] and s2["thresholds"]["alta"] == 80 and s2["thresholds"]["revisar"] == 45
    assert c2.get("/api/stats").json["total"] == 3 and c2.get(f"/api/candidates/{tid}").json["analyst_note"] == "guardar"
    h = c2.get("/api/hunts").json[0]
    assert h["queries"] == ["minha consulta"] and h["enabled"] is False and len(c2.get("/api/hunts").json) == 14
    # exportação também é salva em exportacoes/
    r = c2.get("/api/export?format=csv&kind=mission&scope=confirmed")
    assert r.status_code == 200 and len(os.listdir(tmp_path / "ex")) == 1


def test_ytdlp_module_fallback(monkeypatch):
    monkeypatch.setattr(sources.shutil, "which", lambda n: None)
    monkeypatch.setattr(sources.importlib.util, "find_spec", lambda n: object())
    assert sources.ytdlp_cmd()[-2:] == ["-m", "yt_dlp"]
    monkeypatch.setattr(sources.importlib.util, "find_spec", lambda n: None)
    assert sources.ytdlp_cmd() is None
    assert sources.ytdlp_videos("qualquer") == []


def test_captcha_is_reported_not_bypassed(env, monkeypatch):
    monkeypatch.setattr(net, "fetch", lambda url, **k: {"status": 200, "url": url, "location": None, "error": None,
                                                        "text": "<html><body>Please verify you are human (captcha)</body></html>"})
    for fn in (sources.search_ddg, sources.search_bing):
        hits, err, _ = fn("tigrinho")
        assert hits == [] and "bloqueado" in err
