import hashlib
import json
import time

import pytest

from bethunter import config, db, exporter, mission, pipeline, queries, sources, visual
from bethunter.web import create_app

DOMS = ["xyzbet.bet.br", "tigrefake.bet.br", "pagafake.bet.br"]


def gen_source(calls=None, err=None):
    """Fonte de busca sintética: cada consulta devolve perfis distintos (4 promotores + 2 ruídos)."""
    def run_source(source, q):
        if calls is not None:
            calls.append((source, q))
        if err:
            return [], err, "x"
        h = int(hashlib.md5(q.encode()).hexdigest()[:6], 16)
        hits = []
        for k in range(6):
            u = f"gen_{h % 100000}_{k}"
            text = (f"Cadastre-se pelo link na bio https://{DOMS[h % 3]}/?aff={h % 900 + 100} Fortune Tiger pagando, saque na hora "
                    f"código TIG{h % 90}") if k < 4 else "reportagem sobre as bets e projeto de lei"
            hits.append({"username": u, "profile_url": f"https://www.tiktok.com/@{u}", "video_url": f"https://www.tiktok.com/@{u}/video/{h}{k}",
                         "text": text, "source": "DuckDuckGo", "query": q, "source_url": "https://x"})
        return hits, None, "x"
    return run_source


def only_matrix(small=True, extra=None):
    with db.connect() as c:
        c.execute("UPDATE hunts SET enabled=0 WHERE kind!='matrix'")
        patch = {"request_delay": 0, "enrich_after_search": False}
        if small:
            patch["matrix"] = {"A": ["Fortune Tiger", "Aviator"], "B": ["link na bio", "cadastre-se"], "C": ["saque"], "D": ["código"]}
        patch.update(extra or {})
        db.save_settings(c, patch)


def job():
    return pipeline.Job("t")


def cfg():
    with db.connect() as c:
        return db.get_settings(c)


# ---------------------------------------------------------------- matriz / consultas
def test_matrix_no_absurd_no_dupes_all_pairs():
    s = config.default_settings()
    qs = mission.generate_queries(s)
    assert len(qs) == len(set(qs)) and len(qs) > 150
    assert '"Aviator" "cadastre-se"' in qs and '"Fortune Tiger" "saque"' in qs and '"link na bio" "saque"' in qs
    assert '"link na bio" "cadastre-se"' not in qs and '"saque" "PIX"' not in qs   # mesmo grupo = absurdo
    first_pairs = {q for q in qs[:5]}
    assert len(first_pairs) == 5 and len({frozenset(x.split('" "')) for x in qs}) == len(qs)


def test_generic_terms_never_alone(env):
    s = config.default_settings()
    for g in ("bet", "Bets", "aposta", "apostas", "cassino", "mines"):
        assert "link na bio" in mission.sanitize_query(g, s)
    assert mission.sanitize_query('"Fortune Tiger" "link na bio"', s) == '"Fortune Tiger" "link na bio"'
    with db.connect() as c:
        st = db.get_settings(c)
    pipeline.run_query("bets", "ddg", st)
    with db.connect() as c:
        assert "link na bio" in queries.search_log(c)[0]["query"]


def test_default_hunts_have_no_bare_generic_terms():
    s = config.default_settings()
    generic = {mission.norm(g) for g in s["generic_alone"]}
    for name, kind, qs in config.DEFAULT_HUNTS:
        for q in qs:
            assert mission.norm(q).strip('"') not in generic, (name, q)


# ---------------------------------------------------------------- missão
def test_mission_generates_volume_with_progress_and_depth(env, monkeypatch):
    monkeypatch.setattr(sources, "run_source", gen_source())
    only_matrix()
    j = job()
    r = mission.run_mission(j, goal=200, depth=2, mode="rapido", sources_list=["ddg"])
    assert r["motivo"].startswith("pool qualificado suficiente") and r["unicos"] >= 500   # não parou em 20/50: seguiu até 500
    base = len(mission.generate_queries(cfg()))
    assert r["consultas"] > base                                      # consultas derivadas foram executadas
    assert r["brutos"] == 6 * r["consultas"]
    st = j.stats
    for k in ("consulta_atual", "consultas_feitas", "consultas_total", "brutos", "unicos", "alta", "confirmados", "meta",
              "descartados", "clusters"):
        assert k in st
    assert st["alta"] > 0 and st["clusters"] > 0 and st["meta"] == 200
    with db.connect() as c:
        origins = {r[0] for r in c.execute("SELECT DISTINCT source FROM discoveries")}
        assert "expansão automática" in origins
        assert c.execute("SELECT COUNT(*) FROM candidates WHERE status='CONFIRMADO'").fetchone()[0] == 0   # meta ≠ confirmação


