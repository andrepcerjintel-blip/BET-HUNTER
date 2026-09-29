"""TikTok Search Local (serviço externo via HTTP local): provider, paginação por page_token, zero resultados × falha real,
prioridade/pausa na missão e métricas por fonte. Serviço SIMULADO (net.post/net.fetch); o real depende do teste local."""
import hashlib
import json
import time

import pytest

from bethunter import db, net, pipeline, queries, scoring, sources, tiktok_local, mission
from bethunter.web import create_app

URL = "http://127.0.0.1:8000"
_real_sleep = time.sleep


def resp(status=200, js=None, err=None, retry_after=None):
    return {"status": status, "json": js, "text": "", "retry_after": retry_after, "error": err}


def rec(vid, user, desc="Fortune Tiger pagando, cadastre-se pelo link na bio", tags=("tigrinho",), views=1000):
    return {"id": str(vid), "description": desc, "create_time": "2026-09-25T10:00:00+00:00", "author_username": user,
            "author_id": "77" + str(vid)[-4:], "region_code": "BR", "view_count": views, "like_count": 10, "comment_count": 2,
            "share_count": 1, "hashtags": list(tags), "music_id": "1", "music_title": "som original", "duration": 15000,
            "source_term": "search:x"}


def page(recs, token=None, more=None):
    return resp(200, {"query": "x", "type": "keyword", "device": "dev0", "count": len(recs), "cursor": 0, "next_cursor": 20,
                      "has_more": bool(token) if more is None else more, "page_token": token, "elapsed_s": 1.0, "results": recs})


def install(monkeypatch, pages=None, fn=None, health=None):
    calls, it = [], iter(pages or [])
    def fake_post(url, headers=None, data=None, json_body=None, params=None, timeout=None, allow_local=False):
        assert allow_local and url == URL + "/search"
        calls.append(json_body)
        return fn(json_body, len(calls)) if fn else next(it)
    monkeypatch.setattr(net, "post", fake_post)
    h = health or {"status": "ok", "device_count": 5, "capacity_remaining_today": 1200}
    monkeypatch.setattr(net, "fetch", lambda url, **k: {"status": 200, "url": url, "location": None, "error": None, "text": json.dumps(h)}
                        if url == URL + "/health" else {"status": None, "url": url, "location": None, "error": "simulado", "text": ""})
    return calls


@pytest.fixture(autouse=True)
def fast(env, monkeypatch):
    monkeypatch.setattr(tiktok_local.time, "sleep", lambda s: None)
    with db.connect() as c:
        db.save_settings(c, {"request_delay": 0, "enrich_after_search": False})


def S(**kw):
    with db.connect() as c:
        s = db.get_settings(c)
    s.update(kw)
    return s


# ---------------------------------------------------------------- mapeamento de consulta e formato do pedido
def test_query_type_mapping():
    assert tiktok_local.classify_query("Fortune Tiger") == ("keyword", "Fortune Tiger")
    assert tiktok_local.classify_query("#fortunetiger") == ("hashtag", "fortunetiger")
    assert tiktok_local.classify_query("@usuario") == ("user", "usuario")
    assert tiktok_local.classify_query('"link na bio" "saque"') == ("keyword", "link na bio saque")


def test_request_body_only_documented_fields(monkeypatch):
    calls = install(monkeypatch, [page([rec(1, "a_user")])])
    hits, err, ep = tiktok_local.search("Fortune Tiger", S())
    assert err is None and ep == URL + "/search" and len(hits) == 1
    assert calls[0] == {"type": "keyword", "query": "Fortune Tiger", "limit": 20,
                        "filters": {"sort_type": "0", "publish_time": "7"}}                       # 1ª página sem page_token
    for rg, code in (("24h", "1"), ("7d", "7"), ("30d", "30"), ("90d", "90"), ("180d", "180"), ("all", "0")):
        calls = install(monkeypatch, [page([])])
        tiktok_local.search("#fortunetiger", S(tiktok_search_recency=rg, tiktok_search_limit=30))
        assert calls[0]["filters"]["publish_time"] == code and calls[0]["type"] == "hashtag" and calls[0]["query"] == "fortunetiger" and calls[0]["limit"] == 30
    calls = install(monkeypatch, [page([])])
    tiktok_local.search("@usuario", S())
    assert calls[0] == {"type": "user", "query": "usuario", "limit": 20}                        # filtros não vão em busca de usuário


