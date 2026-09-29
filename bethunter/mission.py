"""MISSÃO AUTOMÁTICA: matriz de consultas + descoberta iterativa com profundidade limitada.
Para quando: meta atingida, pool qualificado suficiente, consultas esgotadas, fontes indisponíveis ou usuário interrompe."""
import re
import time
from collections import deque
from datetime import datetime, timedelta

from . import db, extract, pipeline, queries, sources, urltools, visual
from .db import jl
from .util import base_domain, find_terms, norm, now_iso

BARE = {"a", "e", "o", "de"}


# ------------------------------------------------------------------ consultas

def sanitize_query(q, settings):
    """Termo genérico sozinho (bet, aposta, cassino, mines…) nunca vira consulta principal: combina com indicador promocional."""
    core = norm(q).strip().strip('"').strip()
    if core in {norm(g) for g in settings.get("generic_alone", [])}:
        return f'"{q.strip().strip(chr(34))}" "link na bio"'
    return q


def generate_queries(settings, limit=None):
    """Matriz A-D: pares configurados (padrão A+B, A+C, B+C, A+D, B+D), sem combinações absurdas nem duplicadas,
    intercalados para que as primeiras consultas já cubram todos os pares."""
    m = settings.get("matrix", {})
    lists = {g: [t.strip() for t in m.get(g, []) if t.strip()] for g in "ABCD"}
    per_pair = []
    for p in settings.get("matrix_pairs", []):
        if len(p) != 2 or p[0] == p[1] or p[0] not in lists or p[1] not in lists:
            continue
        combos = []
        for a in lists[p[0]]:
            for b in lists[p[1]]:
                na, nb = norm(a), norm(b)
                if na == nb or na in nb or nb in na:
                    continue
                combos.append((a, b))
        per_pair.append(combos)
    out, seen, i = [], set(), 0
    while any(i < len(c) for c in per_pair):
        for combos in per_pair:
            if i < len(combos):
                a, b = combos[i]
                key = frozenset((norm(a), norm(b)))
                if key not in seen:
                    seen.add(key)
                    out.append(f'"{a}" "{b}"')
        i += 1
    return out[:limit] if limit else out


def _bio_phrase(text, settings):
    for piece in re.split(r"[.!?\n|•👇⬇]+", text or ""):
        piece = piece.strip().strip('"“”')
        n = len(piece.split())
        if 3 <= n <= 9:
            t = norm(piece)
            if find_terms(t, extract.lexicon(settings, "payment") + extract.lexicon(settings, "bonus")) or \
               (find_terms(t, extract.lexicon(settings, "cta")) and extract.bet_context(extract.extract_all(piece, settings))):
                return piece
    return None


def derive_queries(row, settings):
    """Indicadores já coletados de um candidato -> novas consultas [(tipo, valor, consulta)]. Domínio primeiro."""
    ignore = set(settings.get("ignore_domains", []))
    out = []
    aff = jl(row["affiliate_ids"])
    for d in jl(row["domains"]):
        bd = base_domain(d)
        if urltools.domain_kind(d, settings) != "other" or bd in ignore:
            continue
        out.append(("domínio", bd, f'"{bd}"'))
        if d != bd:
            out.append(("domínio", d, f'"{d}"'))
        label = bd.split(".")[0]
        if len(label) >= 4:
            out.append(("plataforma", label, f'"{label}" link na bio'))
            for a in aff:
                out.append(("código", a, f'"{a.split("=", 1)[-1]}" "{label}"'))
    seen = {q for _, _, q in out}
    for t in pipeline.indicator_queries(row, settings):
        if t[2] not in seen:
            out.append(t)
            seen.add(t[2])
    ph = _bio_phrase(row["bio"], settings)
    if ph:
        out.append(("texto", ph, f'"{ph}"'))
    return out[: settings.get("derived_per_candidate", 6)]


# ------------------------------------------------------------------ missão

def snapshot(conn, goal):
    st = queries.stats(conn)
    ms = queries.mission(conn)
    pool = ms["em_revisao"] + ms["confirmados"]
    return {"brutos": st["brutos"], "unicos": st["unicos"], "alta": st["alta"], "em_revisao": st["em_revisao"],
            "confirmados": ms["confirmados"], "meta": goal, "descartados": st["descartados"],
            "clusters": st["clusters"], "pool": pool, "baixa": st["baixa"]}


def _initial_tasks(conn, s):
    base, seen = [], set()

    def add(q, origin):
        q = sanitize_query(q, s)
        k = norm(q)
        if k not in seen:
            seen.add(k)
            base.append((q, origin))
    matrix_q = []
    for h in conn.execute("SELECT * FROM hunts WHERE enabled=1 ORDER BY position").fetchall():
        if h["kind"] == "expand_confirmed":
            continue
        if h["kind"] == "matrix":
            matrix_q = generate_queries(s)
            continue
        for q, origin in pipeline._all_hunt_queries(conn, h, s):
            add(q, origin)
    for q in matrix_q:
        add(q, None)
    return base, seen


