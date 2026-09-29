"""Pipeline: descoberta -> candidatos -> evidências -> análise -> score -> dedupe -> clusters."""
import csv
import hashlib
import io
import json
import logging
import re
import threading
import time
import uuid

from . import db, extract, scoring, sources, urltools
from .db import jd, jl
from .util import (NAO_IDENTIFICADO, base_domain, canonical_video_url, clip, extract_urls, host_of, norm, now_iso,
                   normalize_username, parse_tiktok_url, profile_url, USERNAME_RE, URL_RE, extract_hashtags,
                   extract_mentions)

STATUSES = ["NOVO", "REVISAR", "CONFIRMADO", "DESCARTADO", "BAIXA RELEVÂNCIA", "DUPLICADO", "PERFIL INDISPONÍVEL",
            "CONTEÚDO REMOVIDO", "JÁ ENCAMINHADO"]
UNRESOLVED = "não resolvido"
log = logging.getLogger("bethunter")
INACTIVE = ("DESCARTADO", "BAIXA RELEVÂNCIA", "DUPLICADO", "PERFIL INDISPONÍVEL", "CONTEÚDO REMOVIDO")
ORIGIN_LABELS = {"domínio": "domínio", "código": "link de afiliado", "hashtag": "hashtag", "plataforma": "menção"}

REASON_TO_EVTYPE = {"link_bet": "link externo", "cta": "CTA", "gameplay": "gameplay", "platform": "plataforma",
                    "payment": "saque/pagamento", "aff_link": "afiliado", "bonus_code": "bônus/código",
                    "group": "grupo", "hashtag": "hashtag", "expressions": "expressão", "recurrence": "recorrência",
                    "padrao_recorrente": "padrão recorrente", "shared_domain": "domínio compartilhado"}


# ============================================================ candidatos / evidências

def upsert_candidate(conn, username, *, source, query="", url="", hunt="", profile_id=None, count_dup=True):
    """Dedupe por username e por ID de perfil. -> (id, is_new)."""
    now = now_iso()
    row = conn.execute("SELECT id FROM candidates WHERE username=?", (username,)).fetchone()
    if not row and profile_id:
        row = conn.execute("SELECT id FROM candidates WHERE profile_id=?", (str(profile_id),)).fetchone()
        if row:  # mesmo perfil com outro @: guarda alias
            cur = jl(conn.execute("SELECT merged_from FROM candidates WHERE id=?", (row["id"],)).fetchone()[0])
            if username not in cur:
                cur.append(username)
                conn.execute("UPDATE candidates SET merged_from=? WHERE id=?", (jd(cur), row["id"]))
    if row:
        cid = row["id"]
        conn.execute("UPDATE candidates SET last_seen=?, dup_hits=dup_hits+? WHERE id=?",
                     (now, 1 if count_dup else 0, cid))
        new = False
    else:
        cur = conn.execute(
            "INSERT INTO candidates(username, profile_url, profile_id, first_seen, last_seen, first_source) "
            "VALUES(?,?,?,?,?,?)", (username, profile_url(username), str(profile_id or ""), now, now, source))
        cid, new = cur.lastrowid, True
    if source:
        conn.execute("INSERT OR IGNORE INTO discoveries(candidate_id, source, query, hunt, url, found_at) VALUES(?,?,?,?,?,?)",
                     (cid, source, query or "", hunt or "", url or "", now))
    return cid, new


def add_evidence(conn, cid, kind, *, source="", source_url="", query="", url_video="", caption="", text="",
                 hashtags=None, mentions=None, tags=None, meta=None, dedupe_key=None):
    body = norm((caption or "") + " " + (text or ""))[:400]
    key = dedupe_key or hashlib.sha1(f"{kind}|{url_video}|{body}".encode()).hexdigest()
    hashtags = hashtags if hashtags is not None else extract_hashtags((caption or "") + " " + (text or ""))
    mentions = mentions if mentions is not None else extract_mentions((caption or "") + " " + (text or ""))
    cur = conn.execute(
        "INSERT OR IGNORE INTO evidences(candidate_id,kind,source,source_url,query,url_video,caption,text,hashtags,"
        "mentions,tags,collected_at,dedupe_key,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, kind, source, source_url, query, url_video, caption, text, jd(hashtags), jd(mentions), jd(tags or []),
         now_iso(), key, jd(meta or {})))
    return cur.rowcount > 0


def _cached_records(urls):
    """Resoluções já feitas (qualquer candidato): evita repetir rede para a mesma URL."""
    if not urls:
        return {}
    out = {}
    with db.connect() as conn:
        q = ",".join("?" * len(urls))
        for l in conn.execute(f"SELECT * FROM links WHERE url_original IN ({q}) AND error NOT LIKE ?",
                              (*urls, UNRESOLVED + "%")):
            recs = out.setdefault(l["url_original"], [])
            if not any(r["url_final"] == l["url_final"] for r in recs):
                recs.append({"url_original": l["url_original"], "chain": jl(l["chain"]), "url_final": l["url_final"],
                             "domain_final": l["domain_final"], "params": jl(l["params"]), "params_raw": l["params_raw"],
                             "page_title": l["page_title"], "page_text": l["page_text"], "aggregator": l["aggregator"],
                             "error": l["error"]})
    return out


def promising_text(text, settings):
    """Score preliminar barato (só texto+URL): vale gastar rede/processamento neste resultado?"""
    x = extract.extract_all(text, settings)
    if any(p["type"] in ("affiliate", "referral") for u in x["urls"] for p in urltools.parse_params(u)):
        return True
    if any(urltools.bet_link_level({"domain_final": host_of(u)}, settings, []) in ("known", "hint_strong") for u in x["urls"]):
        return True
    return extract.bet_context(x) and bool(x["cta"] or x["payment"] or x["bonus"] or x["group"] or x["codes"])