# ---------------------------------------------------------------- paginação por page_token
def test_pagination_reenvia_page_token_nao_next_cursor(monkeypatch):
    calls = install(monkeypatch, [page([rec(1, "u1"), rec(2, "u2")], token="T1"), page([rec(3, "u3")], token="T2"), page([rec(4, "u4")], token=None, more=False)])
    hits, err, _ = tiktok_local.search("Aviator", S())
    assert err is None and [h["meta"]["video_id"] for h in hits] == ["1", "2", "3", "4"]
    assert [c.get("page_token") for c in calls] == [None, "T1", "T2"]
    assert all("cursor" not in c and "next_cursor" not in c for c in calls)
    assert all(c["query"] == "Aviator" and c["filters"] == calls[0]["filters"] for c in calls)      # mesma consulta em todas as páginas


def test_pagination_stops_and_guards(monkeypatch):
    calls = install(monkeypatch, [page([rec(1, "u1")], token="T", more=False)])                # has_more=false com token
    tiktok_local.search("Mines", S()); assert len(calls) == 1
    calls = install(monkeypatch, [page([rec(1, "u1")], token=None, more=True)])                # página mesclada: sem token
    tiktok_local.search("Mines", S()); assert len(calls) == 1
    n = iter(range(1, 999))
    calls = install(monkeypatch, fn=lambda b, k: page([rec(next(n), "usr_x")], token=f"T{k}"))      # sempre has_more
    hits, err, _ = tiktok_local.search("Mines", S(tiktok_search_max_pages=4))
    assert len(calls) == 4 and len(hits) == 4 and err is None                                   # limite de páginas
    calls = install(monkeypatch, [page([rec(1, "usr_x")], token="SAME"), page([rec(2, "usr_x")], token="SAME")])   # token repetido
    tiktok_local.search("Mines", S()); assert len(calls) == 2
    calls = install(monkeypatch, [page([rec(1, "usr_x")], token="T"), page([], token="T2")])        # página vazia encerra
    assert len(tiktok_local.search("Mines", S())[0]) == 1 and len(calls) == 2


def test_user_cancel_stops_pagination(monkeypatch):
    calls = install(monkeypatch, fn=lambda b, k: page([rec(k, "usr_x")], token=f"T{k}"))
    hits, err, _ = tiktok_local.search("Plinko", S(), stop=lambda: len(calls) >= 2)
    assert len(calls) == 2 and err == "interrompida pelo usuário" and len(hits) == 2


# ---------------------------------------------------------------- normalização
def test_normalize_video_url_only_when_safe():
    s = S()
    h = tiktok_local.normalize(rec(7658136483181776158, "Cc.Tyler1", tags=("Tigrinho", "SaqueRapido")), "keyword", "Fortune Tiger", s)
    assert h["username"] == "cc.tyler1" and h["video_url"] == "https://www.tiktok.com/@cc.tyler1/video/7658136483181776158"
    assert h["hashtags"] == ["tigrinho", "saquerapido"] and h["source"] == "TIKTOK_SEARCH_LOCAL" and h["query"] == "Fortune Tiger"
    m = h["meta"]
    assert m["video_id"] == "7658136483181776158" and m["region_code"] == "BR" and m["create_time"].startswith("2026-09-25")
    assert m["metrics"] == {"views": 1000, "likes": 10, "comments": 2, "shares": 1} and m["source_type"] == "BUSCA_NATIVA_TIKTOK"
    assert tiktok_local.normalize(rec("abc", "user_ok"), "keyword", "t", s)["video_url"] == ""      # id inválido: não inventa URL
    assert tiktok_local.normalize(rec(1, "nome inválido!"), "keyword", "t", s) is None               # sem username válido: sem candidato
    assert tiktok_local.normalize({"id": "1", "description": "x"}, "keyword", "t", s) is None
    u = tiktok_local.normalize({"type": "user", "username": "nasa", "display_name": "NASA", "user_id": "9", "follower_count": 5}, "user", "nasa", s)
    assert u["username"] == "nasa" and u["video_url"] == "" and u["text"] == ""