def test_mission_depth_zero_vs_two(env, monkeypatch):
    monkeypatch.setattr(sources, "run_source", gen_source())
    only_matrix()
    r0 = mission.run_mission(job(), 200, 0, "rapido", ["ddg"])
    with db.connect() as c:
        c.execute("DELETE FROM search_log")
    r2 = mission.run_mission(job(), 200, 2, "rapido", ["ddg"])
    assert r0["consultas"] == len(mission.generate_queries(cfg()))
    assert r2["consultas"] > r0["consultas"]


def test_mission_stops_when_goal_reached_by_analyst(env, monkeypatch):
    monkeypatch.setattr(sources, "run_source", gen_source())
    only_matrix()
    pipeline.investigate("promo_fake1")
    with db.connect() as c:
        pipeline.set_status(c, 1, "CONFIRMADO")
    r = mission.run_mission(job(), goal=1, depth=2, mode="rapido", sources_list=["ddg"])
    assert r["motivo"] == "meta de confirmados atingida" and r["consultas"] == 0


def test_mission_stops_on_pool_and_interrupt(env, monkeypatch):
    monkeypatch.setattr(sources, "run_source", gen_source())
    only_matrix()
    r = mission.run_mission(job(), goal=10, depth=2, mode="rapido", sources_list=["ddg"])
    assert r["motivo"].startswith("pool qualificado")
    j = job(); j.cancelled = True
    assert mission.run_mission(j, 200, 2, "rapido", ["ddg"])["motivo"] == "interrompida pelo usuário"


def test_mission_pauses_blocked_source_and_continues_with_others(env, monkeypatch):
    def rs(source, q):
        return ([], "bloqueado por captcha", "x") if source == "ddg" else gen_source()(source, q)
    monkeypatch.setattr(sources, "run_source", rs)
    only_matrix()
    j = job()
    r = mission.run_mission(j, 200, 0, "rapido", ["ddg", "bing"])
    assert r["fontes_pausadas"] == ["ddg"] and r["unicos"] > 0 and any("pausada" in l for l in j.lines)
    monkeypatch.setattr(sources, "run_source", gen_source(err="bloqueado"))
    with db.connect() as c:
        c.execute("DELETE FROM search_log")
    r = mission.run_mission(job(), 200, 0, "rapido", ["ddg"])
    assert "fontes automáticas indisponíveis" in r["motivo"]


def test_progressive_processing_fast_vs_complete(env, monkeypatch):
    monkeypatch.setattr(sources, "run_source", gen_source())
    only_matrix(extra={"matrix": {"A": ["Fortune Tiger"], "B": ["link na bio"], "C": ["saque"], "D": ["código"]}})
    mission.run_mission(job(), 200, 0, "rapido", ["ddg"])
    assert not any("xyzbet" in u or "tigrefake" in u or "pagafake" in u for u in env)     # rápido: nenhuma rede para links
    with db.connect() as c:
        rows = c.execute("SELECT * FROM links").fetchall()
        assert rows and all(r["error"].startswith(pipeline.UNRESOLVED) for r in rows)
        assert any("aff=" in r["params_raw"] and "affiliate" in r["params"] for r in rows)                                    # afiliado preservado mesmo sem resolver
        assert c.execute("SELECT COUNT(*) FROM candidates WHERE score>=70").fetchone()[0] > 0
    with db.connect() as c:
        c.execute("DELETE FROM search_log")
    mission.run_mission(job(), 200, 0, "completo", ["ddg"])
    assert any("bet.br/?aff=" in u for u in env)                                          # completo: promissores resolvem a cadeia
    with db.connect() as c:
        assert not c.execute("SELECT 1 FROM links WHERE error LIKE ? AND candidate_id IN (SELECT id FROM candidates WHERE score>=45)", (pipeline.UNRESOLVED + "%",)).fetchone()


def test_dedupe_before_heavy_processing(env, monkeypatch):
    monkeypatch.setattr(sources, "run_source", gen_source())
    with db.connect() as c:
        s = db.get_settings(c)
    pipeline.run_query("Fortune Tiger link na bio", "ddg", s)
    n1 = len([u for u in env if "bet.br/?aff=" in u])
    assert n1 > 0
    pipeline.run_query("Fortune Tiger link na bio", "bing", s)      # mesmos vídeos/URLs: nada é refeito
    assert len([u for u in env if "bet.br/?aff=" in u]) == n1
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 6