def resolve_urls(urls, settings, known=(), only=None):
    """Etapa de REDE (chamar fora de transação). -> {url: [registros]}.
    Reaproveita resoluções já existentes; só acessa a rede para URLs em `only` (candidatos promissores) quando informado."""
    out = {}
    known = set(known)
    todo = [u for u in dict.fromkeys(urls) if u not in known]
    cached = _cached_records(todo)
    for u in todo:
        kind = urltools.domain_kind(host_of(u), settings)
        if kind == "tiktok" and not urltools.unwrap(u):
            continue
        if u in cached:
            out[u] = cached[u]
        elif settings.get("resolve_links", True) and (only is None or u in only):
            out[u] = urltools.expand_link(u, settings)
        else:
            out[u] = [{"url_original": u, "chain": [], "url_final": u, "domain_final": host_of(u),
                       "params": urltools.parse_params(u), "params_raw": urltools.raw_query(u), "page_title": "",
                       "page_text": "", "aggregator": "", "error": UNRESOLVED + " (pré-score/modo rápido)"}]
    return out


def deepen_links(cid, settings):
    """Etapa 3 do processamento progressivo: só candidatos promissores resolvem a cadeia completa."""
    with db.connect() as conn:
        rows = conn.execute("SELECT DISTINCT url_original, origin FROM links WHERE candidate_id=? AND error LIKE ?",
                            (cid, UNRESOLVED + "%")).fetchall()
    if not rows:
        return 0
    origin = {r["url_original"]: r["origin"] for r in rows}
    resolved = resolve_urls(list(origin), dict(settings, resolve_links=True))
    with db.connect() as conn:
        for u in origin:
            conn.execute("DELETE FROM links WHERE candidate_id=? AND url_original=? AND error LIKE ?", (cid, u, UNRESOLVED + "%"))
        for u, recs in resolved.items():
            save_links(conn, cid, {u: recs}, origin[u], settings)
        refresh_candidate(conn, cid, settings)
    return len(origin)


def save_links(conn, cid, resolved, origin, settings):
    n = 0
    have = conn.execute("SELECT COUNT(DISTINCT url_original) FROM links WHERE candidate_id=?", (cid,)).fetchone()[0]
    cap = settings.get("max_links_per_candidate", 6)
    for orig, recs in resolved.items():
        if conn.execute("SELECT 1 FROM links WHERE candidate_id=? AND url_original=?", (cid, orig)).fetchone():
            continue
        if have >= cap:
            break
        have += 1
        for r in recs:
            cur = conn.execute(
                "INSERT OR IGNORE INTO links(candidate_id,origin,url_original,chain,url_final,domain_final,base_domain,"
                "params,params_raw,page_title,page_text,aggregator,error,collected_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, origin, r["url_original"], jd(r["chain"]), r["url_final"], r["domain_final"],
                 base_domain(r["domain_final"] or ""), jd(r["params"]), r["params_raw"], r["page_title"],
                 r["page_text"], r["aggregator"], r["error"], now_iso()))
            n += cur.rowcount
    return n


def known_urls(conn, cid):
    return {r[0] for r in conn.execute("SELECT DISTINCT url_original FROM links WHERE candidate_id=?", (cid,))}


# ============================================================ análise

def load_bundle(conn, cid):
    c = conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    evs = []
    for e in conn.execute("SELECT * FROM evidences WHERE candidate_id=? ORDER BY id", (cid,)):
        evs.append({"id": e["id"], "kind": e["kind"], "caption": e["caption"], "text": e["text"],
                    "url_video": e["url_video"], "hashtags": jl(e["hashtags"]), "mentions": jl(e["mentions"]),
                    "tags": jl(e["tags"]), "source": e["source"], "source_url": e["source_url"],
                    "collected_at": e["collected_at"], "query": e["query"], "meta": jl(e["meta"], {})})
    links = []
    for l in conn.execute("SELECT * FROM links WHERE candidate_id=? ORDER BY id", (cid,)):
        links.append({"id": l["id"], "origin": l["origin"], "url_original": l["url_original"],
                      "chain": jl(l["chain"]), "url_final": l["url_final"], "domain_final": l["domain_final"],
                      "params": jl(l["params"]), "params_raw": l["params_raw"], "page_title": l["page_title"],
                      "page_text": l["page_text"], "aggregator": l["aggregator"], "error": l["error"]})
    visuals = [{"url_video": v["url_video"], "engine": v["engine"], "signals": jl(v["signals"], {}), "text": v["text"]}
               for v in conn.execute("SELECT * FROM visuals WHERE candidate_id=? ORDER BY id", (cid,))]
    return {"username": c["username"], "display_name": c["display_name"], "bio": c["bio"], "evidences": evs,
            "links": links, "visuals": visuals, "cand": c}


def _cluster_rows(bundle, res, settings):
    rows = set()
    ignore = [d.lower() for d in settings.get("ignore_domains", [])]
    for l in bundle["links"]:
        host = l.get("domain_final") or ""
        if l.get("aggregator"):
            rows.add(("AGREGADOR", base_domain(l["aggregator"]), ""))
        kind = urltools.domain_kind(host, settings)
        if host and kind == "other" and base_domain(host) not in ignore:
            bd = base_domain(host)
            ids = ",".join(f"{p['param']}={p['value']}" for p in l["params"] if p["type"] in ("affiliate", "referral"))
            rows.add(("DOMINIO", bd, ids))
        for p in l["params"]:
            if p["type"] in ("affiliate", "referral") and host:
                rows.add(("AFILIADO_ID", f"{base_domain(p['dominio'])}:{p['value']}", f"{p['param']}={p['value']}"))
            elif p["type"] == "campaign" and host:
                rows.add(("CAMPANHA", f"{base_domain(p['dominio'])}:{p['value']}", f"{p['param']}={p['value']}"))
    for c in res["codes"]:
        rows.add(("CODIGO", c.upper(), ""))
    for p in res["platforms"]:
        rows.add(("PLATAFORMA", p.lower(), ""))
    return rows


