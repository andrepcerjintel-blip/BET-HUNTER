"""Consultas de leitura: listagem/filtros, painel, clusters, métricas, missão, log."""
import re
from collections import Counter

from . import db, pipeline
from .db import jl
from .util import NAO_IDENTIFICADO, split_iso

LIST_COLS = ("id,username,profile_url,display_name,status,score,classification,content_type,main_evidence,priority,"
             "recurring,platforms,games,hashtags,codes,affiliate_ids,domains,video_url,link_original,link_final,"
             "domain_final,sources,first_source,evidence_count,profile_status,analyst_note,first_seen,last_analyzed,"
             "ev_types,flags,mentions,visual_analysis")


def _like_json(col, val):
    return f"{col} LIKE ?", f'%"{val}"%'


def build_where(f):
    """Filtros comuns (listagem, exportação, cópia, ações em lote)."""
    w, a = [], []
    view = f.get("view", "results")
    if view == "results":
        w.append("evidence_count>0")
        if not f.get("status"):  # BAIXA RELEVÂNCIA fica fora da revisão principal (mas registrada)
            w.append("status!='BAIXA RELEVÂNCIA'")
    elif view == "pending":
        w.append("evidence_count=0")
    if f.get("min_score") not in (None, ""):
        w.append("score>=?"); a.append(int(f["min_score"]))
    if f.get("status"):
        sts = f["status"] if isinstance(f["status"], list) else str(f["status"]).split("|")
        w.append(f"status IN ({','.join('?' * len(sts))})"); a += sts
    if f.get("classification"):
        w.append("classification LIKE ?"); a.append(f["classification"] + "%")
    for key, col in (("platform", "platforms"), ("game", "games"), ("hashtag", "hashtags")):
        if f.get(key):
            v = f[key].lstrip("#") if key == "hashtag" else f[key]
            w.append(f"LOWER({col}) LIKE ?"); a.append(f'%"{v.lower()}"%')
    if f.get("domain"):
        w.append("LOWER(domains) LIKE ?"); a.append(f'%{f["domain"].lower()}%')
    if f.get("ev_type"):
        w.append("ev_types LIKE ?"); a.append(f'%"{f["ev_type"]}"%')
    if f.get("with_link") in (True, "1", 1, "true"):
        w.append("link_final!=''")
    if f.get("with_aff") in (True, "1", 1, "true"):
        w.append("(affiliate_ids!='[]' OR codes!='[]')")
    if f.get("date_from"):
        w.append("substr(first_seen,1,10)>=?"); a.append(f["date_from"])
    if f.get("date_to"):
        w.append("substr(first_seen,1,10)<=?"); a.append(f["date_to"])
    if f.get("source"):
        w.append("sources LIKE ?"); a.append(f'%"{f["source"]}"%')
    if f.get("cluster"):
        tipo, _, valor = f["cluster"].partition(":")
        w.append("id IN (SELECT candidate_id FROM cluster_members WHERE tipo=? AND valor=?)"); a += [tipo, valor]
    if f.get("q"):
        w.append("(username LIKE ? OR bio LIKE ? OR main_evidence LIKE ? OR analyst_note LIKE ? OR display_name LIKE ?)")
        a += [f"%{f['q']}%"] * 5
    if f.get("ids"):
        ids = [int(i) for i in f["ids"]]
        w.append(f"id IN ({','.join('?' * len(ids))})"); a += ids
    return (" AND ".join(w) or "1=1"), a


ORDERS = {
    "priority": "priority ASC, score DESC, id ASC",     # MAIS IMPORTANTES PRIMEIRO
    "score": "score DESC, id DESC",
    "recent": "id DESC",
    "username": "username ASC",
}


def row_to_dict(r):
    d = dict(r)
    for k in ("platforms", "games", "hashtags", "codes", "affiliate_ids", "domains", "sources", "ev_types", "mentions"):
        if k in d:
            d[k] = jl(d[k])
    for k in ("flags",):
        if k in d:
            d[k] = jl(d[k], {})
    d["pendente"] = d.get("evidence_count", 0) == 0
    return d


def list_candidates(conn, f, page=1, per_page=50, sort="priority"):
    where, args = build_where(f)
    total = conn.execute(f"SELECT COUNT(*) FROM candidates WHERE {where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT {LIST_COLS} FROM candidates WHERE {where} ORDER BY {ORDERS.get(sort, ORDERS['priority'])} "
        f"LIMIT ? OFFSET ?", (*args, per_page, (max(page, 1) - 1) * per_page)).fetchall()
    return {"total": total, "page": page, "per_page": per_page, "items": [row_to_dict(r) for r in rows]}