def test_derive_queries_from_collected_data(env):
    r = pipeline.investigate("promo_fake1")
    with db.connect() as c:
        row = c.execute("SELECT * FROM candidates WHERE id=?", (r["id"],)).fetchone()
        s = db.get_settings(c)
    qs = [q for _, _, q in mission.derive_queries(row, s)]
    assert '"xyzbet.bet.br"' in qs and '"xyzbet" link na bio' in qs and '"981" "xyzbet"' in qs
    assert len(qs) <= s["derived_per_candidate"]


def test_expand_this_profile_uses_collected_data(env, monkeypatch):
    monkeypatch.setattr(sources, "run_source", gen_source())
    r = pipeline.investigate("promo_fake1")
    out = pipeline.expand_candidate(r["id"])
    assert out["novos_por_busca"] > 0 and any(b["tipo"] == "domínio" for b in out["buscas"])


# ---------------------------------------------------------------- visual (opcional)
def test_visual_signals_feed_score_and_status(env, monkeypatch):
    pipeline.import_items("@vis_fake")
    with db.connect() as c:
        cid = c.execute("SELECT id FROM candidates").fetchone()[0]
    pipeline.add_manual_evidence(cid, text="olha isso link na bio", url_video="https://www.tiktok.com/@vis_fake/video/9")
    with db.connect() as c:
        d0 = queries.candidate_detail(c, cid)
        assert d0["visual_analysis"] == "não disponível" and d0["status"] in ("NOVO", "BAIXA RELEVÂNCIA", "REVISAR")
    assert visual.run_visual(cid, cfg())["status"] == "não disponível"   # sem motor: não bloqueia
    monkeypatch.setattr(visual, "engine", lambda: "ocr")
    monkeypatch.setattr(visual, "fetch_thumb", lambda u: ("https://t/x.jpg", b"img"))
    sig = {k: False for k in visual.KEYS}; sig.update(slot_ui=True, balance=True, withdraw=True, platform_logo="XYZBet", promo_code="TIGRE10")
    monkeypatch.setattr(visual, "analyze_image", lambda b: {"engine": "ocr", "signals": sig, "text": "saldo R$ 100"})
    with db.connect() as c:
        s = db.get_settings(c)
    assert visual.run_visual(cid, s)["status"] == "disponível"
    with db.connect() as c:
        d = queries.candidate_detail(c, cid)
    keys = {r["key"] for r in d["reasons"]}
    assert {"gameplay", "platform", "payment", "bonus_code"} <= keys and d["visual_analysis"] == "disponível"
    assert "XYZBet" in d["platforms"] and "TIGRE10" in d["codes"] and d["visuals"][0]["engine"] == "ocr"
    assert visual.run_visual(cid, s)["novos"] == 0            # não reprocessa vídeo já analisado


def test_visual_manual_status(env):
    pipeline.import_items("@vm")
    with db.connect() as c:
        cid = c.execute("SELECT id FROM candidates").fetchone()[0]
    pipeline.add_manual_evidence(cid, text="x", tags=["gameplay"])
    with db.connect() as c:
        assert queries.candidate_detail(c, cid)["visual_analysis"] == "manual"


def test_multimodal_json_parse(monkeypatch):
    class R:
        status_code = 200
        def json(self): return {"content": [{"text": 'ok {"slot_ui": true, "pix": true, "platform_logo": null, "x": 1}'}]}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(visual.requests, "post", lambda *a, **k: R())
    r = visual.analyze_multimodal(b"\xff\xd8\xff" + b"0" * 10)
    assert r["signals"]["slot_ui"] and r["signals"]["pix"] and r["signals"]["platform_logo"] is None


# ---------------------------------------------------------------- BAIXA RELEVÂNCIA
def test_low_relevance_kept_hidden_and_discard_stays_manual(env):
    r = pipeline.investigate("noticias_fake")
    with db.connect() as c:
        row = c.execute("SELECT status, status_manual FROM candidates WHERE id=?", (r["id"],)).fetchone()
        assert row["status"] == "BAIXA RELEVÂNCIA" and row["status_manual"] == 0
        assert queries.list_candidates(c, {"view": "results"})["total"] == 0
        assert queries.list_candidates(c, {"view": "results", "status": "BAIXA RELEVÂNCIA"})["total"] == 1
        pipeline.set_status(c, r["id"], "DESCARTADO")
    assert pipeline.investigate("noticias_fake")["cached"]        # registro mantido: não é recoletado
    with db.connect() as c:
        db.save_settings(c, {"auto_discard_low": True})
    pipeline.import_items("@qq")
    r2 = pipeline.investigate("mention_fake", force=True)
    with db.connect() as c:
        assert c.execute("SELECT status FROM candidates WHERE username='mention_fake'").fetchone()[0] in ("DESCARTADO", "NOVO")