def refresh_candidate(conn, cid, settings=None, cascade=True):
    """Reanalisa um candidato e grava todos os campos derivados."""
    settings = settings or db.get_settings(conn)
    b = load_bundle(conn, cid)
    c = b["cand"]
    res = scoring.analyze(b, settings)
    ev_real = [e for e in b["evidences"] if e["kind"] != "relacao"]

    # link principal: preferir destino de apostas; senão primeiro não agregador
    prim = None
    bet_terms = scoring.extract.lexicon(settings, "bet_terms")
    for l in b["links"]:
        k = urltools.domain_kind(l["domain_final"] or "", settings)
        if k in ("messenger", "tiktok"):
            continue
        lvl = urltools.bet_link_level(l, settings, bet_terms)
        if lvl in ("known", "page", "hint_strong"):
            prim = l
            break
        prim = prim or l
    video_url = ""
    for e in b["evidences"]:
        if e["url_video"]:
            video_url = video_url or e["url_video"]
            if any(w in norm((e["caption"] or "") + (e["text"] or "")) for w in ("link na bio", "cadastr", "bonus", "saque")):
                video_url = e["url_video"]
                break
    domains = sorted({l["domain_final"] for l in b["links"] if l["domain_final"]
                      and urltools.domain_kind(l["domain_final"], settings) == "other"})
    ev_types = sorted({e["kind"] for e in ev_real} | {t for e in ev_real for t in e["tags"]} |
                      {REASON_TO_EVTYPE[r["key"]] for r in res["reasons"] if r["key"] in REASON_TO_EVTYPE})
    sources_ = [r[0] for r in conn.execute("SELECT DISTINCT source FROM discoveries WHERE candidate_id=?", (cid,))]
    bio_link = c["bio_link"] or next((l["url_original"] for l in b["links"] if l["origin"] == "bio"), "")
    base_score = scoring.clamp(res["raw"])

    conn.execute("""UPDATE candidates SET base_raw=?, score_base=?, base_reasons=?, flags=?, content_type=?, priority=?,
        recurring=?, platforms=?, games=?, hashtags=?, codes=?, affiliate_ids=?, domains=?, mentions=?, ev_types=?,
        video_url=?, link_original=?, link_final=?, domain_final=?, main_evidence=?, sources=?, evidence_count=?,
        bio_link=?, last_analyzed=?, visual_analysis=? WHERE id=?""",
                 (res["raw"], base_score, jd(res["reasons"]), jd(res["flags"]), res["content_type"], res["priority"],
                  int(res["recurring"]), jd(res["platforms"]), jd(res["games"]), jd(res["hashtags"]),
                  jd(res["codes"]), jd(res["affiliate_ids"]), jd(domains),
                  jd(sorted({m for e in b["evidences"] for m in e["mentions"]} | set(res["mentions"]))),
                  jd(ev_types), video_url, prim["url_original"] if prim else "", prim["url_final"] if prim else "",
                  prim["domain_final"] if prim else "", res["main_evidence"], jd(sources_), len(ev_real), bio_link,
                  now_iso(), res["visual_analysis"], cid))
    conn.execute("DELETE FROM cluster_members WHERE candidate_id=?", (cid,))
    for tipo, valor, extra in _cluster_rows(b, res, settings):
        conn.execute("INSERT OR IGNORE INTO cluster_members(candidate_id,tipo,valor,extra) VALUES(?,?,?,?)",
                     (cid, tipo, valor, extra))
    apply_bonus(conn, cid, settings)
    if cascade:
        refresh_peers(conn, cid, settings)


def _peer_ids(conn, cid):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT b.candidate_id FROM cluster_members a JOIN cluster_members b "
        "ON a.tipo=b.tipo AND a.valor=b.valor WHERE a.candidate_id=? AND a.tipo='DOMINIO' AND b.candidate_id!=?",
        (cid, cid))]


def refresh_peers(conn, cid, settings):
    for pid in _peer_ids(conn, cid):
        apply_bonus(conn, pid, settings)


def apply_bonus(conn, cid, settings):
    c = conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    reasons = jl(c["base_reasons"])
    raw = c["base_raw"]
    flags = jl(c["flags"], {})
    thr = settings.get("cluster_min_score") or settings["thresholds"]["revisar"]
    doms = [r[0] for r in conn.execute("SELECT valor FROM cluster_members WHERE candidate_id=? AND tipo='DOMINIO'", (cid,))]
    if doms and (flags.get("bet_content") or flags.get("link_bet")):
        q = ",".join("?" * len(doms))
        peers = conn.execute(
            f"SELECT DISTINCT c.id, cm.valor FROM cluster_members cm JOIN candidates c ON c.id=cm.candidate_id "
            f"WHERE cm.tipo='DOMINIO' AND cm.valor IN ({q}) AND c.id!=? AND c.score_base>=? "
            f"AND c.status NOT IN ('DESCARTADO','DUPLICADO','PERFIL INDISPONÍVEL','CONTEÚDO REMOVIDO')",
            (*doms, cid, thr)).fetchall()
        if peers:
            w = settings["weights"]["shared_domain"]
            reasons = reasons + [{"key": "shared_domain", "pts": w, "label": "mesmo domínio em múltiplos perfis promotores",
                                  "detail": f"{peers[0]['valor']} em {len(peers)} outro(s) perfil(is)"}]
            raw += w
    score = scoring.clamp(raw)
    conn.execute("UPDATE candidates SET score=?, classification=?, reasons=? WHERE id=?",
                 (score, scoring.classify(score, settings), jd(reasons), cid))
    apply_auto_status(conn, cid, settings)


def apply_auto_status(conn, cid, settings):
    c = conn.execute("SELECT status, status_manual, evidence_count, classification, profile_status, flags FROM candidates WHERE id=?",
                     (cid,)).fetchone()
    if c["status_manual"]:
        return
    if c["profile_status"] == "INDISPONIVEL":
        new = "PERFIL INDISPONÍVEL"
    elif c["evidence_count"] == 0:
        new = "NOVO"
    elif c["classification"].startswith("BAIXA"):
        if jl(c["flags"], {}).get("commercial_origin"):
            new = "NOVO"       # anúncio da fonte oficial: fica visível para revisão (nunca confirmado automaticamente)
        else:
            new = "DESCARTADO" if settings.get("auto_discard_low", False) else "BAIXA RELEVÂNCIA"
    else:
        new = "REVISAR"
    if new != c["status"]:
        conn.execute("UPDATE candidates SET status=? WHERE id=?", (new, cid))


def set_status(conn, cid, status, note=None):
    if status not in STATUSES:
        raise ValueError("status inválido")
    c = conn.execute("SELECT evidence_count FROM candidates WHERE id=?", (cid,)).fetchone()
    if status == "CONFIRMADO" and c["evidence_count"] == 0:
        raise PermissionError("PENDENTE DE VALIDAÇÃO: sem evidência registrada — adicione evidência antes de confirmar")
    conn.execute("UPDATE candidates SET status=?, status_manual=1 WHERE id=?", (status, cid))
    if note is not None:
        conn.execute("UPDATE candidates SET analyst_note=? WHERE id=?", (note, cid))
    refresh_peers(conn, cid, db.get_settings(conn))


