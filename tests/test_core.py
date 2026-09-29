import json

import pytest

from bethunter import config, db, exporter, pipeline, queries, scoring, urltools
from bethunter.util import find_terms, norm, extract_urls


def S():
    return config.default_settings()


# ---------------------------------------------------------------- score / falso positivo
def bundle(bio="", caps=(), links=(), name="x"):
    return {"username": "x", "display_name": name, "bio": bio, "links": list(links),
            "evidences": [{"kind": "video", "caption": c, "text": "", "url_video": "https://www.tiktok.com/@x/video/1",
                           "tags": [], "hashtags": []} for c in caps]}


def link(dom, params=(), origin="bio", **kw):
    return {"origin": origin, "url_original": "https://" + dom, "chain": [], "url_final": "https://" + dom,
            "domain_final": dom, "params": list(params), "page_title": "", "page_text": "", **kw}


def test_promoter_scores_high_and_explains():
    b = bundle("Cadastre-se pelo link na bio! código TIGRE10",
               ["Ganhei no Fortune Tiger! saque na hora, cadastre-se"],
               [link("xyzbet.bet.br", [{"param": "aff", "value": "93281", "type": "affiliate", "dominio": "xyzbet.bet.br"}])])
    r = scoring.analyze(b, S())
    keys = {x["key"] for x in r["reasons"]}
    assert {"link_bet", "cta", "gameplay", "payment", "aff_link", "bonus_code"} <= keys
    assert scoring.clamp(r["raw"]) >= 70 and r["priority"] == 1 and r["content_type"] == "AFILIADO"
    assert "93281" in r["main_evidence"] and "aff=93281" in r["affiliate_ids"]
    assert all(x["label"] for x in r["reasons"])


def test_news_is_reduced_and_not_promotion():
    b = bundle("Jornal", ["Reportagem: projeto de lei prevê regulamentação das bets, segundo a polícia investigação de golpe"])
    r = scoring.analyze(b, S())
    assert scoring.clamp(r["raw"]) < 45
    assert r["content_type"] == "JORNALISMO / NOTÍCIA"
    assert any(x["key"] == "r_journalism" for x in r["reasons"])


@pytest.mark.parametrize("txt", ["bet", "Aposta", "cassino", "#tigrinho", "bônus"])
def test_isolated_keyword_is_never_enough(txt):
    r = scoring.analyze(bundle("", [txt]), S())
    assert scoring.clamp(r["raw"]) < 45


def test_cta_without_betting_context_gets_no_points():
    r = scoring.analyze(bundle("Cadastre-se na newsletter, link na bio", ["acesse meu site"]), S())
    assert not any(x["key"] == "cta" for x in r["reasons"])


def test_educational_and_critical():
    r = scoring.analyze(bundle("", ["Entenda a ludopatia: vício em jogo destrói famílias. Procure ajuda. Não caia em golpe das bets"]), S())
    assert r["content_type"] in ("PREVENÇÃO / EDUCATIVO", "CRÍTICA ÀS APOSTAS")
    assert scoring.clamp(r["raw"]) == 0


def test_promoter_with_disclaimer_stays_promoter():
    b = bundle("Jogue com responsabilidade. Cadastre-se pelo link na bio", ["Tigrinho pagando, cadastre-se"],
               [link("xyzbet.bet.br")])
    assert scoring.clamp(scoring.analyze(b, S())["raw"]) >= 70


def test_recurring_pattern():
    caps = [f"Plataforma pagando {i}! Fortune Tiger cadastre-se pelo link" for i in range(5)]
    r = scoring.analyze(bundle("link na bio", caps, [link("xyzbet.bet.br")]), S())
    assert r["recurring"] and any(x["key"] == "padrao_recorrente" for x in r["reasons"])


def test_thresholds_configurable():
    s = S(); s["thresholds"] = {"alta": 30, "revisar": 10}
    assert scoring.classify(35, s).startswith("ALTA")
    assert scoring.classify(20, s) == "REVISAR" and scoring.classify(5, s).startswith("BAIXA")


def test_weights_configurable():
    s = S(); s["weights"]["link_bet"] = 0
    r = scoring.analyze(bundle("", [], [link("xyzbet.bet.br")]), s)
    assert not any(x["key"] == "link_bet" for x in r["reasons"])


