"""Classificação justificável (CONFIRMADO / PROVÁVEL / REVISÃO HUMANA / DESCARTADO) + feedback humano.

O score técnico é apenas apoio: a saída sempre lista as evidências. Indicadores isolados (username, hashtag, menção)
nunca bastam. A decisão humana registrada em datasets/feedback.csv sempre prevalece sobre a automática."""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import extract_indicators as ind  # noqa: E402

CONFIRMADO, PROVAVEL, REVISAO, DESCARTADO = "CONFIRMADO", "PROVÁVEL", "REVISÃO HUMANA", "DESCARTADO"
W = {"link_plataforma": 30, "codigo_afiliado": 25, "bio_ganho": 20, "video_promove": 20, "instrucao_cadastro": 15,
     "cpa_chines": 10, "plataforma_chinesa": 10, "username": 5, "similar_positivo": 10,
     "neg_jornalistico": -30, "neg_denuncia": -30, "neg_critico": -30, "mera_mencao": -20, "sem_link_cta": -20,
     "similar_negativo": -10, "dominio_so_negativo": -10}
FEEDBACK_FIELDS = ["perfil", "predicao", "decisao_humana", "motivo"]
DATASET_FIELDS = ["username", "bio", "links", "content", "final_domain", "affiliate_codes", "hashtags", "motivo", "added_at"]
CLASS_FIELDS = ["username", "classificacao_automatica", "decisao_humana", "classificacao_final", "aprovado_humano",
                "score_tecnico", "plataformas", "dominios", "codigos_afiliado", "evidencias"]
USER_HINTS = ["plataforma", "chines", "gringa", "cpa", "casino", "cassino", "slots", "tigrinho", "bet"]
_DEC = {"confirmar": CONFIRMADO, "confirmado": CONFIRMADO, "descartar": DESCARTADO, "descartado": DESCARTADO,
        "manter em revisao": REVISAO, "manter": REVISAO, "revisao": REVISAO, "revisao humana": REVISAO}
_CLS = {"confirmado": CONFIRMADO, "provavel": PROVAVEL, "revisao humana": REVISAO, "revisao": REVISAO, "descartado": DESCARTADO}


def key(u):
    return str(u or "").strip().lstrip("@").lower()


def norm_decision(d):
    return _DEC.get(common.norm(d))


def norm_class(c):
    return _CLS.get(common.norm(c), c)


# ---------------------------------------------------------------- golden set / feedback
def tokens(text):
    return {w for w in re.findall(r"[a-z0-9]{4,}", common.norm(text)) if w not in ind.STOP}


def load_golden():
    """Aprende de positivos/negativos: domínios, códigos e texto. O USERNAME NÃO É USADO."""
    g = {"pos_domains": set(), "neg_domains": set(), "pos_codes": set(), "pos_texts": [], "neg_texts": []}
    for fname, dom, txt in (("positivos_confirmados.csv", "pos_domains", "pos_texts"), ("negativos_confirmados.csv", "neg_domains", "neg_texts")):
        for r in common.read_csv(common.P("datasets", fname)):
            for d in re.split(r"[;,\s]+", r.get("final_domain") or ""):
                if d and common.link_kind("https://" + d) == "outro":
                    g[dom].add(common.registrable(d.lower()))
            if dom == "pos_domains":
                g["pos_codes"].update(c.split("=")[-1] for c in re.split(r"[;\s]+", r.get("affiliate_codes") or "") if c)
            t = " ".join([r.get("bio") or "", r.get("content") or ""])
            if len(tokens(t)) >= 6:
                g[txt].append(tokens(t))
    return g


def load_feedback():
    """Última decisão humana por perfil (a ordem do arquivo vale)."""
    out = {}
    for r in common.read_csv(common.P("datasets", "feedback.csv")):
        d = norm_decision(r.get("decisao_humana"))
        if d and r.get("perfil"):
            out[key(r["perfil"])] = {"decisao": d, "motivo": r.get("motivo", ""), "predicao": r.get("predicao", "")}
    return out


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