def refresh_all(conn, settings=None):
    """Reanálise completa em duas passagens (bases primeiro, bônus de cluster depois)."""
    settings = settings or db.get_settings(conn)
    ids = [r[0] for r in conn.execute("SELECT id FROM candidates")]
    for i in ids:
        refresh_candidate(conn, i, settings, cascade=False)
    for i in ids:
        apply_bonus(conn, i, settings)
    return len(ids)


# ============================================================ ingestão de resultados de busca

def _ingest_urls(hits):
    urls = []
    for h in hits:
        urls += extract_urls(h["text"])
    return list(dict.fromkeys(urls))


def _ingest_commercial(conn, h, hunt):
    """Anúncio da Commercial Content API -> candidato + evidência automática. Dedupe global por ad.id.
    Só `ad.id` é confirmado: sem perfil/URL inventados (profile_url fica vazio = NÃO IDENTIFICADO).
    -> (cid|None, is_new)"""
    from . import commercial
    aid, term = h["ad_id"], h["term"]
    dk = "commercial|" + aid
    uname = commercial.CANDIDATE_PREFIX + aid
    if conn.execute("SELECT 1 FROM evidences WHERE dedupe_key=?", (dk,)).fetchone():
        row = conn.execute("SELECT id FROM candidates WHERE username=?", (uname,)).fetchone()
        if row:   # mesmo anúncio por outro termo: registra só a descoberta, sem duplicar
            conn.execute("INSERT OR IGNORE INTO discoveries(candidate_id,source,query,hunt,url,found_at) VALUES(?,?,?,?,?,?)",
                         (row["id"], commercial.SOURCE, term, hunt or "", h["source_url"], now_iso()))
        return None, False
    cid, is_new = upsert_candidate(conn, uname, source=commercial.SOURCE, query=term, url=h["source_url"], hunt=hunt,
                                   count_dup=False)
    if is_new:
        conn.execute("UPDATE candidates SET profile_url='', display_name=?, profile_status='ANUNCIO' WHERE id=?",
                     (f"Anúncio (ad.id {aid})", cid))
    when = now_iso()
    text = (f"{commercial.EVIDENCE_TEXT}\nTERM: {term}\nCOUNTRY: {h['country']}\nDATE_RANGE: {h['date_range']}\n"
            f"AD_ID: {aid}\nDATA_COLETA: {when}")
    meta = {"source": commercial.SOURCE, "source_type": commercial.SOURCE_TYPE, "term": term, "country": h["country"],
            "date_range": h["date_range"], "ad_id": aid, "raw": h["raw"]}
    add_evidence(conn, cid, "commercial", source=commercial.SOURCE, source_url=commercial.ENDPOINT, query=term, text=text,
                 hashtags=[], mentions=[], tags=[], meta=meta, dedupe_key=dk)
    return cid, is_new


def ingest_hits(conn, hits, settings, *, hunt="", origin=None, resolved=None):
    resolved = resolved or {}
    stats = {"found": len(hits), "new": 0, "dups": 0, "new_ids": [], "ids": set()}
    seen = {}
    for h in hits:
        if h.get("commercial"):
            cid, is_new = _ingest_commercial(conn, h, hunt)
            if cid is None:
                stats["dups"] += 1
            else:
                stats["ids"].add(cid)
                if is_new:
                    stats["new"] += 1
                    stats["new_ids"].append(cid)
            continue
        u = h["username"]
        src = origin or h["source"]
        if u not in seen:
            cid, is_new = upsert_candidate(conn, u, source=src, query=h["query"], url=h["source_url"], hunt=hunt)
            seen[u] = cid
            if is_new:
                stats["new"] += 1
                stats["new_ids"].append(cid)
            else:
                stats["dups"] += 1
        else:
            cid = seen[u]
            conn.execute("INSERT OR IGNORE INTO discoveries(candidate_id,source,query,hunt,url,found_at) VALUES(?,?,?,?,?,?)",
                         (cid, src, h["query"], hunt or "", h["source_url"], now_iso()))
        text = h["text"]
        kind = ("video" if h["video_url"] else "snippet") if text.strip() else "relacao"
        add_evidence(conn, cid, kind, source=h["source"], source_url=h["video_url"] or h["source_url"],
                     query=h["query"], url_video=h["video_url"], caption=text if h["video_url"] else "",
                     text="" if h["video_url"] else text, hashtags=h.get("hashtags"), meta=h.get("meta"))
        mine = {u2: resolved[u2] for u2 in extract_urls(text) if u2 in resolved}
        if mine:
            save_links(conn, cid, mine, "snippet", settings)
        stats["ids"].add(cid)
    # análise de cada candidato SEM cascata; depois UM recálculo de bônus em lote (candidatos + vizinhos de domínio),
    # em vez de reavaliar todos os vizinhos a cada candidato novo (O(n²) quando centenas compartilham o mesmo domínio)
    for cid in stats["ids"]:
        refresh_candidate(conn, cid, settings, cascade=False)
    batch = set(stats["ids"])
    for cid in stats["ids"]:
        batch.update(_peer_ids(conn, cid))
    for cid in batch:
        apply_bonus(conn, cid, settings)
    return stats