# ---------------------------------------------------------------- URLs / afiliados
def test_params_preserved():
    p = urltools.parse_params("https://dominio.com/?aff=93281&Ref=AbC&promocode=X1&utm_campaign=c1&foo=bar")
    d = {(x["param"], x["value"], x["type"]) for x in p}
    assert ("aff", "93281", "affiliate") in d and ("Ref", "AbC", "referral") in d
    assert ("promocode", "X1", "promo") in d and ("utm_campaign", "c1", "campaign") in d
    assert not any(x["param"] == "foo" for x in p)


def test_chain_linktree_shortener_final(env):
    recs = urltools.expand_link("https://linktr.ee/promo_fake1", S())
    r = [x for x in recs if x["domain_final"] == "xyzbet.bet.br"][0]
    assert r["url_original"] == "https://linktr.ee/promo_fake1" and r["aggregator"] == "linktr.ee"
    assert "https://bit.ly/promo_fake1" in r["chain"]
    assert r["url_final"].endswith("aff=981&utm_source=tiktok")
    assert any(p["value"] == "981" for p in r["params"])
    assert any(x["domain_final"] == "t.me" for x in recs)


def test_tiktok_redirect_unwrap():
    assert urltools.unwrap("https://www.tiktok.com/link/v2?scene=bio_url&target=https%3A%2F%2Fa.com%2F%3Faff%3D1") == "https://a.com/?aff=1"


# ---------------------------------------------------------------- pipeline
def test_investigate_end_to_end(env):
    r = pipeline.investigate("@Promo_Fake1")
    assert r["ok"] and r["profile_fetched"] and r["videos_collected"] == 4 and r["evidence_count"] >= 5
    assert r["score"] >= 70 and r["classification"].startswith("ALTA")
    with db.connect() as c:
        d = queries.candidate_detail(c, r["id"])
    assert d["status"] == "REVISAR" and d["domain_final"] == "xyzbet.bet.br"
    assert "aff=981" in d["affiliate_ids"] and "TIGRE981" in d["codes"]
    assert d["evidences"][0]["collected_at"] and d["evidences"][0]["fuso"]
    lk = [l for l in d["links"] if l["domain_final"] == "xyzbet.bet.br"][0]
    assert lk["chain"] and lk["params_raw"].startswith("aff=981")
    assert any(x["tipo"] == "DOMINIO" for x in d["clusters"])
    assert any("perfil-semente" == x["source"] for x in d["discoveries"])


def test_cache_warning_and_force(env):
    pipeline.investigate("promo_fake1")
    r = pipeline.investigate("https://www.tiktok.com/@promo_fake1")
    assert r["cached"] and r["previous"]["score"] >= 70 and r["previous"]["evidencias"] and r["previous"]["data_anterior"]
    assert pipeline.investigate("promo_fake1", force=True)["cached"] is False


def test_news_profile_becomes_low_relevance_not_discarded(env):
    r = pipeline.investigate("noticias_fake")
    with db.connect() as c:
        row = c.execute("SELECT * FROM candidates WHERE id=?", (r["id"],)).fetchone()
    assert row["classification"].startswith("BAIXA") and row["status"] == "BAIXA RELEVÂNCIA"


def test_unavailable_profile_and_blocked_profile_are_pending(env):
    a = pipeline.investigate("gone_fake")
    b = pipeline.investigate("blocked_fake")
    with db.connect() as c:
        ra = c.execute("SELECT * FROM candidates WHERE id=?", (a["id"],)).fetchone()
        rb = c.execute("SELECT * FROM candidates WHERE id=?", (b["id"],)).fetchone()
    assert ra["status"] == "PERFIL INDISPONÍVEL" and ra["evidence_count"] == 0
    assert rb["evidence_count"] == 0 and b["profile_fetched"] is False and b["profile_error"]


