"""TikTok Commercial Content API como fonte adicional. `net.post` é SIMULADO: o teste real depende de credenciais
aprovadas e de internet (execução local)."""
import hashlib
import json
import os
import time

import pytest

from bethunter import commercial, config, db, envcheck, exporter, mission, net, pipeline, queries, sources
from bethunter.web import create_app

TOKEN_URL = "https://auth.exemplo.test/token"
_real_sleep = time.sleep            # os testes zeram commercial.time.sleep (global): a espera dos polls usa a original
SECRET = "SEGREDO-NAO-GRAVAR"
KEY = "CHAVE-CLIENTE"


def resp(status, js=None, err=None, retry_after=None):
    return {"status": status, "json": js, "text": "", "retry_after": retry_after, "error": err}


def page(ids, has_more=False, sid=None):
    return resp(200, {"data": {"ads": [{"ad": {"id": i}} for i in ids], "has_more": has_more, "search_id": sid},
                      "error": {"code": "ok"}})


@pytest.fixture
def creds(monkeypatch, env):
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", KEY)
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", SECRET)
    monkeypatch.setattr(commercial.time, "sleep", lambda s: None)
    commercial.reset_token()
    with db.connect() as c:
        db.save_settings(c, {"commercial_api_token_url": TOKEN_URL, "request_delay": 0})
    yield
    commercial.reset_token()


def install(monkeypatch, pages=None, token=None, fn=None):
    """Simula net.post. `pages`: respostas em ordem para o endpoint de consulta."""
    calls, it = [], iter(pages or [])

    def fake_post(url, headers=None, data=None, json_body=None, params=None, timeout=None):
        calls.append({"url": url, "headers": headers, "data": data, "json": json_body, "params": params})
        if url == TOKEN_URL:
            return token or resp(200, {"access_token": f"TOK{len(calls)}", "expires_in": 7200})
        return fn(len(calls)) if fn else next(it)
    monkeypatch.setattr(net, "post", fake_post)
    return calls


def queries_only(calls):
    return [c for c in calls if c["url"] == commercial.ENDPOINT]


def S():
    with db.connect() as c:
        return db.get_settings(c)


# ---------------------------------------------------------------- 1. sem credenciais
def test_without_credentials_everything_still_works(env, monkeypatch, tmp_path):
    monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False); monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(net, "post", lambda *a, **k: pytest.fail("não deve haver chamada de rede sem credenciais"))
    assert commercial.static_state()[0] == "NÃO CONFIGURADA"
    hits, err, _ = commercial.search("Fortune Tiger")
    assert hits == [] and "NÃO CONFIGURADA" in err
    c = create_app(str(tmp_path / "s.db")).test_client()
    assert c.get("/api/env").json["TIKTOK COMMERCIAL API"]["estado"] == "NÃO CONFIGURADA"
    assert c.post("/api/investigate", json={"target": "@ok_user"}).status_code == 200      # demais fontes/fluxo intactos
    j = pipeline.start_job("m", lambda jb: mission.run_mission(jb, 200, 0, "rapido", ["commercial"]), sync=True)
    assert j.status == "done" and "indisponíveis" in j.result["motivo"]
    with db.connect() as cc:      # caça 13 existe e roda sem credenciais, sem erro
        h = [x for x in cc.execute("SELECT * FROM hunts").fetchall() if x["kind"] == "commercial"][0]
    assert h["name"] == "CAÇA 13 — TIKTOK COMMERCIAL CONTENT"
    r = pipeline.run_hunt(h["id"], pipeline.Job("x"))
    assert r["ok"] and r["found"] == 0 and r["errors"] == 0


def test_credentials_found_but_auth_pending(env, monkeypatch):
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", KEY); monkeypatch.setenv("TIKTOK_CLIENT_SECRET", SECRET)
    monkeypatch.setattr(net, "post", lambda *a, **k: pytest.fail("autenticação pendente: não inventa endpoint"))
    est, det = commercial.static_state()
    assert est == "CREDENCIAIS ENCONTRADAS" and "pendente" in det
    hits, err, _ = commercial.search("Aviator")
    assert hits == [] and err.startswith("CREDENCIAIS ENCONTRADAS")
    assert commercial.check()["estado"] == "CREDENCIAIS ENCONTRADAS"