# ---------------------------------------------------------------- pontuação
def score(meta, feats, golden=None, sim_threshold=0.5):
    """-> dict com classificacao automática, score_tecnico, evidencias e pilares. Nunca usa só o username."""
    golden = golden or load_golden()
    pts, ev, neg = [], [], []
    hb, hc, ha = feats["hits_bio"], feats["hits_content"], feats["hits"]
    bio_promo = sorted(set(hb["ganho"]) | set(hb["plataforma"]) & set(ha["ganho"] + ["plataforma pagando", "nova plataforma"]))
    vid_promo = sorted(set(hc["ganho"]) | set(hc["cadastro"]) | set(hc["plataforma"]))
    cta = sorted(set(ha["cadastro"]))

    def add(k, text):
        pts.append((k, W[k], text))
        (neg if W[k] < 0 else ev).append(text)

    link = bool(feats["gambling_links"]) or bool(feats["landing_gambling"])
    if feats["gambling_links"]:
        g = feats["gambling_links"][0]
        verbo = "leva a" if not g.get("erro") else f"aponta para (cadeia não resolvida: {g['erro']})"
        add("link_plataforma", f"link da bio ({g['short_url']}) {verbo} {g['final_url']} — {g['motivo']}")
    elif feats["landing_gambling"]:
        g = feats["landing_gambling"][0]
        add("link_plataforma", f"landing page {g['domain']} contém termos de apostas: {', '.join(g['termos'][:5])}")
    aff = [a for a in feats["affiliate"] if a["strength"] == "forte" and (a["parameter"] in common.DIRECT_AFF or link or a["parameter"] == "codigo_texto")]
    if any(a["parameter"] in golden["pos_codes"] or a["value"] in golden["pos_codes"] for a in aff):
        ev.append("código de afiliado já associado a perfil confirmado (golden set)")
    if aff:
        a = aff[0]
        where = f"URL contém ?{a['parameter']}={a['value']}" if a["url"] else f"texto traz código '{a['value']}'"
        add("codigo_afiliado", where)
    if bio_promo:
        add("bio_ganho", "bio promove ganhos/plataforma: " + ", ".join(f"'{t}'" for t in bio_promo[:4]))
    if vid_promo:
        add("video_promove", "conteúdo dos vídeos promove: " + ", ".join(f"'{t}'" for t in vid_promo[:4]))
    if cta:
        add("instrucao_cadastro", "chamada/instrução de cadastro: " + ", ".join(f"'{t}'" for t in cta[:4]))
    if feats["cpa_chines"]:
        add("cpa_chines", "menciona 'CPA chinês'")
    if feats["plataforma_chinesa"]:
        add("plataforma_chinesa", "menciona 'plataforma chinesa'")
    uname = common.norm(meta.get("username"))
    if any(h in uname for h in USER_HINTS):
        add("username", "username relacionado (apenas indicador, não decide)")

    # sinais negativos
    for k, label in (("neg_jornalistico", "conteúdo jornalístico"), ("neg_denuncia", "denúncia"), ("neg_critico", "conteúdo crítico")):
        if ha[k]:
            add(k, f"{label}: " + ", ".join(f"'{t}'" for t in ha[k][:3]))
    topic = ha["tema"] or ha["plataforma"] or ha["afiliacao"]
    promo = bool(link or aff or bio_promo or vid_promo or cta)
    if topic and not promo:
        add("mera_mencao", "menciona o tema (" + ", ".join(f"'{t}'" for t in topic[:3]) + ") sem promoção, link ou CTA")
    if not feats["final_domains"] and not feats["contacts"] and not feats["hubs"] and not cta:
        add("sem_link_cta", "sem link externo e sem chamada para ação")

    # golden set por CONTEÚDO (nunca username)
    mine = tokens(" ".join([meta.get("bio") or ""] + [str(c) for c in meta.get("content") or []]))
    if len(mine) >= 6:
        sp = max((jaccard(mine, t) for t in golden["pos_texts"]), default=0)
        sn = max((jaccard(mine, t) for t in golden["neg_texts"]), default=0)
        if sp >= sim_threshold and sp > sn:
            add("similar_positivo", f"conteúdo semelhante (J={sp:.2f}) a perfil confirmado")
        elif sn >= sim_threshold:
            add("similar_negativo", f"conteúdo semelhante (J={sn:.2f}) a perfil descartado")
    doms = {common.registrable(d) for d in feats["final_domains"]}
    if doms & golden["neg_domains"] and not doms & golden["pos_domains"]:
        add("dominio_so_negativo", "domínio final aparece só em exemplos descartados")

    total = sum(p for _, p, _ in pts)
    pillar_link, pillar_code = link, bool(aff)
    pillar_promo = bool((bio_promo or vid_promo) and cta)
    strong = sum([pillar_link, pillar_code, pillar_promo])
    has_neg = bool(ha["neg_jornalistico"] or ha["neg_denuncia"] or ha["neg_critico"])
    if pillar_link and (pillar_code or pillar_promo) and total >= 60 and not has_neg:
        cls = CONFIRMADO
    elif total >= 40 and strong >= 1:
        cls = PROVAVEL
    elif total >= 15 or strong >= 1 or (promo and not has_neg):
        cls = REVISAO
    else:
        cls = DESCARTADO
    if has_neg and cls in (CONFIRMADO, PROVAVEL):
        cls = REVISAO
        neg.append("sinal de conteúdo jornalístico/denúncia/crítico junto a indicadores positivos → exige revisão humana")
    if cls == DESCARTADO and not ev:
        ev.append("nenhum indicador relevante encontrado")
    return {"classificacao": cls, "score_tecnico": total, "evidencias": ev, "evidencias_negativas": neg,
            "pilares": {"link": pillar_link, "codigo": pillar_code, "promocao_cadastro": pillar_promo},
            "detalhe_score": [{"sinal": k, "pontos": p, "evidencia": t} for k, p, t in pts]}