def log_search(conn, hunt, query, source, found, new, dups, errors, msg, t0):
    conn.execute("INSERT INTO search_log(ts,hunt,query,source,found,new,dups,errors,error_msg,duration_ms) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (now_iso(), hunt or "", query, source, found, new, dups, errors, msg or "", int((time.time() - t0) * 1000)))


def run_query(q, source, settings, hunt="", origin=None):
    """Executa UMA consulta em UMA fonte: rede -> ingestão -> log."""
    from . import mission
    if source == "commercial":
        pass                                  # a API oficial recebe o termo como está (bet/bets/aposta/apostas são ignorados nela)
    elif source == "tiktok_local":            # busca nativa: termos de jogo entram como estão; só o ruído puro é combinado
        from . import commercial
        if norm(q).strip('"# ') in commercial.NOISE_TERMS:
            q = mission.sanitize_query(q, settings)
    else:
        q = mission.sanitize_query(q, settings)   # termo genérico nunca vai sozinho
    t0 = time.time()
    try:
        hits, err, _ = sources.run_source(source, q)
    except Exception as e:  # fonte nunca derruba a ferramenta; detalhe técnico só no arquivo de log
        log.exception("FONTE=%s | CONSULTA=%s | falha inesperada", source, q)
        hits, err = [], f"erro interno na fonte ({type(e).__name__}) — detalhes em logs/bethunter.log"
    resolved = {}
    if hits:
        try:
            with db.connect() as conn:  # duplicidade ANTES da análise pesada: vídeo já conhecido não é reprocessado
                seen_vids = {r[0] for r in conn.execute("SELECT url_video FROM evidences WHERE url_video!=''")}
            fresh = [h for h in hits if not (h["video_url"] and h["video_url"] in seen_vids)]
            only = {u for h in fresh if promising_text(h["text"], settings) for u in extract_urls(h["text"])}
            resolved = resolve_urls(_ingest_urls(fresh), settings, only=only)
        except Exception as e:
            log.exception("CONSULTA=%s | falha na resolução de URLs", q)
            err = (err or "") + f" (resolução de URLs: {type(e).__name__})"
    label = sources.SOURCE_LABELS[source] if source in ("commercial", "tiktok_local") else (origin or sources.SOURCE_LABELS.get(source, source))
    if err:
        log.warning("FONTE=%s | CONSULTA=%s | ERRO=%s", sources.SOURCE_LABELS.get(source, source), q, err)
    with db.connect() as conn:
        st = ingest_hits(conn, hits, settings, hunt=hunt, origin=label, resolved=resolved) if hits else \
            {"found": 0, "new": 0, "dups": 0, "new_ids": [], "ids": set()}
        log_search(conn, hunt, q, sources.SOURCE_LABELS.get(source, source), st["found"], st["new"], st["dups"],
                   1 if err else 0, err, t0)
    st["error"] = err
    return st


# ============================================================ investigação de perfil

def cache_info(conn, row, settings):
    """Se o item foi analisado recentemente -> dict do aviso 'ESTE ITEM JÁ FOI ANALISADO'."""
    if not row or not row["last_analyzed"] or row["evidence_count"] == 0:
        return None
    from datetime import datetime, timedelta
    try:
        when = datetime.fromisoformat(row["last_analyzed"])
    except ValueError:
        return None
    if datetime.now(when.tzinfo) - when > timedelta(days=settings.get("cache_days", 7)):
        return None
    evs = [{"kind": e["kind"], "url": e["url_video"], "texto": clip(e["caption"] or e["text"], 200)}
           for e in conn.execute("SELECT * FROM evidences WHERE candidate_id=? AND kind!='relacao' ORDER BY id LIMIT 5",
                                 (row["id"],))]
    return {"id": row["id"], "username": row["username"], "data_anterior": row["last_analyzed"],
            "status": row["status"], "score": row["score"], "classificacao": row["classification"],
            "motivos": jl(row["reasons"]), "evidencias": evs, "evidencia_principal": row["main_evidence"]}


def investigate(target, *, force=False, source="perfil-semente", query="", expand=False):
    """INVESTIGAR PERFIL: coleta perfil + vídeos disponíveis, resolve links, pontua. Aceita @user, URL de perfil ou de vídeo."""
    username = normalize_username(target)
    if not username:
        return {"ok": False, "error": "username/URL do TikTok inválido"}
    video = parse_tiktok_url(target) if "tiktok.com" in target.lower() else None
    with db.connect() as conn:
        settings = db.get_settings(conn)
        row = conn.execute("SELECT * FROM candidates WHERE username=?", (username,)).fetchone()
        if row and not force:
            ci = cache_info(conn, row, settings)
            if ci:
                return {"ok": True, "cached": True, "previous": ci, "id": row["id"]}
    # ---- rede (fora de transação)
    prof = sources.fetch_profile(username)
    vids = list(prof["videos"])
    vdata = None
    if video and video.get("video_id"):
        vurl = canonical_video_url(target)
        if not any(v["url"] == vurl for v in vids):
            vdata = sources.fetch_video(vurl)
            if vdata["ok"]:
                vids.insert(0, {"url": vurl, "desc": vdata["desc"], "hashtags": vdata["hashtags"], "mentions": vdata["mentions"]})
    urls = []
    if prof["bio_link"]:
        urls.append(prof["bio_link"])
    urls += extract_urls(prof["bio"])
    for v in vids:
        urls += extract_urls(v["desc"])
    urls = list(dict.fromkeys(urls))
    with db.connect() as conn:
        have = known_urls(conn, row["id"]) if row else set()
    resolved = resolve_urls(urls, settings, known=have)
    # ---- gravação
    with db.connect() as conn:
        cid, is_new = upsert_candidate(conn, username, source=source, query=query or target, url=target,
                                       profile_id=prof["user_id"] or None, count_dup=False)
        ps = "OK" if prof["ok"] else ("INDISPONIVEL" if prof["unavailable"] else "NAO_COLETADO")
        conn.execute("UPDATE candidates SET display_name=COALESCE(NULLIF(?,''),display_name), bio=COALESCE(NULLIF(?,''),bio), "
                     "bio_link=COALESCE(NULLIF(?,''),bio_link), profile_id=COALESCE(NULLIF(?,''),profile_id), "
                     "profile_status=CASE WHEN ?='NAO_COLETADO' AND profile_status IN ('OK','INDISPONIVEL') THEN profile_status ELSE ? END "
                     "WHERE id=?", (prof["display_name"], prof["bio"], prof["bio_link"], prof["user_id"], ps, ps, cid))
        if prof["bio"]:
            add_evidence(conn, cid, "bio", source="TikTok (perfil)", source_url=prof["source_url"], text=prof["bio"])
        if prof["bio_link"]:
            add_evidence(conn, cid, "link", source="TikTok (perfil)", source_url=prof["source_url"],
                         text=f"Link do perfil: {prof['bio_link']}")
        for v in vids:
            add_evidence(conn, cid, "video", source="TikTok (perfil)", source_url=v["url"] or prof["source_url"],
                         url_video=v["url"], caption=v["desc"], hashtags=v["hashtags"], mentions=v["mentions"])
        if video and video.get("video_id") and not any(v["url"] == canonical_video_url(target) for v in vids):
            add_evidence(conn, cid, "relacao", source="importação", url_video=canonical_video_url(target),
                         source_url=target, text="URL de vídeo informada (conteúdo não coletado automaticamente)")
        if resolved:
            bio_urls = set([prof["bio_link"]] + extract_urls(prof["bio"]))
            save_links(conn, cid, {u: r for u, r in resolved.items() if u in bio_urls}, "bio", settings)
            save_links(conn, cid, {u: r for u, r in resolved.items() if u not in bio_urls}, "video", settings)
        refresh_candidate(conn, cid, settings)
        row = conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        res = {"ok": True, "cached": False, "id": cid, "is_new": is_new, "profile_fetched": prof["ok"],
               "profile_error": prof["error"], "videos_collected": len(vids), "score": row["score"],
               "classification": row["classification"], "evidence_count": row["evidence_count"]}
    if res["score"] >= settings["thresholds"]["revisar"] and res["evidence_count"]:
        from . import visual
        res["visual"] = visual.run_visual(cid, settings)["status"]
        with db.connect() as conn:
            r2 = conn.execute("SELECT score, classification FROM candidates WHERE id=?", (cid,)).fetchone()
            res["score"], res["classification"] = r2["score"], r2["classification"]
    if expand:
        res["expansion"] = expand_candidate(cid)
    return res


# ============================================================ expansão

def indicator_queries(row, settings):
    """Indicadores do perfil convertidos em consultas -> [(tipo, valor, consulta)]"""
    out = []
    ignore = set(settings.get("ignore_domains", []))
    for d in jl(row["domains"]):
        bd = base_domain(d)
        if urltools.domain_kind(d, settings) == "other" and bd not in ignore:
            out.append(("domínio", bd, f'"{bd}"'))
    for a in jl(row["affiliate_ids"])[:3]:
        out.append(("código", a, f'"{a}"'))
    for c in jl(row["codes"])[:3]:
        out.append(("código", c, f'"{c}"'))
    from . import extract
    for h in extract.hashtag_bet_related(jl(row["hashtags"]), settings)[:3]:
        out.append(("hashtag", "#" + h, "#" + h))
    for p in jl(row["platforms"])[:2]:
        out.append(("plataforma", p, f'"{p}" link na bio'))
    for g in jl(row["games"])[:2]:
        out.append(("jogo", g, f'{g} link na bio'))
    return out


def drop_offline_local(srcs, settings, job=None):
    """Serviço local offline/indisponível não é fatal: remove a fonte e segue com as demais."""
    if "tiktok_local" not in srcs:
        return srcs
    from . import tiktok_local
    est = tiktok_local.check(settings)
    if est["estado"] == tiktok_local.OK:
        return srcs
    if job:
        job.log(f"TikTok Search Local {est['estado']}: fonte ignorada")
    return [x for x in srcs if x != "tiktok_local"]


def expand_candidate(cid, search=True, job=None):
    """ENCONTRAR PERFIS RELACIONADOS: menções/marcados + buscas por indicadores + relacionados já na base."""
    with db.connect() as conn:
        settings = db.get_settings(conn)
        row = conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        if not row:
            return {"ok": False, "error": "candidato não encontrado"}
        seed = row["username"]
        mentions = [m for m in jl(row["mentions"]) if m != seed]
        new_mentions, existing_m = [], []
        for m in mentions:
            if not USERNAME_RE.match(m):
                continue
            mid, is_new = upsert_candidate(conn, m, source="menção", query=f"@{seed}", url=row["profile_url"])
            add_evidence(conn, mid, "relacao", source="menção", source_url=row["profile_url"], query=f"@{seed}",
                         text=f"Mencionado/marcado por @{seed}")
            refresh_candidate(conn, mid, settings)
            (new_mentions if is_new else existing_m).append(m)
        related = [dict(r) for r in conn.execute(
            "SELECT DISTINCT c.id, c.username, c.status, c.score, a.tipo, a.valor FROM cluster_members a "
            "JOIN cluster_members b ON a.tipo=b.tipo AND a.valor=b.valor JOIN candidates c ON c.id=b.candidate_id "
            "WHERE a.candidate_id=? AND b.candidate_id!=? ORDER BY c.score DESC", (cid, cid))]
        queries = indicator_queries(row, settings)[: settings.get("expand_max_queries", 8)]
    searches, search_new = [], 0
    if search:
        for tipo, val, q in queries:
            srcs = list(settings.get("expand_sources", ["ddg", "bing"]))
            if tipo == "hashtag":
                srcs = ["tiktok_tag"] + [s for s in srcs if s != "tiktok"]
            srcs = sources.by_priority(drop_offline_local(srcs, settings, job))
            for s in srcs:
                st = run_query(q, s, settings, hunt=f"expansão de @{seed}", origin="expansão automática")
                searches.append({"tipo": tipo, "valor": val, "consulta": q, "fonte": s, "encontrados": st["found"],
                                 "novos": st["new"], "erro": st.get("error")})
                search_new += st["new"]
                if job:
                    job.log(f"{q} [{s}] → {st['found']} ({st['new']} novos)")
                time.sleep(settings.get("request_delay", 0))
    return {"ok": True, "seed": seed, "menções_novas": new_mentions, "menções_existentes": existing_m,
            "novos_por_busca": search_new, "relacionados_existentes": related,
            "buscas": searches}


def indicator_search(kind, value, job=None):
    """Busca recursiva: transforma um indicador em nova consulta."""
    with db.connect() as conn:
        settings = db.get_settings(conn)
    v = value.strip()
    if kind == "domínio":
        q, origin = f'"{v}"', "domínio"
    elif kind in ("código", "afiliado"):
        q, origin = f'"{v}"', "link de afiliado" if "=" in v else "código promocional"
    elif kind == "hashtag":
        q, origin = "#" + v.lstrip("#"), "hashtag"
    elif kind == "plataforma":
        q, origin = f'"{v}" link na bio', "plataforma"
    elif kind == "usuário":
        q, origin = "@" + v.lstrip("@"), "usuário"
    else:
        q, origin = v, "busca livre"
    total = {"found": 0, "new": 0, "dups": 0, "errors": []}
    srcs = list(settings.get("expand_sources", ["ddg", "bing", "tiktok"]))
    if kind == "hashtag":
        srcs = ["tiktok_tag"] + srcs
    if kind == "usuário":
        srcs = ["tiktok_local"]                 # busca de usuário: só a fonte nativa
    elif "tiktok_local" not in srcs:
        srcs = ["tiktok_local"] + srcs          # fonte principal primeiro
    srcs = sources.by_priority(drop_offline_local(srcs, settings, job))
    for s in srcs:
        st = run_query(q, s, settings, hunt=f"busca recursiva: {kind}", origin=origin)
        total["found"] += st["found"]; total["new"] += st["new"]; total["dups"] += st["dups"]
        if st.get("error"):
            total["errors"].append(f"{s}: {st['error']}")
        if job:
            job.log(f"{q} [{s}] → {st['found']} ({st['new']} novos)")
        time.sleep(settings.get("request_delay", 0))
    total["consulta"] = q
    return total


# ============================================================ caças

def _all_hunt_queries(conn, hunt, settings):
    """Consultas de uma caça: estáticas + dinâmicas (afiliados, domínios conhecidos)."""
    qs = [(q, None) for q in jl(hunt["queries"])]
    kind = hunt["kind"]
    if kind == "matrix":
        from . import mission
        qs += [(q, None) for q in mission.generate_queries(settings)]
    if kind == "commercial":   # termos editáveis em CONFIG (+ os da própria caça)
        qs += [(t, None) for t in settings.get("commercial_api_terms", [])]
    if kind == "affiliate_links":
        seen = set()
        for r in conn.execute("SELECT params FROM links"):
            for p in jl(r[0]):
                if p["type"] in ("affiliate", "referral"):
                    k = f"{p['param']}={p['value']}"
                    if k not in seen and len(seen) < 25:
                        seen.add(k)
                        qs.append((f'"{k}"', "link de afiliado"))
    elif kind == "known_domains":
        doms = list(settings.get("bet_domains", []))
        for r in conn.execute("SELECT DISTINCT base_domain, domain_final FROM links WHERE domain_final!=''"):
            if urltools.domain_kind(r[1], settings) == "other" and r[0] not in settings.get("ignore_domains", []):
                lvl = urltools.bet_link_level({"domain_final": r[1]}, settings, scoring.extract.lexicon(settings, "bet_terms"))
                if lvl in ("known", "hint_strong", "page") or r[0] in doms:
                    doms.append(r[0])
        for d in list(dict.fromkeys(doms))[:30]:
            qs.append((f'"{d}"', "domínio"))
    return qs


def run_hunt(hunt_id, job=None):
    with db.connect() as conn:
        settings = db.get_settings(conn)
        h = conn.execute("SELECT * FROM hunts WHERE id=?", (hunt_id,)).fetchone()
        if not h:
            return {"ok": False, "error": "caça não encontrada"}
        qs = _all_hunt_queries(conn, h, settings)
        expand_ids = []
        if h["kind"] == "expand_confirmed":
            expand_ids = [r[0] for r in conn.execute("SELECT id FROM candidates WHERE status='CONFIRMADO' LIMIT 40")]
    srcs = jl(h["sources"]) or sources.SEARCH_SOURCES
    from . import commercial
    if "commercial" in srcs and not commercial.usable(settings):
        srcs = [x for x in srcs if x != "commercial"]      # não configurada: ignora sem repetir erro
        if job:
            job.log("TikTok Commercial Content API NÃO CONFIGURADA: fonte ignorada nesta caça")
    if "tiktok_local" in srcs:
        from . import tiktok_local
        est = tiktok_local.check(settings)
        if est["estado"] != tiktok_local.OK:              # offline não é fatal: segue com as demais fontes
            srcs = [x for x in srcs if x != "tiktok_local"]
            if job:
                job.log(f"TikTok Search Local {est['estado']}: fonte ignorada nesta caça")
    srcs = sources.by_priority(srcs)
    tasks = [(q, s, o) for q, o in qs for s in srcs]
    total_steps = len(tasks) + len(expand_ids)
    tot = {"found": 0, "new": 0, "dups": 0, "errors": 0}
    new_ids = []
    done = 0
    for q, s, o in tasks:
        st = run_query(q, s, settings, hunt=h["name"], origin=o)
        tot["found"] += st["found"]; tot["new"] += st["new"]; tot["dups"] += st["dups"]
        tot["errors"] += 1 if st.get("error") else 0
        new_ids += st["new_ids"]
        done += 1
        if job:
            job.progress(done, total_steps, f"{q} [{s}] → {st['found']} ({st['new']} novos)"
                         + (f" ⚠ {st['error']}" if st.get("error") else ""))
        time.sleep(settings.get("request_delay", 0))
    for cid in expand_ids:
        r = expand_candidate(cid, job=job)
        tot["new"] += r.get("novos_por_busca", 0) + len(r.get("menções_novas", []))
        done += 1
        if job:
            job.progress(done, total_steps, f"expandido @{r.get('seed')}")
    if settings.get("enrich_after_search") and new_ids:
        n = enrich(new_ids, settings, job)
        tot["enriched"] = n
    with db.connect() as conn:
        conn.execute("UPDATE hunts SET last_run=? WHERE id=?", (now_iso(), hunt_id))
    return {"ok": True, **tot}


def enrich(ids, settings, job=None):
    """Coleta perfil (bio/vídeos/link) dos melhores candidatos novos."""
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT id, username FROM candidates WHERE id IN ({','.join('?' * len(ids))}) "
            f"AND profile_status='NAO_COLETADO' ORDER BY score DESC, priority ASC LIMIT ?",
            (*ids, settings.get("enrich_max", 40))).fetchall() if ids else []
    n = 0
    for r in rows:
        try:
            res = investigate(r["username"], force=True, source=None)
            n += 1 if res.get("profile_fetched") else 0
        except Exception as e:
            log.exception("enriquecer @%s", r["username"])
            if job:
                job.log(f"enriquecer @{r['username']}: falhou ({type(e).__name__})")
        if job:
            job.log(f"perfil coletado: @{r['username']}")
        time.sleep(settings.get("request_delay", 0))
    return n