# ---------------------------------------------------------------- 2-7. consulta e paginação
def test_request_shape_uses_only_documented_fields(creds, monkeypatch):
    calls = install(monkeypatch, [page(["1", "2"])])
    hits, err, url = commercial.search('"Fortune Tiger"')
    assert err is None and url == commercial.ENDPOINT and [h["ad_id"] for h in hits] == ["1", "2"]
    tok, q = calls
    assert tok["url"] == TOKEN_URL and tok["data"]["client_key"] == KEY and tok["data"]["client_secret"] == SECRET
    assert q["params"] == {"fields": "ad.id"} and q["headers"]["Authorization"] == "Bearer TOK1"
    b = q["json"]
    assert set(b) == {"filters", "search_term", "max_count"} and "search_id" not in b        # 1ª chamada SEM search_id
    assert set(b["filters"]) == {"ad_published_date_range", "country_code"} and b["filters"]["country_code"] == "BR"
    assert b["search_term"] == "Fortune Tiger" and b["max_count"] == 20
    assert set(b["filters"]["ad_published_date_range"]) == {"min", "max"}
    assert hits[0]["country"] == "BR" and hits[0]["raw"] == {"ad": {"id": "1"}}


def test_has_more_false_single_call(creds, monkeypatch):
    calls = install(monkeypatch, [page(["1"], has_more=False, sid="x")])
    commercial.search("Tigrinho")
    assert len(queries_only(calls)) == 1


def test_pagination_with_search_id_multiple_pages(creds, monkeypatch):
    calls = install(monkeypatch, [page(["1", "2"], True, "S1"), page(["3"], True, "S2"), page(["4"], False, None)])
    hits, err, _ = commercial.search("Aviator")
    q = queries_only(calls)
    assert err is None and [h["ad_id"] for h in hits] == ["1", "2", "3", "4"] and len(q) == 3
    assert [c["json"].get("search_id") for c in q] == [None, "S1", "S2"]                   # 2ª página usa o search_id devolvido


def test_page_limit_and_infinite_loop_guards(creds, monkeypatch):
    with db.connect() as c:
        db.save_settings(c, {"commercial_api_max_pages": 3})
    counter = iter(range(1, 999))
    calls = install(monkeypatch, fn=lambda n: page([str(next(counter))], True, f"S{n}"))
    hits, err, _ = commercial.search("Crash")
    assert err is None and len(queries_only(calls)) == 3 and len(hits) == 3                # has_more sempre true: para em max_pages
    calls = install(monkeypatch, [page(["9"], True, None)])                                 # has_more sem search_id
    assert len(commercial.search("Plinko")[0]) == 1 and len(queries_only(calls)) == 1
    calls = install(monkeypatch, [page([], True, "A"), page([], True, "A")])               # páginas vazias com search_id repetido
    commercial.search("slot")
    assert len(queries_only(calls)) <= 2


def test_date_range_presets_and_custom():
    d = __import__("datetime").date(2026, 9, 29)
    s = config.default_settings()
    for rg, mn in (("hoje", "20260929"), ("24h", "20260928"), ("3d", "20260926"), ("7d", "20260922")):
        s["commercial_api_range"] = rg
        assert commercial.date_range(s, today=d) == (mn, "20260929")
    s.update(commercial_api_range="custom", commercial_api_date_from="2026-08-01", commercial_api_date_to="2026-08-15")
    assert commercial.date_range(s, today=d) == ("20260801", "20260815")
    s.update(commercial_api_date_from="2026-09-10", commercial_api_date_to="2026-09-01")
    assert commercial.date_range(s, today=d) == ("20260901", "20260910")
    s.update(commercial_api_date_from="lixo")
    assert commercial.date_range(s, today=d)[1] == "20260929"                              # inválido: cai no padrão, sem quebrar