def test_dedupe_and_pending_separation(env):
    s = pipeline.import_items("@lista_a\nlista_b\nhttps://www.tiktok.com/@lista_a\nhttps://www.tiktok.com/@lista_c/video/123\n#slotpagando\nxyzbet.com\nlixo com espaco")
    assert s["perfis_novos"] == 3 and s["perfis_duplicados"] == 1 and s["hashtags"] == 1 and s["dominios"] == 1 and s["invalidos"] == 1
    with db.connect() as c:
        assert queries.list_candidates(c, {"view": "results"})["total"] == 0
        pend = queries.list_candidates(c, {"view": "pending"})
        assert pend["total"] == 3 and all(i["pendente"] for i in pend["items"])
        st = db.get_settings(c)
        assert "xyzbet.com" in st["bet_domains"] and "slotpagando" in st["hashtags"]
        cid = pend["items"][0]["id"]
        with pytest.raises(PermissionError):
            pipeline.set_status(c, cid, "CONFIRMADO")


def test_manual_evidence_unlocks_confirmation(env):
    pipeline.import_items("@manual_fake")
    with db.connect() as c:
        cid = c.execute("SELECT id FROM candidates").fetchone()[0]
    pipeline.add_manual_evidence(cid, text="Vi no vídeo: cadastre-se pelo link na bio, Fortune Tiger pagando", url_video="https://www.tiktok.com/@manual_fake/video/55",
                                 link="https://xyzbet.bet.br/?ref=77", tags=["gameplay", "logomarca"])
    with db.connect() as c:
        d = queries.candidate_detail(c, cid)
        assert d["evidence_count"] == 1 and d["score"] >= 70
        assert "ref=77" in d["affiliate_ids"]
        pipeline.set_status(c, cid, "CONFIRMADO", note="ok")
        assert c.execute("SELECT status FROM candidates WHERE id=?", (cid,)).fetchone()[0] == "CONFIRMADO"


def test_manual_status_not_overridden_by_reanalysis(env):
    r = pipeline.investigate("noticias_fake")
    with db.connect() as c:
        pipeline.set_status(c, r["id"], "REVISAR")
    pipeline.investigate("noticias_fake", force=True)
    with db.connect() as c:
        assert c.execute("SELECT status FROM candidates WHERE id=?", (r["id"],)).fetchone()[0] == "REVISAR"


def test_clusters_and_shared_domain_bonus(env):
    for n in ("promo_fake1", "promo_fake2", "promo_fake3"):
        pipeline.investigate(n)
    with db.connect() as c:
        cl = queries.clusters(c, 2)
        dom = [x for x in cl if x["tipo"] == "DOMINIO"][0]
        assert dom["valor"] == "xyzbet.bet.br" and dom["perfis_qtd"] == 3
        assert set(dom["affiliate_ids"]) == {"aff=981", "aff=223", "aff=516"}
        assert any(x["tipo"] == "AGREGADOR" and x["valor"] == "linktr.ee" for x in cl)
        assert not any(x["tipo"] == "AFILIADO_ID" for x in cl)   # IDs diferentes: sem cluster de ID
        d = queries.candidate_detail(c, 1)
        assert any(x["key"] == "shared_domain" for x in d["reasons"])
        # diferentes perfis no mesmo domínio NÃO são deduplicados
        assert c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 3
        assert queries.list_candidates(c, {"cluster": "DOMINIO:xyzbet.bet.br", "view": "all"})["total"] == 3


def test_search_ingest_and_dedupe(env):
    pipeline.investigate("promo_fake1")
    with db.connect() as c:
        s = db.get_settings(c)
    st = pipeline.run_query("Fortune Tiger", "ddg", s, hunt="CAÇA 01", origin=None)
    assert st["found"] == 3 and st["new"] == 2 and st["dups"] == 1
    with db.connect() as c:
        n = queries.list_candidates(c, {"view": "all"})
        names = {i["username"] for i in n["items"]}
        assert {"novo_fake1", "novo_fake2", "promo_fake1"} <= names
        log = queries.search_log(c)[0]
        assert log["found"] == 3 and log["new"] == 2 and log["dups"] == 1 and log["hunt"] == "CAÇA 01"
        d = queries.candidate_detail(c, [i for i in n["items"] if i["username"] == "novo_fake1"][0]["id"])
        assert d["evidences"][0]["url_video"].endswith("/video/3001") and d["evidence_count"] == 1
        assert d["discoveries"][0]["source"] == "DuckDuckGo"
    st2 = pipeline.run_query("Fortune Tiger", "ddg", s)
    assert st2["new"] == 0 and st2["dups"] == 3   # nada é duplicado no banco
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 3