# ---------------------------------------------------------------- zero resultados × falha real
def test_zero_results_is_valid_not_failure(monkeypatch):
    install(monkeypatch, [page([]), page([])])
    hits, err, _ = tiktok_local.search("nada existe", S())
    assert hits == [] and err is None
    st = pipeline.run_query("nada existe", "tiktok_local", S(), hunt="t")
    assert st["found"] == 0 and st["error"] is None
    with db.connect() as c:
        r = queries.search_log(c)[0]
        assert r["errors"] == 0 and r["found"] == 0 and r["source"] == "TIKTOK_SEARCH_LOCAL"


@pytest.mark.parametrize("script,expect", [
    ([resp(None, err="falha de conexão (sem internet)")], "OFFLINE"),
    ([resp(None, err="tempo esgotado")], "tempo esgotado"),
    ([resp(429), resp(429)], "RATE LIMITED"),
    ([resp(502, {"detail": "TikTok request failed: soft error"})], "TikTok recusou"),
    ([resp(503), resp(503)], "serviço ocupado"),
    ([resp(422, {"detail": "page_token invalid"})], "recusado"),
    ([resp(500)], "HTTP 500"),
    ([resp(200, {"sem": "results"})], "resposta inválida"),
    ([resp(200, None)], "resposta inválida"),
])
def test_real_failures_are_errors(monkeypatch, script, expect):
    install(monkeypatch, script)
    hits, err, _ = tiktok_local.search("Tigrinho", S())
    assert hits == [] and expect in err


def test_429_retries_once_and_partial_results_kept(monkeypatch):
    calls = install(monkeypatch, [resp(429, retry_after=3), page([rec(1, "u1")], token="T"), resp(502)])
    hits, err, _ = tiktok_local.search("Tigrinho", S())
    assert [h["meta"]["video_id"] for h in hits] == ["1"] and "HTTP 502" in err and len(calls) == 3


def test_check_states(monkeypatch):
    install(monkeypatch)
    assert tiktok_local.check(S())["estado"] == "OK" and tiktok_local.check(S())["detalhe"].startswith("TikTok Search Local disponível.")
    assert tiktok_local.check(S(tiktok_search_local_url=""))["estado"] == "NÃO CONFIGURADO"
    install(monkeypatch, health={"status": "ok", "capacity_remaining_today": 0})
    assert tiktok_local.check(S())["estado"] == "RATE LIMITED"
    monkeypatch.setattr(net, "fetch", lambda url, **k: {"status": None, "error": "falha de conexão", "text": "", "url": url, "location": None})
    assert tiktok_local.check(S())["estado"] == "OFFLINE"
    monkeypatch.setattr(net, "fetch", lambda url, **k: {"status": 500, "error": None, "text": "", "url": url, "location": None})
    assert tiktok_local.check(S())["estado"] == "ERRO"
    monkeypatch.setattr(net, "fetch", lambda url, **k: {"status": 200, "error": None, "text": "<html>outra coisa</html>", "url": url, "location": None})
    assert tiktok_local.check(S())["estado"] == "ERRO"
    monkeypatch.setattr(net, "fetch", lambda url, **k: {"status": 429, "error": None, "text": "", "url": url, "location": None})
    assert tiktok_local.check(S())["estado"] == "RATE LIMITED"