def test_max_count_configurable_and_reduced_on_400(creds, monkeypatch):
    with db.connect() as c:
        db.save_settings(c, {"commercial_api_max_count": 40, "commercial_api_country": "br"})
    calls = install(monkeypatch, [resp(400, {"error": {"code": "invalid_params"}}), resp(400, {}), page(["1"])])
    hits, err, _ = commercial.search("Spaceman")
    assert err is None and len(hits) == 1
    assert [c["json"]["max_count"] for c in queries_only(calls)] == [40, 20, 10] and calls[1]["json"]["filters"]["country_code"] == "BR"
    install(monkeypatch, fn=lambda n: resp(400, {}))
    hits, err, _ = commercial.search("Spaceman")
    assert hits == [] and "HTTP 400" in err                                                 # reduz no máximo 3 vezes


# ---------------------------------------------------------------- 8. deduplicação
def test_dedupe_by_ad_id_across_pages_terms_and_runs(creds, monkeypatch):
    install(monkeypatch, [page(["7", "7", "8"]), page(["7", "9"]), page(["7", "9"])])
    s = S()
    a = pipeline.run_query("Fortune Tiger", "commercial", s, hunt="t")
    b = pipeline.run_query("Tigrinho", "commercial", s, hunt="t")      # 7 já existe (outro termo), 9 novo
    c = pipeline.run_query("Tigrinho", "commercial", s, hunt="t")      # tudo já coletado
    assert (a["found"], a["new"]) == (2, 2) and (b["found"], b["new"], b["dups"]) == (2, 1, 1) and c["new"] == 0
    with db.connect() as cc:
        assert cc.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 3
        assert cc.execute("SELECT COUNT(*) FROM evidences WHERE kind='commercial'").fetchone()[0] == 3
        cid = cc.execute("SELECT id FROM candidates WHERE username='ad:7'").fetchone()[0]
        srcs = cc.execute("SELECT query FROM discoveries WHERE candidate_id=?", (cid,)).fetchall()
        assert {r[0] for r in srcs} == {"Fortune Tiger", "Tigrinho"}                        # mesmo anúncio por 2 termos: 1 evidência


# ---------------------------------------------------------------- 9-13. erros
def test_timeout_keeps_partial_results(creds, monkeypatch):
    install(monkeypatch, [page(["1"], True, "S1"), resp(None, err="tempo esgotado")])
    hits, err, _ = commercial.search("Mines")
    assert [h["ad_id"] for h in hits] == ["1"] and err == "tempo esgotado"


def test_401_reauthenticates_once(creds, monkeypatch):
    calls = install(monkeypatch, [resp(401), page(["1"])])
    hits, err, _ = commercial.search("Mines")
    assert err is None and len(hits) == 1 and len([c for c in calls if c["url"] == TOKEN_URL]) == 2
    install(monkeypatch, [resp(401), resp(401)])
    commercial.reset_token()
    hits, err, _ = commercial.search("Mines")
    assert hits == [] and "AUTENTICAÇÃO FALHOU" in err and "401" in err


def test_auth_endpoint_failures(creds, monkeypatch):
    install(monkeypatch, token=resp(401, {"error": "invalid_client"}))
    hits, err, _ = commercial.search("Mines")
    assert hits == [] and err.startswith("AUTENTICAÇÃO FALHOU") and "invalid_client" in err
    commercial.reset_token()
    install(monkeypatch, token=resp(200, {"sem_token": 1}))
    assert commercial.search("Mines")[1].startswith("AUTENTICAÇÃO FALHOU")
    install(monkeypatch, token=resp(429))
    assert commercial.search("Mines")[1].startswith("RATE LIMITED")


def test_403_no_permission(creds, monkeypatch):
    calls = install(monkeypatch, [resp(403)])
    hits, err, _ = commercial.search("Mines")
    assert hits == [] and "SEM PERMISSÃO PARA O ENDPOINT" in err and "403" in err and len(queries_only(calls)) == 1