def test_migration_old_auto_discards_become_low_relevance(env):
    with db.connect() as c:
        c.execute("INSERT INTO candidates(username,status,status_manual) VALUES('old_auto','DESCARTADO',0),('old_man','DESCARTADO',1)")
        c.execute("DELETE FROM settings WHERE key='migrated_v2'")
    db.init_db()
    with db.connect() as c:
        st = dict(c.execute("SELECT username, status FROM candidates").fetchall())
    assert st["old_auto"] == "BAIXA RELEVÂNCIA" and st["old_man"] == "DESCARTADO"


# ---------------------------------------------------------------- API: missão, desfazer, exportação final, persistência
def test_api_mission_undo_export_persistence(env, tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "run_source", gen_source())
    path = str(tmp_path / "t.db")
    app = create_app(path); c = app.test_client()
    only_matrix()
    j = c.post("/api/mission/start", json={"goal": 200, "depth": 1, "mode": "rapido", "sources": ["ddg"]}).json
    for _ in range(300):
        jj = c.get(f"/api/jobs/{j['job']}").json
        if jj["status"] != "running":
            break
        time.sleep(0.05)
    assert jj["status"] == "done" and jj["stats"]["unicos"] > 0 and jj["result"]["ok"]
    s = c.get("/api/stats").json
    assert s["brutos"] > 0 and s["unicos"] == s["total"] and "em_revisao" in s and s["confirmados"] == 0
    queue = c.get("/api/queue?n=5").json["ids"]
    assert queue
    a, b = queue[0], queue[1]
    prev = c.get(f"/api/candidates/{a}").json
    assert c.post(f"/api/candidates/{a}/status", json={"status": "CONFIRMADO", "note": "obs A"}).json["status"] == "CONFIRMADO"
    c.post(f"/api/candidates/{b}/status", json={"status": "DESCARTADO"})
    # CTRL+Z
    c.post(f"/api/candidates/{b}/restore", json={"status": "REVISAR", "manual": False})
    assert c.get(f"/api/candidates/{b}").json["status"] == "REVISAR"
    assert c.post(f"/api/candidates/{a}/restore", json={"status": prev["status"], "manual": bool(prev["status_manual"])}).json["ok"]
    assert c.get(f"/api/candidates/{a}").json["status"] == prev["status"]
    c.post(f"/api/candidates/{a}/status", json={"status": "CONFIRMADO", "note": "obs A"})
    # cancelar
    j2 = c.post("/api/mission/start", json={"goal": 200, "depth": 3, "mode": "rapido", "sources": ["ddg"]}).json
    assert c.post(f"/api/jobs/{j2['job']}/cancel").json["ok"]
    # exportação final da missão
    r = c.get("/api/export?format=csv&kind=mission&scope=confirmed")
    txt = r.data.decode("utf-8-sig"); lines = txt.splitlines()
    assert lines[0] == "NUMERO,USERNAME,URL_PERFIL,URL_VIDEO,DESCRICAO_EVIDENCIA,TEXTO_EVIDENCIA,PLATAFORMA,DOMINIO,URL_EXTERNA,CODIGO_AFILIADO,DATA_COLETA,OBSERVACAO_ANALISTA"
    assert len(lines) == 2 and lines[1].startswith("1,") and "obs A" in lines[1] and "aff=" in lines[1]
    assert c.get("/api/export?format=xlsx&kind=mission&scope=confirmed").data[:2] == b"PK"
    assert c.get("/api/export?format=xlsx&kind=full&scope=confirmed").data[:2] == b"PK"
    assert "VISUAL_ANALYSIS" in c.get("/api/export?format=csv&kind=full&scope=confirmed").data.decode("utf-8-sig").splitlines()[0]
    assert c.get("/api/export?format=json&scope=confirmed").json["quantidade"] == 1
    # reiniciar aplicação: tudo persiste
    total = c.get("/api/stats").json["total"]
    time.sleep(0.3)
    app2 = create_app(path); c2 = app2.test_client()
    s2 = c2.get("/api/stats").json
    assert s2["total"] == total and s2["confirmados"] == 1 and s2["goal"] == 200
    assert c2.get(f"/api/candidates/{a}").json["analyst_note"] == "obs A"
    assert c2.get("/api/settings").json["mission_depth"] == 3