# ---------------------------------------------------------------- candidatos, evidências, deduplicação
def test_users_dedupe_and_multiple_videos_same_user(monkeypatch):
    install(monkeypatch, [page([rec(1, "promo_a", tags=("tigrinho",)), rec(2, "promo_a", desc="Aviator sinais grupo vip", tags=("aviator",)),
                                rec(3, "promo_b")]),
                          page([rec(2, "promo_a", desc="Aviator sinais grupo vip", tags=("aviator",)), rec(4, "promo_a")])])
    s = S()
    a = pipeline.run_query("Fortune Tiger", "tiktok_local", s, hunt="t")
    b = pipeline.run_query("Aviator", "tiktok_local", s, hunt="t")
    assert (a["found"], a["new"]) == (3, 2) and (b["found"], b["new"]) == (2, 0)
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 2               # usuários únicos
        d = queries.candidate_detail(c, c.execute("SELECT id FROM candidates WHERE username='promo_a'").fetchone()[0])
        vids = sorted(e["url_video"] for e in d["evidences"])
        assert vids == [f"https://www.tiktok.com/@promo_a/video/{i}" for i in (1, 2, 4)]       # 3 vídeos = 3 evidências; vídeo 2 não duplica
        e0 = [e for e in d["evidences"] if e["url_video"].endswith("/1")][0]
        assert e0["kind"] == "video" and e0["hashtags"] == ["tigrinho"] and e0["meta"]["source"] == "TIKTOK_SEARCH_LOCAL"
        assert e0["meta"]["term"] == "Fortune Tiger" and e0["meta"]["metrics"]["views"] == 1000 and e0["source"] == "TIKTOK_SEARCH_LOCAL"
        assert d["sources"] == ["TIKTOK_SEARCH_LOCAL"] and "aviator" in d["hashtags"] and "tigrinho" in d["hashtags"]


def test_engagement_metrics_never_change_score(monkeypatch):
    install(monkeypatch, [page([rec(1, "low_views", views=3), rec(2, "viral", views=99_000_000)])])
    pipeline.run_query("Fortune Tiger", "tiktok_local", S(), hunt="t")
    with db.connect() as c:
        sc = {r["username"]: r["score"] for r in c.execute("SELECT username, score FROM candidates")}
    assert sc["low_views"] == sc["viral"] > 0


def test_offline_service_does_not_break_other_sources(monkeypatch):
    monkeypatch.setattr(net, "fetch", lambda url, **k: {"status": None, "error": "falha de conexão", "text": "", "url": url, "location": None})
    monkeypatch.setattr(sources, "run_source", lambda src, q: ([{"username": "via_ddg", "profile_url": "https://www.tiktok.com/@via_ddg", "video_url": "",
                                                                    "text": "Fortune Tiger link na bio", "source": "DuckDuckGo", "query": q, "source_url": "x"}], None, "x")
                        if src == "ddg" else ([], "OFFLINE: falha de conexão", "x"))
    r = pipeline.start_job("m", lambda j: mission.run_mission(j, 200, 0, "rapido", ["tiktok_local", "ddg"]), sync=True)
    assert r.status == "done" and r.result["consultas"] > 0
    assert r.stats["fontes_estado"]["TIKTOK_SEARCH_LOCAL"] == "OFFLINE" and "tiktok_local" not in r.stats["fontes_ativas"]   # aviso, não erro fatal
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM candidates WHERE username='via_ddg'").fetchone()[0] == 1


# ---------------------------------------------------------------- missão: prioridade, pausa, zero resultados, backoff
def mission_env(monkeypatch, local_fn, other_fn=None):
    """local: provider real sobre net.post simulado; ddg/bing/tiktok: run_source simulado (contando chamadas)."""
    seen = []
    install(monkeypatch, fn=local_fn)
    def rs(src, q):
        seen.append((src, q))
        if src == "tiktok_local":
            return tiktok_local.search(q)
        return other_fn(src, q) if other_fn else ([], None, "x")
    monkeypatch.setattr(sources, "run_source", rs)
    return seen


def gen_local(b, k):
    h = int(hashlib.md5((b["query"] + str(b.get("page_token"))).encode()).hexdigest()[:6], 16)
    return page([rec(h * 10 + i, f"usr_{(h + i) % 40}", desc=f"Fortune Tiger cadastre-se pelo link na bio saque {b['query']}") for i in range(5)])