def test_429_conservative_retry(creds, monkeypatch):
    slept = []
    monkeypatch.setattr(commercial.time, "sleep", lambda s: slept.append(s))
    calls = install(monkeypatch, [resp(429, retry_after=7), page(["1"])])
    assert commercial.search("Mines")[0][0]["ad_id"] == "1" and slept == [7.0]
    calls = install(monkeypatch, [resp(429), resp(429), resp(429)])
    hits, err, _ = commercial.search("Mines")
    assert hits == [] and "RATE LIMITED" in err and len(queries_only(calls)) == 2         # 1 retry apenas, sem agressividade
    install(monkeypatch, [resp(429, retry_after=9999), page(["1"])])
    slept.clear(); commercial.search("Mines")
    assert slept == [30.0]                                                                  # espera limitada


def test_5xx_and_invalid_response(creds, monkeypatch):
    calls = install(monkeypatch, [resp(500), resp(503)])
    hits, err, _ = commercial.search("Mines")
    assert hits == [] and "HTTP 503" in err and len(queries_only(calls)) == 2
    install(monkeypatch, [resp(500), page(["1"])])
    assert len(commercial.search("Mines")[0]) == 1
    install(monkeypatch, [resp(200, None)])
    assert commercial.search("Mines")[1].startswith("resposta inválida")
    install(monkeypatch, [resp(200, {"data": {"algo": "sem lista"}})])
    assert commercial.search("Mines") == ([], None, commercial.ENDPOINT)


def test_error_is_logged_and_never_leaks_secrets(creds, monkeypatch, tmp_path):
    from bethunter import util
    monkeypatch.setenv("BETHUNTER_LOG_DIR", str(tmp_path / "lg")); util.setup_logging()
    install(monkeypatch, [resp(403)])
    st = pipeline.run_query("Mines", "commercial", S(), hunt="t")
    assert st["found"] == 0 and "403" in st["error"]
    with db.connect() as c:
        r = queries.search_log(c)[0]
        assert r["source"] == "TIKTOK_COMMERCIAL_CONTENT_API" and r["query"] == "Mines" and "HTTP 403" in r["error_msg"] and r["ts"] and r["errors"] == 1
    txt = open(tmp_path / "lg" / "bethunter.log", encoding="utf-8").read()
    assert "FONTE=TIKTOK_COMMERCIAL_CONTENT_API" in txt and "TERMO=Mines" in txt and "HTTP=403" in txt
    assert SECRET not in txt and "Bearer" not in txt and "TOK1" not in txt and KEY not in txt


def test_noise_terms_skipped_but_games_run_as_is(creds, monkeypatch):
    calls = install(monkeypatch, [page(["1"])])
    for t in ("bet", "Bets", "aposta", "apostas", '"bet"'):
        assert commercial.search(t) == ([], None, commercial.ENDPOINT)
    assert calls == []
    commercial.search("cassino")
    assert queries_only(calls)[0]["json"]["search_term"] == "cassino"                       # sem "link na bio" acrescentado


# ---------------------------------------------------------------- 14-16. score, revisão, classificação, exportação
def make_organic():
    pipeline.import_items("@organico_fake")
    with db.connect() as c:
        cid = c.execute("SELECT id FROM candidates WHERE username='organico_fake'").fetchone()[0]
    pipeline.add_manual_evidence(cid, text="Fortune Tiger pagando, cadastre-se pelo link na bio, saque na hora",
                                 link="https://xyzbet.bet.br/?aff=55")
    return cid