def run_mission(job, goal, depth, mode, sources_list):
    depth = max(0, min(3, int(depth)))
    complete = mode == "completo"
    with db.connect() as c:
        s = db.get_settings(c)
        base, seen = _initial_tasks(c, s)
        cutoff = (datetime.now().astimezone() - timedelta(days=s.get("cache_days", 7))).isoformat(timespec="seconds")
        recent = {(r["query"], r["source"]) for r in c.execute(
            "SELECT query, source FROM search_log WHERE ts>=? AND errors=0 AND hunt NOT LIKE 'visual'", (cutoff,))}
        seeds = c.execute("SELECT * FROM candidates WHERE status='CONFIRMADO' ORDER BY score DESC LIMIT 60").fetchall() if depth >= 1 else []
    eff = dict(s) if complete else dict(s, resolve_links=False, enrich_after_search=False)
    base, derived = deque(base), deque()
    derived_count = 0
    derived_from = set()

    def push_derived(row, dp):
        nonlocal derived_count
        for _, _, dq in derive_queries(row, s):
            dq = sanitize_query(dq, s)
            k = norm(dq)
            if k in seen or derived_count >= s.get("derived_max", 400):
                continue
            seen.add(k)
            derived.append((dq, dp, "expansão automática"))
            derived_count += 1
    for r in seeds:  # perfis já confirmados são as melhores sementes
        derived_from.add(r["id"])
        push_derived(r, 1)

    active = [x for x in sources_list if x in ("ddg", "bing", "tiktok", "tiktok_tag")] or ["ddg", "bing", "tiktok"]
    paused, fails = [], {x: 0 for x in active}
    done = enriched = 0
    reason = "consultas esgotadas"
    while True:
        with db.connect() as c:
            snap = snapshot(c, goal)
        total_q = done + len(base) + len(derived)
        job.stats = {**snap, "consultas_feitas": done, "consultas_total": total_q, "fontes_ativas": active,
                     "fontes_pausadas": paused, "modo": mode, "profundidade": depth, "consulta_atual": job.stats.get("consulta_atual", "")}
        if job.cancelled:
            reason = "interrompida pelo usuário"; break
        if snap["confirmados"] >= goal:
            reason = "meta de confirmados atingida"; break
        if snap["pool"] >= goal * s.get("pool_factor", 2.5):
            reason = f"pool qualificado suficiente ({snap['pool']} candidatos p/ revisão+confirmados)"; break
        if not active:
            reason = "todas as fontes automáticas indisponíveis (use IMPORTAR LISTA / evidência manual)"; break
        if derived:
            q, dp, origin = derived.popleft()
        elif base:
            q, origin = base.popleft(); dp = 0
        else:
            break
        job.stats["consulta_atual"] = q
        touched, new_ids = set(), []
        for src in list(active):
            if (q, sources.SOURCE_LABELS.get(src, src)) in recent:
                continue
            st = pipeline.run_query(q, src, eff, hunt="missão", origin=origin)
            touched |= st["ids"]; new_ids += st["new_ids"]
            if st.get("error") and st["found"] == 0:
                fails[src] += 1
                if fails[src] >= s.get("source_fail_limit", 3):
                    active.remove(src); paused.append(src)
                    job.log(f"⚠ fonte {sources.SOURCE_LABELS.get(src, src)} pausada nesta missão ({st['error'][:90]})")
            else:
                fails[src] = 0
            time.sleep(s.get("request_delay", 0))
        done += 1
        with db.connect() as c:
            rows = [c.execute("SELECT * FROM candidates WHERE id=?", (i,)).fetchone() for i in touched]
        thr = s["thresholds"]["revisar"]
        promising = [r for r in rows if r and r["evidence_count"] and r["status"] not in pipeline.INACTIVE + ("CONFIRMADO",)
                     and (r["score"] >= thr or jl(r["flags"], {}).get("link_bet"))]
        if complete:  # etapa 3: só candidatos promissores recebem processamento extra
            for r in promising:
                if job.cancelled:
                    break
                pipeline.deepen_links(r["id"], s)
                if r["profile_status"] == "NAO_COLETADO" and enriched < s.get("enrich_max", 40):
                    enriched += 1
                    pipeline.investigate(r["username"], force=True, source=None)
                else:
                    visual.run_visual(r["id"], s)
        if dp < depth:
            with db.connect() as c:
                for r in promising:
                    if r["id"] not in derived_from:
                        derived_from.add(r["id"])
                        row = c.execute("SELECT * FROM candidates WHERE id=?", (r["id"],)).fetchone()
                        push_derived(row, dp + 1)
        job.progress(done, done + len(base) + len(derived),
                     f"[{done}] {q} → {len(touched)} perfis ({len(new_ids)} novos)")
    with db.connect() as c:
        snap = snapshot(c, goal)
        pipeline.log_search(c, "missão", f"fim: {reason}", "missão", 0, 0, 0, 0, "", time.time())
    job.stats = {**job.stats, **snap, "motivo_fim": reason}
    return {"ok": True, "motivo": reason, "consultas": done, **snap, "fontes_pausadas": paused}