# ============================================================ importação

def classify_line(s):
    s = s.strip().strip(",;\t ")
    if not s or s.startswith("//"):
        return None
    if s.startswith("#") and re.match(r"^#[\wÀ-ÿ]{2,60}$", s):
        return ("hashtag", s.lstrip("#").lower())
    if "tiktok.com" in s.lower():
        m = re.search(r"tiktok\.com/tag/([^/?#\s]+)", s, re.I)
        if m:
            return ("hashtag", m.group(1).lower())
        r = parse_tiktok_url(s if "://" in s else "https://" + s.lstrip("/"))
        if r:
            return ("video" if r["video_id"] else "profile", s)
        return ("invalid", s)
    if s.startswith("@"):
        u = normalize_username(s)
        return ("profile", s) if u else ("invalid", s)
    if re.match(r"(?i)^https?://", s) or (URL_RE.fullmatch(s) and " " not in s):
        return ("domain", host_of(s))
    if USERNAME_RE.match(s):
        return ("profile", s)
    return ("invalid", s)


def _csv_items(text):
    rd = list(csv.reader(io.StringIO(text)))
    if not rd:
        return []
    head = [norm(h) for h in rd[0]]
    cols = [i for i, h in enumerate(head) if h in ("username", "usuario", "perfil", "url", "url_perfil", "link",
                                                     "dominio", "domain", "hashtag", "url_video", "handle")]
    data = rd[1:] if cols else rd
    cols = cols or [0]
    return [row[i].strip() for row in data for i in cols if i < len(row) and row[i].strip()]