def test_score_type_status_and_priority(creds, monkeypatch):
    install(monkeypatch, [page(["555"])])
    pipeline.run_query("Fortune Tiger", "commercial", S(), hunt="t")
    org = make_organic()
    with db.connect() as c:
        d = queries.candidate_detail(c, c.execute("SELECT id FROM candidates WHERE username='ad:555'").fetchone()[0])
        assert d["score"] == 20 and [r["key"] for r in d["reasons"]] == ["origin_commercial"] and d["reasons"][0]["pts"] == 20
        assert d["content_type"] == "CONTEÚDO COMERCIAL / ANÚNCIO" and d["classification"].startswith("BAIXA")
        assert d["status"] == "NOVO" and d["profile_url"] == "" and d["profile_status"] == "ANUNCIO"     # nunca CONFIRMADO/ilícito
        assert d["evidence_count"] == 1 and d["sources"] == ["TIKTOK_COMMERCIAL_CONTENT_API"]
        ev = d["evidences"][0]
        assert ev["kind"] == "commercial" and commercial.EVIDENCE_TEXT in ev["text"]
        for line in ("TERM: Fortune Tiger", "COUNTRY: BR", "DATE_RANGE: ", "AD_ID: 555", "DATA_COLETA: "):
            assert line in ev["text"]
        assert ev["meta"]["source_type"] == "CONTEUDO_COMERCIAL" and ev["meta"]["raw"] == {"ad": {"id": "555"}} and ev["meta"]["ad_id"] == "555"
        # score combinado: orgânico forte supera o anúncio fraco
        lst = queries.list_candidates(c, {"view": "all"}, sort="priority")["items"]
        assert lst[0]["id"] == org and lst[0]["score"] > 70 > lst[1]["score"]
        # origem + sinais próprios somam (peso configurável) e a origem não anula redutores nem confirma
        db.save_settings(c, {"weights": {"origin_commercial": 35}})
        pipeline.refresh_all(c)
        assert queries.candidate_detail(c, d["id"])["score"] == 35
        assert queries.candidate_detail(c, d["id"])["status"] == "NOVO"


def test_commercial_origin_does_not_pollute_text_analysis(creds, monkeypatch):
    install(monkeypatch, [page(["1"])])
    pipeline.run_query("Tigrinho", "commercial", S(), hunt="t")
    with db.connect() as c:
        d = queries.candidate_detail(c, 1)
    assert not any(r["key"] in ("r_incidental", "hashtag", "platform") for r in d["reasons"])   # o termo não vira "sinal" textual


def test_review_confirm_and_export(creds, monkeypatch, tmp_path):
    install(monkeypatch, [page(["101", "102"])])
    pipeline.run_query("Aviator", "commercial", S(), hunt="t")
    app = create_app(str(tmp_path / "t.db")); c = app.test_client()
    queue = c.get("/api/queue").json["ids"]
    assert len(queue) == 2                                                                  # entram na revisão (status NOVO)
    assert c.post(f"/api/candidates/{queue[0]}/status", json={"status": "CONFIRMADO", "note": "vi no Ad Library"}).json["status"] == "CONFIRMADO"
    assert c.post(f"/api/candidates/{queue[1]}/status", json={"status": "DESCARTADO"}).json["status"] == "DESCARTADO"
    import csv, io
    rows = list(csv.DictReader(io.StringIO(c.get("/api/export?format=csv&kind=full&scope=all").data.decode("utf-8-sig"))))
    assert list(rows[0])[-6:] == ["SOURCE", "SOURCE_TYPE", "SEARCH_TERM", "COUNTRY_CODE", "DATE_RANGE", "AD_ID"]
    r0 = rows[0]
    assert (r0["SOURCE"], r0["SOURCE_TYPE"], r0["SEARCH_TERM"], r0["COUNTRY_CODE"]) == ("TIKTOK_COMMERCIAL_CONTENT_API", "CONTEUDO_COMERCIAL", "Aviator", "BR")
    assert r0["AD_ID"] in ("101", "102") and r0["TIPO"] == "CONTEÚDO COMERCIAL / ANÚNCIO" and r0["URL_PERFIL"] == "NÃO IDENTIFICADO" and "-" in r0["DATE_RANGE"]
    simple = list(csv.DictReader(io.StringIO(c.get("/api/export?format=csv&kind=simple&scope=confirmed").data.decode("utf-8-sig"))))
    assert list(simple[0])[:3] == ["username", "url_perfil", "url_video"] and simple[0]["url_perfil"] == "NÃO IDENTIFICADO" and simple[0]["observacao_analista"] == "vi no Ad Library"
    mission_csv = list(csv.DictReader(io.StringIO(c.get("/api/export?format=csv&kind=mission&scope=confirmed").data.decode("utf-8-sig"))))
    assert len(mission_csv) == 1 and mission_csv[0]["URL_PERFIL"] == "NÃO IDENTIFICADO" and commercial.EVIDENCE_TEXT in mission_csv[0]["TEXTO_EVIDENCIA"]
    js = c.get("/api/export?format=json&scope=confirmed").json["candidatos"][0]
    assert js["evidences"][0]["meta"]["raw"]["ad"]["id"] in ("101", "102")
    assert c.get("/api/copy?what=profiles").data.decode().strip() == ""                    # sem perfil inventado
    assert c.get("/api/export?format=xlsx&kind=full&scope=all").data[:2] == b"PK"