def candidate_detail(conn, cid):
    r = conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    if not r:
        return None
    d = row_to_dict(r)
    d["reasons"] = jl(r["reasons"])
    d["evidences"] = []
    for e in conn.execute("SELECT * FROM evidences WHERE candidate_id=? ORDER BY (kind='relacao'), id", (cid,)):
        e = dict(e)
        for k in ("hashtags", "mentions", "tags"):
            e[k] = jl(e[k])
        e["meta"] = jl(e.get("meta"), {})
        e["data"], e["hora"], e["fuso"] = split_iso(e["collected_at"])
        d["evidences"].append(e)
    d["links"] = []
    for l in conn.execute("SELECT * FROM links WHERE candidate_id=? ORDER BY id", (cid,)):
        l = dict(l)
        l["chain"] = jl(l["chain"]); l["params"] = jl(l["params"])
        d["links"].append(l)
    d["discoveries"] = [dict(x) for x in conn.execute(
        "SELECT source, query, hunt, url, found_at FROM discoveries WHERE candidate_id=? ORDER BY id", (cid,))]
    d["clusters"] = [dict(x) for x in conn.execute(
        "SELECT tipo, valor, extra FROM cluster_members WHERE candidate_id=?", (cid,))]
    # indicadores para busca recursiva
    s = db.get_settings(conn)
    d["visuals"] = [{"url_video": v["url_video"], "engine": v["engine"], "signals": jl(v["signals"], {}),
                     "text": v["text"]} for v in conn.execute("SELECT * FROM visuals WHERE candidate_id=?", (cid,))]
    d["indicators"] = [{"tipo": t, "valor": v, "consulta": q} for t, v, q in pipeline.indicator_queries(r, s)]
    d["total_score_bruto"] = r["base_raw"] + sum(x["pts"] for x in d["reasons"] if x["key"] == "shared_domain")
    return d


def stats(conn):
    one = lambda q, *a: conn.execute(q, a).fetchone()[0]
    s = db.get_settings(conn)
    inactive = "status NOT IN ('DESCARTADO','BAIXA RELEVÂNCIA','DUPLICADO','PERFIL INDISPONÍVEL','CONTEÚDO REMOVIDO')"
    dom = set()
    for (d,) in conn.execute("SELECT domains FROM candidates WHERE domains!='[]'"):
        dom.update(jl(d))
    dups_log = one("SELECT COALESCE(SUM(dups),0) FROM search_log")
    return {
        "total": one("SELECT COUNT(*) FROM candidates"),
        "unicos": one("SELECT COUNT(*) FROM candidates"),
        "brutos": one("SELECT COALESCE(SUM(found),0) FROM search_log"),
        "baixa": one("SELECT COUNT(*) FROM candidates WHERE status='BAIXA RELEVÂNCIA'"),
        "em_revisao": one("SELECT COUNT(*) FROM candidates WHERE status IN ('NOVO','REVISAR') AND evidence_count>0 "
                          "AND classification NOT LIKE 'BAIXA%'"),
        "alta": one(f"SELECT COUNT(*) FROM candidates WHERE classification LIKE 'ALTA%' AND evidence_count>0 AND {inactive}"),
        "revisar": one("SELECT COUNT(*) FROM candidates WHERE status='REVISAR'"),
        "descartados": one("SELECT COUNT(*) FROM candidates WHERE status='DESCARTADO'"),
        "confirmados": one("SELECT COUNT(*) FROM candidates WHERE status='CONFIRMADO'"),
        "encaminhados": one("SELECT COUNT(*) FROM candidates WHERE status='JÁ ENCAMINHADO'"),
        "duplicados": dups_log + one("SELECT COUNT(*) FROM candidates WHERE status='DUPLICADO'"),
        "pendentes": one("SELECT COUNT(*) FROM candidates WHERE evidence_count=0"),
        "dominios": len(dom),
        "clusters": one(f"SELECT COUNT(*) FROM (SELECT 1 FROM cluster_members cm JOIN candidates c ON c.id=cm.candidate_id "
                        f"WHERE c.{inactive} GROUP BY cm.tipo, cm.valor HAVING COUNT(*)>=2)"),
        "goal": s["goal"],
    }


CLUSTER_LABEL = {"DOMINIO": "Domínio", "AGREGADOR": "Agregador", "PLATAFORMA": "Plataforma", "CODIGO": "Código promocional",
                 "CAMPANHA": "Campanha", "AFILIADO_ID": "ID de afiliado"}