def parse_import(text, filename=""):
    if filename.lower().endswith(".csv") or (text.count(",") > text.count("\n") and "\n" in text and
                                              re.search(r"(?i)username|url|dominio|domain", text.split("\n", 1)[0])):
        return _csv_items(text)
    return [ln for ln in text.splitlines()]


def import_items(text, *, filename="", source="lista importada", domains_as_bet=True):
    """Importa lista (um item por linha). Perfis/URLs viram candidatos PENDENTES até haver evidência."""
    items = parse_import(text, filename)
    summ = {"perfis_novos": 0, "perfis_duplicados": 0, "videos": 0, "dominios": 0, "hashtags": 0, "invalidos": 0,
            "linhas": 0, "novos_usernames": [], "dominios_lista": [], "hashtags_lista": []}
    with db.connect() as conn:
        settings = db.get_settings(conn)
        patch_domains, patch_tags = list(settings["bet_domains"]), list(settings["hashtags"])
        for raw in items:
            c = classify_line(raw)
            if not c:
                continue
            summ["linhas"] += 1
            kind, val = c
            if kind in ("profile", "video"):
                u = normalize_username(val)
                cid, is_new = upsert_candidate(conn, u, source=source, query=raw.strip(), url=val)
                if is_new:
                    summ["perfis_novos"] += 1
                    summ["novos_usernames"].append(u)
                else:
                    summ["perfis_duplicados"] += 1
                if kind == "video":
                    vurl = canonical_video_url(val)
                    add_evidence(conn, cid, "relacao", source=source, source_url=vurl, url_video=vurl,
                                 text="URL de vídeo importada (conteúdo ainda não coletado)")
                    summ["videos"] += 1
                refresh_candidate(conn, cid, settings)
            elif kind == "hashtag":
                summ["hashtags"] += 1
                summ["hashtags_lista"].append(val)
                if val not in patch_tags:
                    patch_tags.append(val)
            elif kind == "domain":
                summ["dominios"] += 1
                bd = base_domain(val)
                summ["dominios_lista"].append(bd)
                if domains_as_bet and bd not in patch_domains:
                    patch_domains.append(bd)
            else:
                summ["invalidos"] += 1
        db.save_settings(conn, {"bet_domains": patch_domains, "hashtags": patch_tags})
    return summ