# ---------------------------------------------------------------- 17. persistência e segredos
def test_persistence_and_no_secrets_in_db(creds, monkeypatch, tmp_path):
    calls = install(monkeypatch, [page(["900"])])
    path = str(tmp_path / "t.db")
    app = create_app(path); c = app.test_client()
    c.put("/api/settings", json={"commercial_api_country": "PT", "commercial_api_terms": ["Aviator"], "commercial_api_range": "custom",
                                 "commercial_api_date_from": "2026-09-01", "commercial_api_date_to": "2026-09-05"})
    st = pipeline.run_query("Aviator", "commercial", c.get("/api/settings").json, hunt="t")
    assert st["new"] == 1 and queries_only(calls)[0]["json"]["filters"] == {
        "ad_published_date_range": {"min": "20260901", "max": "20260905"}, "country_code": "PT"}   # país/datas vêm da CONFIG
    _real_sleep(0.2)
    c2 = create_app(path).test_client()
    s = c2.get("/api/settings").json
    assert s["commercial_api_country"] == "PT" and s["commercial_api_terms"] == ["Aviator"] and s["commercial_api_token_url"] == TOKEN_URL
    d = c2.get("/api/candidates?view=all").json["items"][0]
    det = c2.get(f"/api/candidates/{d['id']}").json
    assert det["evidences"][0]["meta"]["country"] == "PT" and det["evidences"][0]["meta"]["date_range"] == "20260901-20260905"
    raw = b""
    for f in (path, path + "-wal", path + "-shm"):
        if os.path.exists(f):
            raw += open(f, "rb").read()
    for secret in (SECRET, KEY, "TOK1", "TOK2", "access_token"):
        assert secret.encode() not in raw
    assert "TIKTOK_CLIENT_SECRET=\n" in open(".env.example").read() and "TIKTOK_CLIENT_KEY=\n" in open(".env.example").read()
    src = "".join(open(p, encoding="utf-8").read() for p in ("bethunter/commercial.py", "bethunter/config.py", "bethunter/web.py"))
    assert SECRET not in src and KEY not in src


def test_migration_adds_meta_column_and_hunt_to_existing_db(tmp_path, monkeypatch):
    import sqlite3
    p = tmp_path / "antigo.db"
    monkeypatch.setenv("BETHUNTER_DB", str(p))
    db.init_db()
    con = sqlite3.connect(p)
    con.executescript("ALTER TABLE evidences RENAME TO ev_old; CREATE TABLE evidences(id INTEGER PRIMARY KEY, candidate_id INTEGER, kind TEXT, source TEXT, source_url TEXT, query TEXT, url_video TEXT, caption TEXT, text TEXT, hashtags TEXT, mentions TEXT, tags TEXT, collected_at TEXT, dedupe_key TEXT);"
                      "DROP TABLE ev_old; DELETE FROM hunts WHERE kind='commercial'; UPDATE hunts SET name='CAÇA 13 — matriz de consultas (jogos × CTA × financeiro × afiliados)' WHERE kind='matrix';")
    con.commit(); con.close()
    db.init_db()
    with db.connect() as c:
        assert "meta" in [r[1] for r in c.execute("PRAGMA table_info(evidences)")]
        hs = {r["kind"]: r["name"] for r in c.execute("SELECT * FROM hunts")}
        assert hs["commercial"] == "CAÇA 13 — TIKTOK COMMERCIAL CONTENT" and hs["matrix"].startswith("CAÇA 14")