def test_source_failure_is_logged_not_fatal(env):
    with db.connect() as c:
        s = db.get_settings(c)
    st = pipeline.run_query("qualquer", "tiktok", s)      # TikTok sem resultados públicos -> erro registrado
    assert st["found"] == 0 and st["error"]
    with db.connect() as c:
        lg = queries.search_log(c)[0]
        assert lg["errors"] == 1 and lg["error_msg"] and lg["source"] == "TikTok Search"
    # e a ferramenta continua operando com fonte manual
    pipeline.import_items("@ainda_funciona")
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1


def test_expand_mentions_and_related(env):
    with db.connect() as c:
        cid, _ = pipeline.upsert_candidate(c, "seed_fake", source="manual")
    pipeline.add_manual_evidence(cid, text="Parceria com @mention_fake e @outro_fake, Fortune Tiger link na bio cadastre-se",
                                 link="https://xyzbet.bet.br/?aff=1")
    for n in ("promo_fake1",):
        pipeline.investigate(n)
    r = pipeline.expand_candidate(cid, search=False)
    assert set(r["menções_novas"]) == {"mention_fake", "outro_fake"}
    assert any(x["username"] == "promo_fake1" for x in r["relacionados_existentes"])
    r2 = pipeline.expand_candidate(cid, search=False)
    assert r2["menções_novas"] == []     # sem duplicar
    with db.connect() as c:
        m = c.execute("SELECT * FROM candidates WHERE username='mention_fake'").fetchone()
        assert m["evidence_count"] == 0    # menção sozinha NÃO é evidência -> pendente


def test_hunts_defaults_and_run(env):
    with db.connect() as c:
        hs = c.execute("SELECT * FROM hunts ORDER BY position").fetchall()
        assert len(hs) == 14 and all(h["enabled"] for h in hs) and hs[-1]["kind"] == "matrix"
        c.execute("UPDATE hunts SET queries=?, sources=? WHERE id=1", (json.dumps(["Fortune Tiger"]), json.dumps(["ddg"])))
    job = pipeline.start_job("t", lambda j: pipeline.run_hunt(1, j), sync=True)
    assert job.status == "done" and job.result["new"] == 3 and job.result["enriched"] == 1
    with db.connect() as c:
        assert c.execute("SELECT last_run FROM hunts WHERE id=1").fetchone()[0]


def test_recursive_indicator_search(env):
    r = pipeline.indicator_search("domínio", "xyzbet.bet.br")
    assert r["consulta"] == '"xyzbet.bet.br"' and r["found"] >= 3


def test_export_and_copy(env):
    pipeline.investigate("promo_fake1"); pipeline.investigate("promo_fake2")
    with db.connect() as c:
        for i in (1, 2):
            pipeline.set_status(c, i, "CONFIRMADO", note="nota, com vírgula")
        data, mime, name = exporter.export(c, {}, fmt="csv", kind="simple", scope="confirmed")
        txt = data.decode("utf-8-sig")
        head = txt.splitlines()[0]
        assert head == "username,url_perfil,url_video,evidencia,dominio,url_externa,score,status,data_coleta,observacao_analista"
        assert "xyzbet.bet.br" in txt and "nota, com vírgula" in txt and name.endswith(".csv")
        full = exporter.export(c, {}, fmt="csv", kind="full", scope="confirmed")[0].decode("utf-8-sig")
        for col in ("MOTIVO_SCORE", "AFFILIATE_ID", "URL_INTERMEDIARIA", "OBSERVACAO_ANALISTA", "FUSO_HORARIO", "FONTE_DESCOBERTA"):
            assert col in full.splitlines()[0]
        assert "aff=981" in full
        j = json.loads(exporter.export(c, {}, fmt="json", scope="confirmed")[0])
        assert j["quantidade"] == 2 and j["candidatos"][0]["evidences"]
        x = exporter.export(c, {}, fmt="xlsx", kind="full", scope="confirmed")[0]
        assert x[:2] == b"PK"
        assert exporter.copy_list(c, {}, "profiles").splitlines() == ["https://www.tiktok.com/@promo_fake1", "https://www.tiktok.com/@promo_fake2"]
        assert exporter.copy_list(c, {}, "usernames").splitlines() == ["@promo_fake1", "@promo_fake2"]
        assert all("/video/" in u for u in exporter.copy_list(c, {}, "videos").splitlines())