def classify_profile(meta, redirects, golden=None, feedback=None, vocab=None):
    golden = golden or load_golden()
    learned = golden["pos_domains"]
    if meta.get("status") == "INDISPONIVEL":
        feats = ind.build_features(meta, [], vocab, learned_domains=learned)
        res = {"classificacao": DESCARTADO, "score_tecnico": 0, "evidencias": ["conta indisponível/removida — sem dados para analisar"],
               "evidencias_negativas": [], "pilares": {}, "detalhe_score": []}
    else:
        feats = ind.build_features(meta, redirects, vocab, learned_domains=learned)
        res = score(meta, feats, golden)
    fb = (feedback if feedback is not None else load_feedback()).get(key(meta["username"]))
    final, approved = res["classificacao"], False
    if fb:
        final, approved = fb["decisao"], fb["decisao"] == CONFIRMADO
    return {"username": meta["username"], "classification": final, "classification_auto": res["classificacao"],
            "human_decision": fb["decisao"] if fb else None, "human_reason": fb["motivo"] if fb else None,
            "human_approved": approved, "score_tecnico": res["score_tecnico"],
            "score_nota": "score técnico é apoio à triagem, não prova", "evidence": res["evidencias"],
            "negative_evidence": res["evidencias_negativas"], "pillars": res["pilares"], "score_breakdown": res["detalhe_score"],
            "platforms": feats["platforms"], "domains": feats["final_domains"], "shorteners": feats["shorteners"],
            "affiliate_ids": [{"parameter": a["parameter"], "value": a["value"], "domain": a["domain"], "strength": a["strength"]}
                              for a in feats["affiliate"]], "hashtags": feats["hashtags"], "contacts": feats["contacts"],
            "classified_at": common.now_iso()}


# ---------------------------------------------------------------- feedback humano
def record_feedback(perfil, decisao, motivo, predicao=None):
    """Registra em datasets/feedback.csv e promove o perfil aos datasets positivos/negativos (aprendizado por conteúdo)."""
    d = norm_decision(decisao)
    if not d:
        raise ValueError("decisão inválida: use CONFIRMAR, DESCARTAR ou MANTER EM REVISÃO")
    u = key(perfil)
    ed = common.P("output", "evidence", u)
    an = common.read_json(ed / "analysis.json", {}) or {}
    predicao = predicao or an.get("classification_auto") or "NÃO IDENTIFICADO"
    common.append_csv(common.P("datasets", "feedback.csv"),
                      {"perfil": "@" + u, "predicao": predicao, "decisao_humana": d, "motivo": motivo}, FEEDBACK_FIELDS)
    if d in (CONFIRMADO, DESCARTADO):
        meta = common.read_json(ed / "metadata.json", {}) or {}
        target = "positivos_confirmados.csv" if d == CONFIRMADO else "negativos_confirmados.csv"
        common.append_csv(common.P("datasets", target), {
            "username": u, "bio": meta.get("bio"), "links": ";".join(x.get("url", "") for x in (common.read_json(ed / "external_links.json", []) or [])),
            "content": " | ".join(meta.get("content") or []), "final_domain": ";".join(an.get("domains") or []),
            "affiliate_codes": ";".join(f"{a['parameter']}={a['value']}" for a in an.get("affiliate_ids") or []),
            "hashtags": ";".join(an.get("hashtags") or []), "motivo": motivo, "added_at": common.now_iso()}, DATASET_FIELDS)
    return d