# ---------------------------------------------------------------- 21. status do ambiente
def test_env_states(creds, monkeypatch):
    assert commercial.check()["estado"] == "OK" if install(monkeypatch, [page(["1"])]) is not None else False
    for status, esperado in ((401, "AUTENTICAÇÃO FALHOU"), (403, "SEM PERMISSÃO PARA O ENDPOINT"), (429, "RATE LIMITED"), (500, "ERRO")):
        install(monkeypatch, [resp(status)])
        assert commercial.check()["estado"] == esperado
    install(monkeypatch, [resp(None, err="tempo esgotado")])
    assert commercial.check() == {"estado": "ERRO", "detalhe": "tempo esgotado"}
    install(monkeypatch, token=resp(401, {}))
    assert commercial.check()["estado"] == "AUTENTICAÇÃO FALHOU"
    monkeypatch.setattr(net, "fetch", lambda url, **k: {"status": 200, "url": url, "location": None, "error": None, "text": "ok"})
    install(monkeypatch, [page(["1"])])
    r = envcheck.test_all()
    assert r["TIKTOK COMMERCIAL API"]["estado"] == "OK" and "TIKTOK" in r and "BANCO" in r and SECRET not in json.dumps(r)


# ---------------------------------------------------------------- 18. modo missão
def ddg_fake(monkeypatch):
    """ddg sintético; 'commercial' vai para o provider real (net.post simulado)."""
    ddg_calls = []
    def rs(source, q):
        if source == "commercial":
            return commercial.search(q)
        ddg_calls.append(q)
        h = int(hashlib.md5(q.encode()).hexdigest()[:6], 16)
        u = f"org_{h % 100000}"
        return ([{"username": u, "profile_url": f"https://www.tiktok.com/@{u}", "video_url": f"https://www.tiktok.com/@{u}/video/{h}",
                  "text": "Fortune Tiger cadastre-se pelo link na bio saque https://xyzbet.bet.br/?aff=%d" % (h % 900),
                  "source": "DuckDuckGo", "query": q, "source_url": "x"}], None, "x")
    monkeypatch.setattr(sources, "run_source", rs)
    return ddg_calls


def mission_setup(terms):
    with db.connect() as c:
        c.execute("UPDATE hunts SET enabled=0 WHERE kind NOT IN ('matrix','commercial')")
        db.save_settings(c, {"matrix": {"A": ["Fortune Tiger"], "B": ["link na bio"], "C": ["saque"], "D": ["código"]},
                             "commercial_api_terms": terms, "enrich_after_search": False, "priority_queries": []})