def clusters(conn, min_profiles=2, tipo=None):
    q = ("SELECT cm.tipo, cm.valor, cm.extra, c.id, c.username, c.status, c.score, c.link_final FROM cluster_members cm "
         "JOIN candidates c ON c.id=cm.candidate_id WHERE c.status NOT IN ('DUPLICADO','BAIXA RELEVÂNCIA')")
    args = []
    if tipo:
        q += " AND cm.tipo=?"; args.append(tipo)
    groups = {}
    for r in conn.execute(q + " ORDER BY c.score DESC", args):
        g = groups.setdefault((r["tipo"], r["valor"]), {"members": {}, "aff": set(), "links": set()})
        g["members"][r["id"]] = {"id": r["id"], "username": r["username"], "status": r["status"], "score": r["score"]}
        if r["extra"]:
            g["aff"].update(x for x in r["extra"].split(",") if x)
            g["members"][r["id"]].setdefault("aff", set()).update(x for x in r["extra"].split(",") if x)
        if r["link_final"]:
            g["links"].add(r["link_final"])
    out = []
    for (t, v), g in groups.items():
        mem = [m for m in g["members"].values() if m["status"] not in pipeline.INACTIVE or m["status"] == "DESCARTADO"]
        active = [m for m in mem if m["status"] not in pipeline.INACTIVE]
        if len(active) < min_profiles:
            continue
        cnt = Counter(m["status"] for m in mem)
        out.append({
            "cluster": f"{t}:{v}", "tipo": t, "tipo_label": CLUSTER_LABEL.get(t, t), "valor": v,
            "perfis_qtd": len(active), "confirmados": cnt.get("CONFIRMADO", 0) + cnt.get("JÁ ENCAMINHADO", 0),
            "revisar": cnt.get("REVISAR", 0) + cnt.get("NOVO", 0), "descartados": cnt.get("DESCARTADO", 0),
            "perfis": [{"id": m["id"], "username": m["username"], "status": m["status"], "score": m["score"],
                        "aff": sorted(m.get("aff", []))} for m in sorted(active, key=lambda x: -x["score"])],
            "affiliate_ids": sorted(g["aff"]), "links": sorted(g["links"])[:15],
        })
    out.sort(key=lambda c: (-c["perfis_qtd"], c["tipo"], c["valor"]))
    return out


def mission(conn):
    s = db.get_settings(conn)
    one = lambda q: conn.execute(q).fetchone()[0]
    conf = one("SELECT COUNT(*) FROM candidates WHERE status IN ('CONFIRMADO','JÁ ENCAMINHADO')")
    rev = one("SELECT COUNT(*) FROM candidates WHERE status IN ('NOVO','REVISAR') AND evidence_count>0 "
              "AND classification NOT LIKE 'BAIXA%'")
    decided_disc = one("SELECT COUNT(*) FROM candidates WHERE status='DESCARTADO' AND status_manual=1")
    rate = conf / (conf + decided_disc) if (conf + decided_disc) >= 5 else None
    goal = s["goal"]
    rest = max(goal - conf, 0)
    need_more = None
    if rest:
        if rate:
            need_more = max(int(round(rest / rate)) - rev, 0)
        else:
            need_more = max(rest - rev, 0)
    return {"meta": goal, "confirmados": conf, "restantes": rest, "em_revisao": rev,
            "total_coletado": one("SELECT COUNT(*) FROM candidates"), "pendentes": one("SELECT COUNT(*) FROM candidates WHERE evidence_count=0"),
            "taxa_confirmacao": rate, "candidatos_adicionais_estimados": need_more,
            "progresso": min(conf / goal, 1) if goal else 0}


def metrics(conn):
    one = lambda q: conn.execute(q).fetchone()[0]
    conf = one("SELECT COUNT(*) FROM candidates WHERE status IN ('CONFIRMADO','JÁ ENCAMINHADO')")
    disc = one("SELECT COUNT(*) FROM candidates WHERE status='DESCARTADO' AND status_manual=1")
    dec = conf + disc
    def top(col, n=10):
        c = Counter()
        for (v,) in conn.execute(f"SELECT {col} FROM candidates WHERE {col}!='[]' AND status!='DESCARTADO'"):
            c.update(jl(v))
        return [{"valor": k, "qtd": v} for k, v in c.most_common(n)]
    def by(group_col, n=10):
        q = (f"SELECT d.{group_col} k, COUNT(DISTINCT d.candidate_id) total, "
             "SUM(CASE WHEN c.status IN ('CONFIRMADO','JÁ ENCAMINHADO') THEN 1 ELSE 0 END) conf, "
             "SUM(CASE WHEN c.status='DESCARTADO' THEN 1 ELSE 0 END) disc "
             f"FROM discoveries d JOIN candidates c ON c.id=d.candidate_id WHERE d.{group_col}!='' "
             "GROUP BY d.%s ORDER BY conf DESC, total DESC LIMIT ?" % group_col)
        return [{"valor": r["k"], "total": r["total"], "confirmados": r["conf"], "descartados": r["disc"]}
                for r in conn.execute(q, (n,))]
    return {
        "taxa_falso_positivo": disc / dec if dec else None, "taxa_confirmacao": conf / dec if dec else None,
        "decididos": dec, "confirmados": conf, "descartados_manual": disc,
        "melhores_consultas": by("query"), "fontes": by("source"),
        "melhores_hashtags": top("hashtags"), "dominios": top("domains"), "plataformas": top("platforms"),
        "jogos": top("games"),
        "aviso": "Métricas operacionais de apoio ao trabalho de triagem; não constituem conclusão jurídica.",
    }


def search_log(conn, limit=200):
    return [dict(r) for r in conn.execute("SELECT * FROM search_log ORDER BY id DESC LIMIT ?", (limit,))]