# ---------------------------------------------------------------- execução em lote
def load_redirects(user):
    return common.read_json(common.P("output", "evidence", user, "redirects.json"), []) or []


def meta_from_index(row):
    import json
    def js(v):
        try:
            return json.loads(v) if v else []
        except ValueError:
            return []
    return {"username": row["username"], "display_name": row.get("display_name"), "bio": row.get("bio"),
            "followers": common.parse_count(row.get("followers")), "following": common.parse_count(row.get("following")),
            "likes": common.parse_count(row.get("likes")), "bio_links": js(row.get("bio_links")), "content": js(row.get("content")),
            "status": row.get("status"), "private": row.get("private"), "url_perfil": row.get("url_perfil"),
            "collected_at": row.get("collected_at"), "screenshot": row.get("screenshot")}


def classify_all(rows=None):
    from resume_collection import load_index, DONE
    rows = rows if rows is not None else [r for r in load_index().values() if r["status"] in DONE]
    golden, fb, out = load_golden(), load_feedback(), []
    for r in rows:
        meta = meta_from_index(r)
        res = classify_profile(meta, load_redirects(meta["username"]), golden, fb)
        common.write_json(common.evidence_dir(meta["username"]) / "analysis.json",
                          {"classification": res["classification"], **{k: v for k, v in res.items() if k != "classification"}})
        out.append(res)
    common.write_csv(common.P("output", "classificacao.csv"), [{
        "username": r["username"], "classificacao_automatica": r["classification_auto"], "decisao_humana": r["human_decision"],
        "classificacao_final": r["classification"], "aprovado_humano": r["human_approved"], "score_tecnico": r["score_tecnico"],
        "plataformas": "; ".join(r["platforms"]), "dominios": "; ".join(r["domains"]),
        "codigos_afiliado": "; ".join(f"{a['parameter']}={a['value']}" for a in r["affiliate_ids"]),
        "evidencias": " | ".join(r["evidence"] + r["negative_evidence"])} for r in out], CLASS_FIELDS)
    return out


def pending_review(results):
    """PROVÁVEL/CONFIRMADO automáticos ainda sem decisão humana."""
    return [r for r in results if r["classification_auto"] in (CONFIRMADO, PROVAVEL) and not r["human_decision"]]


def review_interactive(results, inp=input, out=print):
    for r in pending_review(results):
        out(f"\n@{r['username']}  auto={r['classification_auto']}  score_técnico={r['score_tecnico']}")
        for e in r["evidence"]:
            out("  + " + e)
        for e in r["negative_evidence"]:
            out("  - " + e)
        out(f"  evidências: output/evidence/{r['username']}/")
        ans = common.norm(inp("[c]onfirmar / [d]escartar / [m]anter em revisão / [s]air: "))
        if ans.startswith("s"):
            break
        d = {"c": "CONFIRMAR", "d": "DESCARTAR", "m": "MANTER EM REVISÃO"}.get(ans[:1])
        if d:
            record_feedback(r["username"], d, inp("motivo: ").strip() or "NÃO IDENTIFICADO", r["classification_auto"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="classifica todos os perfis coletados")
    sub.add_parser("review", help="revisão humana interativa (PROVÁVEL/CONFIRMADO)")
    d = sub.add_parser("decide", help="registra uma decisão: decide @user CONFIRMAR|DESCARTAR|MANTER 'motivo'")
    d.add_argument("perfil"), d.add_argument("decisao"), d.add_argument("motivo")
    a = ap.parse_args(argv)
    if a.cmd == "decide":
        print(record_feedback(a.perfil, a.decisao, a.motivo))
        return
    res = classify_all()
    if a.cmd == "review":
        review_interactive(res)
    else:
        from collections import Counter
        print(dict(Counter(r["classification"] for r in res)))


if __name__ == "__main__":
    main()