# ============================================================ evidência manual

def add_manual_evidence(cid, *, text="", url_video="", link="", tags=None, source="analista"):
    """Fallback quando a coleta automática não é possível: analista cola texto/URLs vistos no TikTok."""
    with db.connect() as conn:
        settings = db.get_settings(conn)
        have = known_urls(conn, cid)
    urls = extract_urls(text + " " + link)
    resolved = resolve_urls(urls, settings, known=have)
    if not (text.strip() or url_video.strip() or link.strip()):
        raise ValueError("informe texto, URL do vídeo ou link")
    with db.connect() as conn:
        c = conn.execute("SELECT username FROM candidates WHERE id=?", (cid,)).fetchone()
        if not c:
            raise ValueError("candidato não encontrado")
        vurl = canonical_video_url(url_video.strip()) if url_video.strip() else ""
        ok = add_evidence(conn, cid, "manual", source=source, source_url=vurl or profile_url(c["username"]),
                          url_video=vurl, caption=text.strip(), tags=tags or [])
        if resolved:
            save_links(conn, cid, resolved, "manual", settings)
        refresh_candidate(conn, cid, settings)
    return ok


def add_manual_candidate(username, **kw):
    """Cria candidato a partir de uma evidência manual (sem depender de busca)."""
    u = normalize_username(username)
    if not u:
        raise ValueError("username inválido")
    with db.connect() as conn:
        cid, _ = upsert_candidate(conn, u, source="manual", query="evidência manual", count_dup=False)
    add_manual_evidence(cid, **kw)
    return cid


# ============================================================ jobs em background

JOBS = {}


class Job:
    def __init__(self, label):
        self.id = uuid.uuid4().hex[:10]
        self.label, self.status, self.total, self.done = label, "running", 0, 0
        self.lines, self.result, self.error = [], None, None
        self.started = time.time()
        self.cancelled, self.stats, self.mission_id = False, {}, None

    def log(self, msg):
        self.lines.append(msg)
        del self.lines[:-200]

    def progress(self, done, total, msg=""):
        self.done, self.total = done, total
        if msg:
            self.log(msg)

    def to_dict(self):
        return {"id": self.id, "label": self.label, "status": self.status, "total": self.total, "done": self.done,
                "lines": self.lines[-40:], "result": self.result, "error": self.error, "stats": self.stats,
                "cancelled": self.cancelled,
                "elapsed": round(time.time() - self.started, 1)}


def any_cancel():
    """Há algum job em execução com pedido de interrupção? (usado pela paginação da Commercial API)"""
    return any(j.cancelled for j in JOBS.values() if j.status == "running")


def start_job(label, fn, sync=False):
    job = Job(label)
    JOBS[job.id] = job

    def runner():
        try:
            job.result = fn(job)
            job.status = "done"
        except Exception as e:  # noqa
            log.exception("job '%s' falhou", label)
            job.status, job.error = "error", f"{type(e).__name__}: {str(e)[:150]} (detalhes em logs/bethunter.log)"

    if sync:
        runner()
    else:
        threading.Thread(target=runner, daemon=True).start()
    return job