def cfg_mission(priority=None, **kw):
    with db.connect() as c:
        c.execute("UPDATE hunts SET enabled=0 WHERE kind!='matrix'")
        db.save_settings(c, {"priority_queries": priority if priority is not None else ["Fortune Tiger", "Tigrinho", "Aviator"],
                             "matrix": {"A": ["Mines", "Plinko"], "B": ["link na bio"], "C": ["saque"], "D": ["código"]}, **kw})


def test_mission_priority_local_first_and_matrix_only_on_local(monkeypatch):
    seen = mission_env(monkeypatch, gen_local)
    cfg_mission()
    j = pipeline.Job("m")
    r = mission.run_mission(j, 200, 0, "rapido", ["ddg", "bing", "tiktok_local", "tiktok"])
    assert r["consultas"] == 3 + len(mission.generate_queries(S()))
    pr = [s for s in seen if s[1] in ("Fortune Tiger", "Tigrinho", "Aviator")]
    assert [s[0] for s in pr[:4]] == ["tiktok_local", "bing", "ddg", "tiktok"]              # ordem de prioridade por consulta
    matrix = [s for s in seen if '" "' in s[1] or s[1] not in ("Fortune Tiger", "Tigrinho", "Aviator")]
    assert matrix and {s[0] for s in matrix} == {"tiktok_local"}                            # matriz não gasta fontes fracas
    assert j.stats["fontes_estado"]["TIKTOK_SEARCH_LOCAL"] == "OK" and r["unicos"] > 0


def test_zero_results_never_pauses_source_but_lowers_priority(monkeypatch):
    seen = mission_env(monkeypatch, lambda b, k: page([]))
    cfg_mission(priority=[f"consulta valida {i}" for i in range(14)], matrix={"A": ["Mines"], "B": ["link na bio"], "C": [], "D": []})
    with db.connect() as c:
        db.save_settings(c, {"matrix_pairs": ["AB"], "zero_deprioritize": 10})
    j = pipeline.Job("m")
    r = mission.run_mission(j, 200, 0, "rapido", ["tiktok_local", "bing"])
    assert r["fontes_pausadas"] == [] and "tiktok_local" in r["fontes_reduzidas"]           # reduzida, NÃO pausada
    assert any("prioridade reduzida (não é erro)" in l for l in j.lines) and not any("pausada" in l for l in j.lines)
    with db.connect() as c:
        assert c.execute("SELECT SUM(errors) FROM search_log WHERE source='TIKTOK_SEARCH_LOCAL'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM search_log WHERE source='TIKTOK_SEARCH_LOCAL' AND found=0 AND errors=0").fetchone()[0] >= 10


def test_real_failures_pause_after_limit_zero_in_between_resets(monkeypatch):
    script = iter([502, 502, "zero", 502, 502, 502, 502])                                     # zero (válido) zera a contagem
    def fn(b, k):
        v = next(script, 502)
        return page([]) if v == "zero" else resp(v)
    seen = mission_env(monkeypatch, fn, lambda s, q: ([], None, "x"))
    cfg_mission(priority=[f"q{i}" for i in range(8)], matrix={"A": [], "B": [], "C": [], "D": []})
    j = pipeline.Job("m")
    r = mission.run_mission(j, 200, 0, "rapido", ["tiktok_local", "bing"])
    assert r["fontes_pausadas"] == ["tiktok_local"]
    calls_local = [s for s in seen if s[0] == "tiktok_local"]
    assert len(calls_local) == 6                                                             # 2 falhas, 1 zero (reset), 3 falhas -> pausa
    assert any("pausada" in l and "3 falhas seguidas" in l for l in j.lines)