def test_unconfirmed_never_exported_as_confirmed(env):
    pipeline.investigate("promo_fake1")
    pipeline.import_items("@pend_fake")
    with db.connect() as c:
        assert exporter.build_rows(c, {}, "confirmed") == []


def test_priority_order_and_filters(env):
    pipeline.investigate("promo_fake1"); pipeline.investigate("noticias_fake")
    with db.connect() as c:
        pipeline.run_query  # noqa
        lst = queries.list_candidates(c, {"view": "all"}, sort="priority")["items"]
        assert lst[0]["username"] == "promo_fake1"
        assert queries.list_candidates(c, {"min_score": 70, "view": "all"})["total"] == 1
        assert queries.list_candidates(c, {"domain": "xyzbet", "view": "all"})["total"] == 1
        assert queries.list_candidates(c, {"with_aff": "1", "view": "all"})["total"] == 1
        assert queries.list_candidates(c, {"game": "Fortune Tiger", "view": "all"})["total"] >= 1
        assert queries.list_candidates(c, {"source": "perfil-semente", "view": "all"})["total"] == 2
        assert queries.list_candidates(c, {"status": "BAIXA RELEVÂNCIA", "view": "all"})["total"] == 1
        assert queries.list_candidates(c, {"view": "results"})["total"] == 1   # BAIXA oculta da revisão principal


def test_stats_mission_metrics(env):
    pipeline.investigate("promo_fake1"); pipeline.investigate("noticias_fake")
    with db.connect() as c:
        pipeline.set_status(c, 1, "CONFIRMADO")
        st = queries.stats(c); ms = queries.mission(c); mt = queries.metrics(c)
    assert st["total"] == 2 and st["confirmados"] == 1 and st["baixa"] == 1 and st["descartados"] == 0 and st["dominios"] == 1
    assert st["unicos"] == 2 and st["brutos"] == 0
    assert ms["meta"] == 200 and ms["confirmados"] == 1 and ms["restantes"] == 199
    assert mt["taxa_confirmacao"] is None or 0 <= mt["taxa_confirmacao"] <= 1 and "jurídica" in mt["aviso"]


def test_web_api_smoke(env, tmp_path):
    from bethunter.web import create_app
    app = create_app(str(tmp_path / "t.db")); c = app.test_client()
    assert c.get("/").status_code == 200
    r = c.post("/api/investigate", json={"target": "@promo_fake1"}).json
    assert r["ok"] and r["id"]
    assert c.post("/api/investigate", json={"target": "@promo_fake1"}).json["cached"]
    assert c.post(f"/api/candidates/{r['id']}/status", json={"status": "CONFIRMADO"}).json["status"] == "CONFIRMADO"
    imp = c.post("/api/import", json={"text": "@pend1\n@pend2"}).json
    assert imp["perfis_novos"] == 2
    pend = c.get("/api/candidates?view=pending").json
    assert pend["total"] == 2
    assert c.post(f"/api/candidates/{pend['items'][0]['id']}/status", json={"status": "CONFIRMADO"}).status_code == 409
    assert c.get("/api/export?format=csv&scope=confirmed").data.startswith(b"\xef\xbb\xbfusername")
    assert c.get("/api/copy?what=profiles").data.decode().strip() == "https://www.tiktok.com/@promo_fake1"
    j = c.post("/api/search", json={"queries": ["Fortune Tiger"], "sources": ["ddg"]}).json
    import time
    for _ in range(50):
        jj = c.get(f"/api/jobs/{j['job']}").json
        if jj["status"] != "running":
            break
        time.sleep(0.1)
    assert jj["status"] == "done" and jj["result"]["new"] == 2
    assert c.get("/api/clusters?min=1").status_code == 200
    assert c.put("/api/settings?reanalyze=1", json={"thresholds": {"alta": 90}}).json["ok"]
    b = c.post("/api/bulk/status", json={"ids": [i["id"] for i in pend["items"]], "status": "DESCARTADO"}).json
    assert b["alterados"] == 2
