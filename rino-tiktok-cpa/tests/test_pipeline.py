import json

import build_report
import classify_profiles as cp
import cluster_profiles
import collect_profiles as col
import common
import export_takedown
import extract_indicators as ind
import resume_collection as rc
import run_mission
from conftest import make_fetcher

HTML = """<html><body><h1 data-e2e="user-title">promoplat</h1><h2 data-e2e="user-subtitle">Promo Plat</h2>
<strong data-e2e="following-count">12</strong><strong data-e2e="followers-count">238.2K</strong><strong data-e2e="likes-count">1.5M</strong>
<h2 data-e2e="user-bio">Nova plataforma pagando! Link na bio</h2>
<a data-e2e="user-link" href="https://www.tiktok.com/link/v2?scene=bio_url&target=https%3A%2F%2Fencr.pw%2FAbC">l</a>
<div data-e2e="user-post-item-desc" title="CPA chinês voltou, cadastre-se #cpa"></div></body></html>"""


def test_parse_profile_ok_private_missing_blocked():
    r = col.parse_profile_html(HTML, "promoplat")
    assert r["followers"] == 238200 and r["likes"] == 1_500_000 and r["bio_links"] == ["https://encr.pw/AbC"]
    assert r["status"] == "OK" and "CPA chinês" in r["content"][0]
    no_f = col.parse_profile_html(HTML.replace("238.2K", "-"), "promoplat")
    assert no_f["followers"] is None                              # "-" nunca vira 0
    assert col.parse_profile_html("<p>Esta conta é privada</p><h1 data-e2e='user-title'>x</h1>", "x")["status"] == "PRIVADA"
    assert col.parse_profile_html("<p>Não foi possível encontrar esta conta</p>", "x")["status"] == "INDISPONIVEL"
    b = col.parse_profile_html("<p>Verifique para continuar</p>", "x")
    assert b["status"] == "BLOQUEADO" and "não contornado" in b["error"]
    state = {"x": {"user": {"uniqueId": "jsonu", "nickname": "J", "signature": "bio json", "privateAccount": False}, "stats": {"followerCount": 1234}}}
    j = col.parse_profile_html(f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{json.dumps(state)}</script>', "jsonu")
    assert j["followers"] == 1234 and j["bio"] == "bio json"


class Flaky:
    def __init__(self, fail_on=None):
        self.fail_on, self.calls = fail_on, []

    def collect(self, user, seq):
        self.calls.append(user)
        if user == self.fail_on:
            raise RuntimeError("queda")
        return {"status": "OK", "followers": 1000 + seq, "bio": f"bio {user}", "bio_links": [], "content": []}


def cands(n):
    return [{"username": f"user{i:02d}", "url_original": f"@user{i:02d}", "url_perfil": f"https://www.tiktok.com/@user{i:02d}"} for i in range(n)]


def test_resume_saves_each_profile_and_skips_done(root):
    c = Flaky(fail_on="user02")
    res = rc.run_collection(cands(5), c, delay=(0, 0), sleeper=lambda s: None, log=lambda *_: None)
    idx = rc.load_index()
    assert idx["user02"]["status"] == "ERRO_TEMPORARIO" and idx["user04"]["status"] == "OK" and res["pendentes"] == 1
    c2 = Flaky()
    res2 = rc.run_collection(cands(5), c2, delay=(0, 0), sleeper=lambda s: None, log=lambda *_: None)
    assert c2.calls == ["user02"] and res2["pendentes"] == 0          # só o pendente foi repetido
    assert rc.load_index()["user02"]["attempts"] == "2"
    c3 = Flaky()
    rc.run_collection(cands(5), c3, delay=(0, 0), sleeper=lambda s: None, log=lambda *_: None)
    assert c3.calls == []


def test_resume_after_crash_mid_run(root):
    class Crash(Flaky):
        def collect(self, user, seq):
            if user == "user03":
                raise KeyboardInterrupt
            return super().collect(user, seq)
    try:
        rc.run_collection(cands(6), Crash(), delay=(0, 0), sleeper=lambda s: None, log=lambda *_: None)
    except KeyboardInterrupt:
        pass
    assert set(rc.load_index()) == {"user00", "user01", "user02"}     # progresso em disco antes da queda
    c = Flaky()
    rc.run_collection(cands(6), c, delay=(0, 0), sleeper=lambda s: None, log=lambda *_: None)
    assert c.calls == ["user03", "user04", "user05"]


def test_blocks_stop_round_without_losing_progress(root):
    class Blk:
        def collect(self, u, s):
            return {"status": "BLOQUEADO", "error": "captcha"} if s > 1 else {"status": "OK", "bio": "x"}
    sleeps = []
    r = rc.run_collection(cands(6), Blk(), delay=(0, 0), sleeper=sleeps.append, log=lambda *_: None)
    assert r["parou_por_bloqueio"] and r["pendentes"] == 5 and len(sleeps) >= 2 and rc.load_index()["user00"]["status"] == "OK"


def test_max_attempts_gives_up_explicitly(root):
    class Always:
        def collect(self, u, s):
            return {"status": "ERRO_TEMPORARIO", "error": "tempo esgotado"}
    for _ in range(4):
        rc.run_collection(cands(1), Always(), delay=(0, 0), sleeper=lambda s: None, log=lambda *_: None)
    assert rc.load_index()["user00"]["attempts"] == "3"


# ---------------------------------------------------------------- indicadores / classificação
CHAIN = {"https://encr.pw/AbC": (301, "https://di-di.me/register?inviter=S0FE98", ""),
         "https://di-di.me/register?inviter=S0FE98": (200, None, "<title>DIDI</title>cassino slots bônus")}


def meta(user, bio, content=(), links=(), followers=None):
    return {"username": user, "display_name": user, "bio": bio, "content": list(content), "bio_links": list(links),
            "followers": followers, "status": "OK", "url_perfil": f"https://www.tiktok.com/@{user}"}


def classify(m, table=None, **kw):
    reds = ind.enrich_profile(m, make_fetcher(table or {}))
    return cp.classify_profile(m, reds, **kw)


def test_promoter_confirmed_with_evidence(root):
    r = classify(meta("plataformachinesa01", "Nova plataforma pagando 💰 cadastre-se pelo link na bio", ["CPA chinês voltou, entra pelo meu link"], ["https://encr.pw/AbC"]), CHAIN)
    assert r["classification_auto"] == cp.CONFIRMADO and r["platforms"] == ["DIDI"]
    assert {"parameter": "inviter", "value": "S0FE98", "domain": "di-di.me", "strength": "forte"} in r["affiliate_ids"]
    assert any("inviter=S0FE98" in e for e in r["evidence"]) and r["shorteners"] == ["encr.pw"]
    assert not r["human_approved"]                                  # automático ≠ aprovado para takedown


def test_username_alone_never_classifies(root):
    r = classify(meta("plataforma_chinesa_cpa_casino", "bom dia"))
    assert r["classification"] == cp.DESCARTADO or r["score_tecnico"] <= 5


def test_journalism_and_mere_mention_not_infractor(root):
    j = classify(meta("jornal01", "Reportagem: plataformas chinesas de cassino e o golpe do tigrinho. Não recomendo."))
    assert j["classification"] in (cp.DESCARTADO, cp.REVISAO) and j["classification"] != cp.CONFIRMADO
    assert any("jornalístico" in e or "crítico" in e or "denúncia" in e for e in j["negative_evidence"])
    m = classify(meta("fulano", "falei de cassino no vídeo de hoje"))
    assert m["classification"] in (cp.DESCARTADO, cp.REVISAO) and m["classification"] != cp.PROVAVEL


def test_news_with_promo_signals_capped_at_review(root):
    r = classify(meta("misto", "Reportagem sobre golpe. Nova plataforma pagando, cadastre-se", [], ["https://encr.pw/AbC"]), CHAIN)
    assert r["classification_auto"] == cp.REVISAO


def test_nao_e_golpe_is_not_denuncia(root):
    r = classify(meta("promo2", "Plataforma pagando, não é golpe! cadastre-se no link na bio", [], ["https://encr.pw/AbC"]), CHAIN)
    assert not r["negative_evidence"] or all("denúncia" not in e for e in r["negative_evidence"])
    assert r["classification_auto"] in (cp.CONFIRMADO, cp.PROVAVEL)


def test_unavailable_account(root):
    m = meta("sumiu", "")
    m["status"] = "INDISPONIVEL"
    assert cp.classify_profile(m, [])["classification"] == cp.DESCARTADO


def test_feedback_overrides_and_learns(root):
    m = meta("abcd", "Nova plataforma pagando cadastre-se", [], ["https://encr.pw/AbC"])
    r1 = classify(m, CHAIN)
    common.write_json(common.evidence_dir("abcd") / "analysis.json", {"classification_auto": r1["classification_auto"], "domains": r1["domains"],
                                                                      "affiliate_ids": r1["affiliate_ids"], "hashtags": []})
    cp.record_feedback("@abcd", "DESCARTAR", "conteúdo apenas jornalístico")
    rows = common.read_csv(root / "datasets" / "feedback.csv")
    assert rows[-1]["perfil"] == "@abcd" and rows[-1]["decisao_humana"] == "DESCARTADO" and rows[-1]["predicao"] == r1["classification_auto"]
    assert classify(m, CHAIN)["classification"] == cp.DESCARTADO
    assert len(common.read_csv(root / "datasets" / "negativos_confirmados.csv")) == 1
    cp.record_feedback("@abcd", "CONFIRMAR", "link leva a cassino com referral")            # última decisão vale
    assert classify(m, CHAIN)["human_approved"] is True
    try:
        cp.record_feedback("@x", "TALVEZ", "")
        assert False
    except ValueError:
        pass


def test_golden_set_learns_content_not_username(root):
    text = "gente corre pra entrar nessa plataforma nova que paga todo dia saque imediato no pix sem burocracia nenhuma"
    common.append_csv(root / "datasets" / "positivos_confirmados.csv", {"username": "outro", "bio": text, "final_domain": "novadominio.xyz",
                                                                        "affiliate_codes": "inviter=ZZ99"}, cp.DATASET_FIELDS)
    g = cp.load_golden()
    assert "novadominio.xyz" in g["pos_domains"]
    r = cp.score(meta("qualquernome", text), ind.build_features(meta("qualquernome", text), []), g)
    assert any("semelhante" in e for e in r["evidencias"])
    r2 = cp.score(meta("outro", "nada a ver com isso hoje é domingo"), ind.build_features(meta("outro", "nada a ver"), []), g)
    assert not any("semelhante" in e for e in r2["evidencias"])          # mesmo username, conteúdo diferente: sem bônus


def test_learned_domain_counts_as_platform_link(root):
    common.append_csv(root / "datasets" / "positivos_confirmados.csv", {"username": "p", "final_domain": "raro-site.xyz"}, cp.DATASET_FIELDS)
    t = {"https://bit.ly/q": (302, "https://raro-site.xyz/r", ""), "https://raro-site.xyz/r": (200, None, "olá")}
    r = classify(meta("zz", "veja", [], ["https://bit.ly/q"]), t)
    assert any("golden set" in e for e in r["evidence"])


def test_expand_vocabulary(root, tmp_path):
    v = tmp_path / "v.md"
    v.write_text((ind.common.knowledge("vocabulary.md")).read_text(encoding="utf-8"), encoding="utf-8")
    new = ind.expand_vocabulary(["pix liberado hoje", "pix liberado agora"], ["bom dia"], v)
    assert "pix liberado" in new and "pix liberado" in v.read_text(encoding="utf-8")


# ---------------------------------------------------------------- cluster / export / relatório / missão completa
FIX = {
    "promoa": {"display_name": "A", "followers": "238.2K", "bio": "Nova plataforma pagando, cadastre-se no link na bio", "bio_links": ["https://encr.pw/AbC"], "content": ["CPA chinês voltou"]},
    "promob": {"display_name": "B", "followers": "16.9K", "bio": "Plataforma chinesa pagando saque imediato, entra pelo meu link", "bio_links": ["https://bit.ly/q2"]},
    "semseg": {"display_name": "C", "followers": "-", "bio": "CPA voltou! ganhe dinheiro, cadastre-se no link na bio", "bio_links": ["https://cutt.ly/z"]},
    "noticia": {"display_name": "N", "followers": "5.000", "bio": "Reportagem sobre golpe das apostas. Não recomendo."},
    "sumido": {"status": "INDISPONIVEL"},
}
TABLE = {**CHAIN, "https://bit.ly/q2": (302, "https://di-di.me/register?inviter=S0FE98&utm_source=tt", ""),
         "https://di-di.me/register?inviter=S0FE98&utm_source=tt": (200, None, "DIDI cassino slots"),
         "https://cutt.ly/z": (302, "https://outra-bet.com/?ref=Q1", ""), "https://outra-bet.com/?ref=Q1": (200, None, "bet")}
LINES = ["https://www.tiktok.com/@promoa/video/1", "https://www.tiktok.com/@promoa/photo/9", "https://www.tiktok.com/@promob", "@semseg",
         "https://www.tiktok.com/@noticia/video/3", "@sumido", "https://google.com"]


def test_full_mission_end_to_end(root):
    out = run_mission.run(LINES, col.FixtureCollector(FIX), make_fetcher(TABLE), delay=(0, 0), sleeper=lambda s: None, log=lambda *_: None)
    by = {r["username"]: r for r in out["resultados"]}
    assert len(by) == 5 and by["sumido"]["classification"] == cp.DESCARTADO and by["noticia"]["classification"] != cp.CONFIRMADO
    assert by["promoa"]["classification_auto"] == cp.CONFIRMADO and by["semseg"]["platforms"] == ["outra-bet.com"]
    m = out["metricas"]
    assert m["perfis_encontrados"] == 5 and m["seguidores_conhecidos"] == 238200 + 16900 + 5000
    assert m["perfis_sem_seguidores"] == 2          # semseg ("-") e sumido (indisponível): nunca somados como zero
    assert m["aprovados_takedown"] == [] and common.P("output", "takedown.csv").exists()
    cl = common.read_json(common.P("output", "clusters.json"))["clusters"]
    c = next(c for c in cl if "promoa" in c["perfis"])
    assert {"promoa", "promob"} <= set(c["perfis"]) and c["plataformas"] == ["DIDI"] and "inviter=S0FE98" in c["referrals"]
    assert c["seguidores_conhecidos"] == 238200 + 16900 and any("encr.pw" in x for x in c["relacoes"])
    assert "semseg" not in c["perfis"]
    ed = root / "output" / "evidence" / "promoa"
    assert {p.name for p in ed.iterdir()} >= {"metadata.json", "external_links.json", "redirects.json", "analysis.json"}
    assert common.read_json(ed / "metadata.json")["followers"] == 238200
    assert "MISSÃO TIKTOK CPA" in out["relatorio"] and "Clusters: 1" in out["relatorio"]


def test_takedown_requires_human_approval(root):
    run_mission.run(LINES, col.FixtureCollector(FIX), make_fetcher(TABLE), delay=(0, 0), sleeper=lambda s: None, log=lambda *_: None)
    assert export_takedown.run() == []
    cp.record_feedback("@promoa", "CONFIRMAR", "link leva a cassino com referral")
    cp.record_feedback("@noticia", "CONFIRMAR", "teste: auto descartado")                # auto != indicadores → não exporta
    cp.record_feedback("@promob", "MANTER EM REVISÃO", "aguardando")
    cp.classify_all()
    rows = export_takedown.run()
    assert [r["username"] for r in rows] == ["promoa"]
    r = next(r for r in rows if r["username"] == "promoa")
    assert r["affiliate_code"] == "inviter=S0FE98" and r["external_domain"] == "di-di.me" and r["followers"] == 238200
    assert common.read_csv(common.P("output", "takedown.csv"))[0].keys() >= {"profile_url", "username", "followers", "reason", "evidence", "external_domain", "affiliate_code", "screenshot"}
    assert "@promoa" in build_report.run()[1].split("Perfis aprovados para takedown:")[1]


def test_mission_is_resumable_and_idempotent(root):
    f = make_fetcher(TABLE)
    run_mission.run(LINES, col.FixtureCollector(FIX), f, delay=(0, 0), sleeper=lambda s: None, log=lambda *_: None)
    n = len(f.calls)
    class Never:
        def collect(self, u, s):
            raise AssertionError("não deve recoletar")
    run_mission.run(LINES, Never(), f, delay=(0, 0), sleeper=lambda s: None, log=lambda *_: None)
    assert len(f.calls) == n                                     # redirects reaproveitados, sem novas requisições