def test_rate_limit_reduces_pace_and_continues_with_other_sources(monkeypatch):
    slept = []
    monkeypatch.setattr(mission.time, "sleep", lambda s: slept.append(s))
    seen = mission_env(monkeypatch, lambda b, k: resp(429), lambda s, q: ([{"username": "ok_bing", "profile_url": "https://www.tiktok.com/@ok_bing", "video_url": "",
                                                                             "text": "Fortune Tiger link na bio", "source": "Bing", "query": q, "source_url": "x"}], None, "x"))
    cfg_mission(priority=["a1", "a2", "a3", "a4"], matrix={"A": [], "B": [], "C": [], "D": []})
    j = pipeline.Job("m")
    r = mission.run_mission(j, 200, 0, "rapido", ["tiktok_local", "bing"])
    assert any("rate limit — ritmo reduzido" in l for l in j.lines) and any(x >= 5 for x in slept)
    assert r["fontes_pausadas"] == ["tiktok_local"]
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM candidates WHERE username='ok_bing'").fetchone()[0] == 1


def test_at_user_queries_only_local_and_hashtag_seeds_respect_depth(monkeypatch):
    seen = mission_env(monkeypatch, gen_local, lambda s, q: ([], None, "x"))
    cfg_mission(priority=["@alguem", "Fortune Tiger"], matrix={"A": [], "B": [], "C": [], "D": []})
    r0 = mission.run_mission(pipeline.Job("m"), 200, 0, "rapido", ["tiktok_local", "bing"])
    assert [s[0] for s in seen if s[1] == "@alguem"] == ["tiktok_local"]
    assert r0["consultas"] == 2 and not any(q.startswith("#") for _, q in seen)             # profundidade 0: sem hashtags como sementes
    seen.clear()
    with db.connect() as c:
        c.execute("DELETE FROM search_log")
    r2 = mission.run_mission(pipeline.Job("m"), 200, 1, "rapido", ["tiktok_local", "bing"])
    tags = {q for _, q in seen if q.startswith("#")}
    assert tags and "#tigrinho" in tags and r2["consultas"] > 2                             # hashtags de candidatos promissores viram buscas
    assert {s for s, q in seen if q.startswith("#")} >= {"tiktok_local"}


# ---------------------------------------------------------------- métricas por fonte, ambiente, API
def test_source_metrics_and_env_endpoint(monkeypatch, tmp_path):
    install(monkeypatch, [page([rec(1, "m1"), rec(2, "m2")]), page([]), resp(502)])
    s = S()
    for q in ("a", "b", "c"):
        pipeline.run_query(q, "tiktok_local", s, hunt="t")
    with db.connect() as c:
        m = {r["fonte"]: r for r in queries.source_metrics(c)}["TIKTOK_SEARCH_LOCAL"]
    assert (m["consultas"], m["resultados_brutos"], m["candidatos_unicos"], m["zero_resultados"], m["erros"]) == (3, 2, 2, 1, 1)
    assert m["resultados_por_consulta"] == 0.67 and m["candidatos_por_consulta"] == 0.67 and m["tempo_medio_ms"] >= 0
    c = create_app(str(tmp_path / "m.db")).test_client()
    assert c.get("/api/source-metrics").status_code == 200
    install(monkeypatch)
    assert c.get("/api/env").json["TIKTOK SEARCH LOCAL"]["estado"] == "OK"
    env = c.post("/api/env/test").json
    assert env["TIKTOK SEARCH LOCAL"]["estado"] == "OK" and "TikTok Search Local disponível." in env["TIKTOK SEARCH LOCAL"]["detalhe"]
    monkeypatch.setattr(net, "fetch", lambda url, **k: {"status": None, "error": "falha de conexão", "text": "", "url": url, "location": None})
    assert c.post("/api/env/test").json["TIKTOK SEARCH LOCAL"]["estado"] == "OFFLINE"        # offline não derruba nada
    assert c.get("/").status_code == 200


def test_settings_and_priority_order_defaults():
    from bethunter import config
    s = config.default_settings()
    assert s["tiktok_search_local_url"] == "http://127.0.0.1:8000" and s["tiktok_search_recency"] == "7d" and s["tiktok_search_max_pages"] == 10
    assert sources.by_priority(["tiktok_tag", "ddg", "tiktok", "bing", "commercial", "tiktok_local"]) == \
        ["tiktok_local", "commercial", "bing", "ddg", "tiktok", "tiktok_tag"]
    assert s["mission_sources"] == sources.by_priority(s["mission_sources"]) and len(s["priority_queries"]) == 12