def test_mission_includes_commercial_plus_other_providers(creds, monkeypatch):
    ddg_calls = ddg_fake(monkeypatch)
    n = iter(range(1, 10000))
    calls = install(monkeypatch, fn=lambda k: page([str(next(n)) for _ in range(3)]))
    mission_setup(["Fortune Tiger", "bet", "Aviator"])
    j = pipeline.Job("m")
    r = mission.run_mission(j, 200, 1, "rapido", ["ddg", "commercial"])
    terms = [c["json"]["search_term"] for c in queries_only(calls)]
    assert terms == ["Fortune Tiger", "Aviator"]                                            # 'bet' nunca; só os termos da CAÇA 13
    assert len(ddg_calls) >= len(mission.generate_queries(S())) and not any('" "' in t for t in terms)   # matriz não vai para a API oficial
    with db.connect() as c:
        ads = c.execute("SELECT COUNT(*) FROM candidates WHERE username LIKE 'ad:%'").fetchone()[0]
        org = c.execute("SELECT COUNT(*) FROM candidates WHERE username LIKE 'org_%'").fetchone()[0]
        srcs = {r[0] for r in c.execute("SELECT DISTINCT source FROM discoveries")}
        assert ads == 6 and org > 0 and {"TIKTOK_COMMERCIAL_CONTENT_API", "DuckDuckGo"} <= srcs      # consolidados no mesmo pipeline
        assert c.execute("SELECT COUNT(*) FROM candidates WHERE status='CONFIRMADO'").fetchone()[0] == 0
        # candidatos consolidados sem duplicar e clusters continuam funcionando
        assert queries.stats(c)["unicos"] == ads + org and queries.stats(c)["brutos"] >= 6


def test_mission_pauses_failing_commercial_but_other_sources_continue(creds, monkeypatch):
    ddg_calls = ddg_fake(monkeypatch)
    install(monkeypatch, fn=lambda k: resp(403))
    mission_setup(["Fortune Tiger", "Aviator", "Mines", "Crash", "Plinko"])
    j = pipeline.Job("m")
    r = mission.run_mission(j, 200, 0, "rapido", ["ddg", "commercial"])
    assert r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED" and r["status"] == "COMPLETED"          # falha de fonte NÃO encerra a missão
    assert "commercial" in r["fontes_pausadas"] and any("pausada" in l and "TIKTOK_COMMERCIAL_CONTENT_API" in l for l in j.lines)
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM candidates WHERE username LIKE 'org_%'").fetchone()[0] > 0   # ddg não foi afetado
        errs = c.execute("SELECT COUNT(*) FROM search_log WHERE source='TIKTOK_COMMERCIAL_CONTENT_API' AND errors=1").fetchone()[0]
        assert errs >= 3                                                                     # falhas reais registradas, sem insistir para sempre
        assert c.execute("SELECT COUNT(*) FROM mission_tasks WHERE status='PENDING' OR status='RETRY'").fetchone()[0] == 0


def test_mission_without_commercial_selected_never_calls_it(creds, monkeypatch):
    ddg_fake(monkeypatch)
    calls = install(monkeypatch, fn=lambda k: page(["1"]))
    mission_setup(["Fortune Tiger"])
    mission.run_mission(pipeline.Job("m"), 200, 0, "rapido", ["ddg"])
    assert queries_only(calls) == []


def test_hunt13_editable_and_ui_flags(creds, monkeypatch, tmp_path):
    ddg_fake(monkeypatch)
    calls = install(monkeypatch, fn=lambda k: page([f"x{k}"]))
    c = create_app(str(tmp_path / "h.db")).test_client()
    h = [x for x in c.get("/api/hunts").json if x["kind"] == "commercial"][0]
    assert h["sources"] == ["commercial"] and h["enabled"]
    assert c.put(f"/api/hunts/{h['id']}", json={"queries": ["Spaceman extra"]}).json["ok"]
    c.put("/api/settings", json={"commercial_api_terms": ["Tigrinho", "slot"], "commercial_api_token_url": TOKEN_URL, "request_delay": 0})
    jid = c.post("/api/hunts/run", json={"ids": [h["id"]]}).json["job"]
    for _ in range(100):
        jj = c.get(f"/api/jobs/{jid}").json
        if jj["status"] != "running":
            break
        _real_sleep(0.05)
    assert jj["status"] == "done"
    assert [x["json"]["search_term"] for x in queries_only(calls)] == ["Spaceman extra", "Tigrinho", "slot"]
    html = open("bethunter/static/app.js", encoding="utf-8").read()
    assert "TikTok Commercial Content API" in html and "NÃO CONFIGURADA" in html and "caCountry" in html and "INTERVALO PERSONALIZADO" in html
