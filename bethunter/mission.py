"""MISSÃO AUTOMÁTICA: matriz de consultas + descoberta iterativa com profundidade limitada.
Para quando: meta atingida, pool qualificado suficiente, consultas esgotadas, fontes indisponíveis ou usuário interrompe."""
import inspect
import logging
import os
import random
import re
import time
from collections import deque
from datetime import datetime, timedelta

from . import commercial, db, extract, pipeline, queries, sources, tiktok_local, urltools, visual
from .db import jd, jl
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
    """Fila base: (consulta, origem, só_nestas_fontes|None, tier). tier 0 = consultas prioritárias (fonte principal primeiro),
    1 = caças, 2 = matriz (só na fonte principal enquanto ela estiver saudável)."""
    base, seen = [], set()

    def add(q, origin, only=None, tier=1):
        if only and norm(q).strip('"') in commercial.NOISE_TERMS:
            return                       # bet/bets/aposta/apostas nunca como termo principal isolado
        k = ("C|" if only else "") + norm(q)
        if k not in seen:
            seen.add(k)
            base.append((q, origin, only, tier))
    for q in s.get("priority_queries", []):
        add(q, None, tier=0)
    matrix_q = []
    for h in conn.execute("SELECT * FROM hunts WHERE enabled=1 ORDER BY position").fetchall():
        if h["kind"] == "expand_confirmed":
            continue
        if h["kind"] == "matrix":
            matrix_q = generate_queries(s)
            continue
        if h["kind"] == "commercial":    # só na fonte oficial; termos editáveis em CONFIG
            for q, origin in pipeline._all_hunt_queries(conn, h, s):
                add(q, origin, only=("commercial",))
            continue
        for q, origin in pipeline._all_hunt_queries(conn, h, s):
            add(q, origin)
    for q in matrix_q:
        add(q, None, tier=2)
    return base, seen


ENV_NAME = {"ddg": "DUCKDUCKGO", "bing": "BING", "tiktok": "TIKTOK", "tiktok_tag": "TIKTOK",
            "commercial": "TIKTOK COMMERCIAL API", "tiktok_local": "TIKTOK SEARCH LOCAL"}
log = logging.getLogger("bethunter")

# ======================================================================================================
# ESTADOS INDEPENDENTES:  resultado ≠ consulta ≠ fonte ≠ missão.
#   falha de RESULTADO (zero) ≠ falha de QUERY;  falha de QUERY ≠ falha de FONTE;  falha de FONTE ≠ falha de MISSÃO.
# ======================================================================================================
Q_PENDING, Q_RUNNING, Q_SUCCESS, Q_EMPTY, Q_RETRY, Q_FAILED, Q_RATE, Q_BLOCKED = (
    "PENDING", "RUNNING", "SUCCESS", "EMPTY", "RETRY", "FAILED", "RATE_LIMITED", "BLOCKED")
Q_OPEN = (Q_PENDING, Q_RUNNING, Q_RETRY)
M_PENDING, M_RUNNING, M_DEGRADED, M_COMPLETED, M_TARGET, M_EXHAUSTED, M_CANCELLED, M_FAILED = (
    "PENDING", "RUNNING", "DEGRADED", "COMPLETED", "TARGET_REACHED", "EXHAUSTED", "CANCELLED", "FAILED")
M_OPEN = (M_PENDING, M_RUNNING, M_DEGRADED)
STOP_TARGET, STOP_EXHAUSTED, STOP_CANCEL = "TARGET_REACHED", "QUERY_QUEUE_EXHAUSTED", "USER_CANCELLED"
STOP_TIMEOUT, STOP_LIMIT, STOP_NOSRC, STOP_FATAL = "GLOBAL_TIMEOUT", "GLOBAL_LIMIT", "ALL_SOURCES_UNAVAILABLE", "FATAL_INTERNAL_ERROR"
STOP_STATUS = {STOP_TARGET: M_TARGET, STOP_EXHAUSTED: M_COMPLETED, STOP_CANCEL: M_CANCELLED, STOP_TIMEOUT: M_COMPLETED,
               STOP_LIMIT: M_COMPLETED, STOP_NOSRC: M_EXHAUSTED, STOP_FATAL: M_FAILED}
STOP_TEXT = {STOP_TARGET: "meta de confirmados atingida", STOP_EXHAUSTED: "consultas esgotadas",
             STOP_CANCEL: "interrompida pelo usuário", STOP_TIMEOUT: "limite global de tempo atingido",
             STOP_LIMIT: "limite global de candidatos atingido",
             STOP_NOSRC: "todas as fontes automáticas indisponíveis (use IMPORTAR LISTA / evidência manual)",
             STOP_FATAL: "erro interno fatal (detalhes em logs/bethunter.log)"}


def classify_outcome(st):
    """Resultado de UMA tentativa (consulta+fonte) -> success | empty | rate_limited | blocked | unavailable | cancelled | transient.
    Zero resultados sem erro é 'empty' (consulta válida). Só erro real vira falha."""
    err, found = st.get("error"), st.get("found", 0)
    if not err:
        return "success" if found else "empty"
    if found:
        return "success"                  # resultado parcial: o erro fica no log, os dados são aproveitados
    e = err.lower()
    if "interrompida pelo usuário" in e:
        return "cancelled"
    if "429" in e or "rate limit" in e or "rate_limited" in e:
        return "rate_limited"
    if "não configurad" in e or "nao configurad" in e or "autenticação pendente" in e or "credenciais encontradas" in e:
        return "unavailable"
    if any(k in e for k in ("401", "403", "captcha", "challenge", "bloqueado", "bloqueio", "anti-bot",
                            "autenticação falhou", "sem permissão")):
        return "blocked"
    return "transient"                    # timeout, conexão, 5xx/502/503, resposta inválida, exceção


class SourceState:
    """ACTIVE · DEGRADED · PAUSED (temporária, com retomada automática) · UNAVAILABLE (definitiva). Nunca encerra a missão."""

    def __init__(self, key, s):
        self.key, self.s = key, s
        self.state, self.fail_streak, self.pause_count = "ACTIVE", 0, 0
        self.paused_until, self.reason = 0.0, ""

    def usable(self, now):
        if self.state == "UNAVAILABLE":
            return False
        if self.state == "PAUSED":
            if now < self.paused_until:
                return False
            self.state = "DEGRADED"       # meia-abertura: a próxima tentativa decide
        return True

    def ok(self):
        self.state, self.fail_streak, self.pause_count, self.reason = "ACTIVE", 0, 0, ""

    def fail(self, kind, now):
        """Falha técnica real. -> True se a fonte foi pausada agora."""
        self.fail_streak += 1
        self.reason = kind
        if self.state == "ACTIVE":
            self.state = "DEGRADED"
        if self.fail_streak >= self.s.get("source_fail_limit", 3):
            return self.pause(now)
        return False

    def pause(self, now, cooldown=None):
        self.pause_count += 1
        if self.pause_count > self.s.get("source_max_pauses", 4):
            self.state = "UNAVAILABLE"    # esgotada de vez: só então conta para ALL_SOURCES_UNAVAILABLE
            return True
        base = cooldown if cooldown is not None else min(self.s.get("source_pause_seconds", 30) * 2 ** (self.pause_count - 1), 600)
        self.state, self.paused_until = "PAUSED", now + base
        return True

    def label(self):
        return {"ACTIVE": "ATIVA", "DEGRADED": "DEGRADADA", "PAUSED": "PAUSADA", "UNAVAILABLE": "INDISPONÍVEL"}[self.state] + \
            (f" — {self.reason}" if self.reason and self.state != "ACTIVE" else "")


# ------------------------------------------------------------------ persistência da missão (retomada sem recomeçar do zero)
STARTED_HERE = set()     # missões iniciadas neste processo (o watchdog só reinicia estas; órfãs de outro processo pedem RETOMAR)


def _mission_row(conn, mid):
    return conn.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()


def current(conn):
    """Última missão (para a interface/watchdog): dict com estado, contadores e se há worker vivo."""
    r = conn.execute("SELECT * FROM missions ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None
    alive = any(getattr(j, "mission_id", None) == r["id"] and j.status == "running" for j in pipeline.JOBS.values())
    open_ = r["status"] in M_OPEN
    return {"id": r["id"], "status": r["status"], "stop_reason": r["stop_reason"], "params": jl(r["params"], {}),
            "counters": jl(r["counters"], {}), "sources": jl(r["sources"], {}), "alive": alive,
            "orphan": open_ and not alive, "started_at": r["started_at"], "ended_at": r["ended_at"]}


class MissionRun:
    def __init__(self, job, mid, goal, depth, mode, s, eff, states, estados, deprior, clock, sleeper):
        self.job, self.mid, self.goal, self.depth, self.mode = job, mid, goal, depth, mode
        self.s, self.eff, self.states, self.estados, self.deprior = s, eff, states, estados, deprior
        self.clock, self.sleeper = clock, sleeper
        self.complete = mode == "completo"
        self.tasks, self.base, self.derived = {}, deque(), deque()
        self.seen, self.zero_streak, self.ever_paused = set(), {k: 0 for k in states}, []
        self.derived_count, self.derived_from, self.enriched = 0, set(), 0
        self.t0, self.stop_reason, self.next_wake, self.idle, self.guard_blocks = clock(), None, None, 0, 0
        self.current_q = ""
        self.recent = set()
        self.skip_recent = False

    # ---------- tarefas (fila explícita, persistida)
    def _row(self, r):
        return {"id": r["id"], "seq": r["seq"], "q": r["q"], "origin": r["origin"],
                "only": tuple(x for x in (r["only_src"] or "").split(",") if x) or None, "tier": r["tier"], "dp": r["dp"],
                "status": r["status"], "todo": jl(r["todo"], None) if r["todo"] else None, "done": jl(r["done"], {}),
                "attempts": jl(r["attempts"], {}), "wait": jl(r["wait"], {}), "raw": r["raw"], "new": r["new"], "note": r["note"] or ""}

    def load(self):
        """(Re)carrega a missão do banco. RUNNING órfã (worker caiu) volta para PENDING; nada finalizado é refeito."""
        with db.connect() as c:
            c.execute("UPDATE mission_tasks SET status='PENDING' WHERE mission_id=? AND status='RUNNING'", (self.mid,))
            rows = c.execute("SELECT * FROM mission_tasks WHERE mission_id=? ORDER BY seq", (self.mid,)).fetchall()
        for r in rows:
            t = self._row(r)
            self.tasks[t["id"]] = t
            self.seen.add(("C|" if t["only"] else "") + norm(t["q"]))
            if t["tier"] == 3:
                self.derived_count += 1
            if t["status"] in Q_OPEN:
                (self.derived if t["tier"] == 3 else self.base).append(t["id"])

    def add_task(self, q, origin, only, tier, dp=0):
        with db.connect() as c:
            seq = (c.execute("SELECT COALESCE(MAX(seq),0)+1 FROM mission_tasks WHERE mission_id=?", (self.mid,)).fetchone()[0])
            cur = c.execute("INSERT INTO mission_tasks(mission_id,seq,q,origin,only_src,tier,dp) VALUES(?,?,?,?,?,?,?)",
                            (self.mid, seq, q, origin or "", ",".join(only or ()), tier, dp))
        t = {"id": cur.lastrowid, "seq": seq, "q": q, "origin": origin, "only": tuple(only) if only else None, "tier": tier,
             "dp": dp, "status": Q_PENDING, "todo": None, "done": {}, "attempts": {}, "wait": {}, "raw": 0, "new": 0, "note": ""}
        self.tasks[t["id"]] = t
        (self.derived if tier == 3 else self.base).append(t["id"])
        return t

    def save(self, t):
        with db.connect() as c:
            c.execute("UPDATE mission_tasks SET status=?, todo=?, done=?, attempts=?, wait=?, raw=?, new=?, note=? WHERE id=?",
                      (t["status"], jd(t["todo"]) if t["todo"] is not None else None, jd(t["done"]), jd(t["attempts"]),
                       jd(t["wait"]), t["raw"], t["new"], t["note"], t["id"]))

    def counters(self):
        c = {Q_PENDING: 0, Q_RUNNING: 0, Q_RETRY: 0, Q_SUCCESS: 0, Q_EMPTY: 0, Q_FAILED: 0, Q_RATE: 0, Q_BLOCKED: 0}
        for t in self.tasks.values():
            c[t["status"]] += 1
        failed = c[Q_FAILED] + c[Q_RATE] + c[Q_BLOCKED]
        return {"total": len(self.tasks), "pending": c[Q_PENDING], "running": c[Q_RUNNING], "retry": c[Q_RETRY],
                "completed": c[Q_SUCCESS] + c[Q_EMPTY], "empty": c[Q_EMPTY], "failed": failed,
                "processed": c[Q_SUCCESS] + c[Q_EMPTY] + failed, "open": c[Q_PENDING] + c[Q_RUNNING] + c[Q_RETRY]}

    # ---------- fontes
    def usable_sources(self):
        return [k for k, st in self.states.items() if st.state != "UNAVAILABLE"]

    def pick(self, t):
        q, only, tier = t["q"], t["only"], t["tier"]
        if only:
            return [x for x in only if x in self.states and self.states[x].state != "UNAVAILABLE"]
        act = self.usable_sources()
        if q.startswith("@"):                                   # busca de usuário: só a fonte nativa
            return ["tiktok_local"] if "tiktok_local" in act else []
        cands = [x for x in act if x != "commercial"]
        if tier == 2 and "tiktok_local" in cands and "tiktok_local" not in self.deprior:
            return ["tiktok_local"]                             # matriz: fonte principal antes das fracas
        if tier >= 2:
            return [x for x in cands if x not in self.deprior] or cands
        return cands

    def plan(self, t):
        t["todo"] = self.pick(t)
        if self.skip_recent:
            for x in t["todo"]:
                if (t["q"], sources.SOURCE_LABELS.get(x, x)) in self.recent:
                    t["done"][x] = "cached"
        self.save(t)

    def replan(self, t):
        """Todas as fontes planejadas ficaram indisponíveis sem resultado: tenta as fontes restantes UMA vez."""
        if t.get("replanned") or any(v in ("success", "empty", "cached") for v in t["done"].values()):
            return False
        t["replanned"] = True
        new = [x for x in self.pick(t) if x not in t["done"]]
        if not new:
            return False
        t["todo"] = (t["todo"] or []) + new
        self.save(t)
        return True

    def finalize(self, t):
        outs = set(t["done"].values())
        if outs & {"success", "cached"}:
            st = Q_SUCCESS
        elif "empty" in outs:
            st = Q_EMPTY
        elif outs == {"rate_limited"}:
            st = Q_RATE
        elif outs == {"blocked"}:
            st = Q_BLOCKED
        else:
            st = Q_FAILED
            if not t["done"]:
                t["note"] = "sem fonte disponível para esta consulta"
        t["status"] = st
        self.save(t)
        return st

    def next_task(self):
        """Próxima (consulta, fonte) executável agora. Nunca descarta consulta em silêncio: sem fonte -> FAILED registrada."""
        now, wake = self.clock(), None
        for dq in (self.derived, self.base):
            for tid in list(dq):
                t = self.tasks[tid]
                if t["status"] not in Q_OPEN:
                    dq.remove(tid)
                    continue
                if t["todo"] is None:
                    self.plan(t)
                for x in t["todo"]:
                    if x not in t["done"] and (x not in self.states or self.states[x].state == "UNAVAILABLE"):
                        t["done"][x] = "unavailable"
                rem = [x for x in t["todo"] if x not in t["done"]]
                if not rem:
                    if self.replan(t):
                        continue
                    self.finalize(t)
                    self._log_query(t, "-", t["status"], "NO_SOURCE" if not t["done"] else "CONTINUE", 0, 0)
                    dq.remove(tid)
                    continue
                for x in rem:
                    w = t["wait"].get(x, 0)
                    if w > now:
                        wake = w if wake is None else min(wake, w)
                        continue
                    st = self.states[x]
                    if not st.usable(now):
                        wake = st.paused_until if wake is None else min(wake, st.paused_until)
                        continue
                    return t, x
        self.next_wake = wake
        return None, None

    # ---------- execução de uma tentativa
    def _log_query(self, t, src, status, action, raw, new):
        c = self.counters()
        line = (f"QUERY {t['seq']}/{c['total']} source={src} status={status} raw_results={raw} new_candidates={new} "
                f"action={action} q={t['q']!r}")
        self.job.log(line)
        log.info("[missão %s] %s", self.mid, line)

    def attempt(self, t, src):
        st, label, now = self.states[src], sources.SOURCE_LABELS.get(src, src), self.clock()
        n = t["attempts"].get(src, 0) + 1
        t["attempts"][src], t["status"] = n, Q_RUNNING
        self.save(t)
        self.current_q = t["q"]
        try:
            r = pipeline.run_query(t["q"], src, self.eff, hunt="missão", origin=t["origin"])
        except Exception as exc:          # exceção real: registra o detalhe, marca a tentativa como falha e SEGUE
            log.exception("[missão %s] exceção em %s para %r", self.mid, src, t["q"])
            r = {"found": 0, "new": 0, "dups": 0, "new_ids": [], "ids": set(), "error": f"exceção interna ({type(exc).__name__})"}
        kind = classify_outcome(r)
        raw, new = r.get("found", 0), r.get("new", 0)
        status_txt, action, touched = None, "CONTINUE", set()
        if kind in ("success", "empty"):
            st.ok()
            t["done"][src] = kind
            t["raw"] += raw
            t["new"] += new
            touched = r["ids"]
            if kind == "empty":
                self.zero_streak[src] += 1
                status_txt = "EMPTY"
                if self.zero_streak[src] >= self.s.get("zero_deprioritize", 10) and src not in self.deprior and len(self.states) > 1:
                    self.deprior.add(src)
                    self.job.log(f"{label}: {self.zero_streak[src]} consultas válidas sem resultados — prioridade reduzida (não é erro)")
                    action = "SOURCE_PRIORITY_LOWERED"
            else:
                self.zero_streak[src] = 0
                status_txt = "SUCCESS"
        elif kind == "cancelled":
            t["attempts"][src] = n - 1
            t["status"] = Q_PENDING
            self.save(t)
            return
        elif kind == "rate_limited":
            status_txt = "RATE_LIMITED"
            ra = sources.RETRY_AFTER.pop(label, None) or 0
            cds = self.s.get("rate_limit_cooldowns", [30, 60, 120, 300])
            cd = max(ra, cds[min(st.pause_count, len(cds) - 1)])
            st.fail_streak += 1
            st.reason = "rate limit"
            st.pause(now, cd)
            self._note_pause(src, label, f"rate limit (retoma em {cd:.0f}s)")
            if n >= self.s.get("query_max_attempts", 3) + 3:
                t["done"][src] = "rate_limited"
                action = "SOURCE_PAUSED,QUERY_GIVEN_UP"
            else:
                t["wait"][src] = st.paused_until
                action = "SOURCE_PAUSED,REQUEUED"
        elif kind == "blocked":
            status_txt = "BLOCKED"
            paused = st.fail("bloqueio", now)
            t["done"][src] = "blocked"
            action = "SOURCE_PAUSED" if paused else "SOURCE_DEGRADED"
            if paused:
                self._note_pause(src, label, f"bloqueio/permissão ({(r.get('error') or '')[:70]})")
        elif kind == "unavailable":
            status_txt = "FAILED"
            st.state, st.reason = "UNAVAILABLE", "não configurada"
            t["done"][src] = "unavailable"
            action = "SOURCE_UNAVAILABLE"
        else:                              # transient: timeout, conexão, 5xx, resposta inválida, exceção
            paused = st.fail("falha técnica", now)
            delays = self.s.get("query_retry_delays", [2, 5, 15])
            if n < self.s.get("query_max_attempts", 3):
                d = delays[min(n - 1, len(delays) - 1)] * random.uniform(0.8, 1.2)
                t["wait"][src] = now + d
                status_txt, action = "RETRY", f"RETRY_IN_{d:.0f}s"
            else:
                t["done"][src] = "failed"
                status_txt, action = "FAILED", "QUERY_FAILED,CONTINUE"
            if paused:
                action += ",SOURCE_PAUSED"
                self._note_pause(src, label, f"falhas técnicas seguidas ({(r.get('error') or '')[:70]})")
        # fecha a tarefa quando todas as fontes planejadas terminaram; senão ela volta para a fila
        rem = [x for x in (t["todo"] or []) if x not in t["done"]]
        if not rem and not self.replan(t):
            self.finalize(t)
        else:
            t["status"] = Q_RETRY if any(t["wait"].get(x, 0) > now for x in rem) else Q_PENDING
            self.save(t)
        self._log_query(t, src, status_txt, action, raw, new)
        if kind in ("success", "empty"):
            self._after_success(touched, t["dp"])
        self.sleeper(self.s.get("request_delay", 0))

    def _note_pause(self, src, label, why):
        if src not in self.ever_paused:
            self.ever_paused.append(src)
        self.job.log(f"⚠ fonte {label} pausada nesta missão — {why}; as consultas seguem na fila com as demais fontes")

    def _after_success(self, touched, dp):
        thr = self.s["thresholds"]["revisar"]
        with db.connect() as c:
            rows = [c.execute("SELECT * FROM candidates WHERE id=?", (i,)).fetchone() for i in touched]
        promising = [r for r in rows if r and r["evidence_count"] and r["status"] not in pipeline.INACTIVE + ("CONFIRMADO",)
                     and (r["score"] >= thr or jl(r["flags"], {}).get("link_bet"))]
        if self.complete:  # etapa 3: só candidatos promissores recebem processamento extra
            for r in promising:
                if self.job.cancelled:
                    break
                try:
                    pipeline.deepen_links(r["id"], self.s)
                    if r["profile_status"] == "NAO_COLETADO" and self.enriched < self.s.get("enrich_max", 40):
                        self.enriched += 1
                        pipeline.investigate(r["username"], force=True, source=None)
                    else:
                        visual.run_visual(r["id"], self.s)
                except Exception:
                    log.exception("[missão %s] processamento extra falhou para @%s", self.mid, r["username"])
        if dp < self.depth:   # hashtags/domínios/códigos dos promissores viram novas consultas (até a profundidade)
            with db.connect() as c:
                for r in promising:
                    if r["id"] not in self.derived_from:
                        self.derived_from.add(r["id"])
                        row = c.execute("SELECT * FROM candidates WHERE id=?", (r["id"],)).fetchone()
                        self.push_derived(row, dp + 1)

    def push_derived(self, row, dp):
        for _, _, dq in derive_queries(row, self.s):
            k = norm(dq)
            if k in self.seen or self.derived_count >= self.s.get("derived_max", 400):
                continue
            self.seen.add(k)
            self.add_task(dq, "expansão automática", None, 3, dp)
            self.derived_count += 1

    # ---------- painel e persistência do estado
    def publish(self):
        with db.connect() as c:
            snap = snapshot(c, self.goal)
        cnt = self.counters()
        now = self.clock()
        degraded = any(st.state != "ACTIVE" for st in self.states.values())
        mstatus = self.final_status or (M_DEGRADED if degraded else M_RUNNING)
        situ = {sources.SOURCE_LABELS.get(k, k): st.label() for k, st in self.states.items()}
        usable = [k for k, st in self.states.items() if st.usable(now)]
        self.job.stats = {**snap, "mission_id": self.mid, "mission_status": mstatus, "stop_reason": self.stop_reason or "",
                          "queries_total": cnt["total"], "queries_pending": cnt["pending"], "queries_running": cnt["running"],
                          "queries_completed": cnt["completed"], "queries_empty": cnt["empty"], "queries_failed": cnt["failed"],
                          "queries_retry": cnt["retry"], "queries_processed": cnt["processed"],
                          "consultas_feitas": cnt["processed"], "consultas_total": cnt["total"], "consulta_atual": self.current_q,
                          "fontes_ativas": usable, "fontes_situacao": situ, "fontes_estado": self.estados,
                          "fontes_pausadas": [k for k, st in self.states.items() if st.state in ("PAUSED", "UNAVAILABLE")],
                          "fontes_reduzidas": sorted(self.deprior), "modo": self.mode, "profundidade": self.depth,
                          "motivo_fim": self.stop_reason or ""}
        self.job.progress(cnt["processed"], cnt["total"])
        with db.connect() as c:
            c.execute("UPDATE missions SET status=?, counters=?, sources=?, updated_at=? WHERE id=?",
                      (mstatus, jd({**cnt, "brutos": snap["brutos"], "unicos": snap["unicos"], "confirmados": snap["confirmados"]}),
                       jd(situ), now_iso(), self.mid))
        return snap, cnt

    final_status = None

    # ---------- condição GLOBAL ÚNICA de encerramento
    def decide(self, snap, cnt):
        """Única fonte de verdade sobre o fim da missão. Falha isolada (resultado/consulta/fonte) nunca chega aqui."""
        if self.job.cancelled:
            return STOP_CANCEL
        lim = self.s.get("mission_max_minutes", 0)
        if lim and (self.clock() - self.t0) >= lim * 60:
            return STOP_TIMEOUT
        if snap["confirmados"] >= self.goal:
            return STOP_TARGET
        pl = self.s.get("mission_pool_limit", 0)
        if pl and snap["pool"] >= pl:
            return STOP_LIMIT
        if cnt["open"] == 0:
            return STOP_EXHAUSTED
        if not self.usable_sources():
            return STOP_NOSRC
        return None

    def valid_stop(self, reason, snap, cnt):
        if reason == STOP_CANCEL:
            return bool(self.job.cancelled)
        if reason == STOP_TARGET:
            return snap["confirmados"] >= self.goal
        if reason == STOP_EXHAUSTED:
            return cnt["pending"] == 0 and cnt["running"] == 0 and cnt["retry"] == 0
        if reason == STOP_NOSRC:
            return not self.usable_sources()
        if reason == STOP_TIMEOUT:
            return bool(self.s.get("mission_max_minutes")) and (self.clock() - self.t0) >= self.s["mission_max_minutes"] * 60
        if reason == STOP_LIMIT:
            return bool(self.s.get("mission_pool_limit")) and snap["pool"] >= self.s["mission_pool_limit"]
        return reason == STOP_FATAL

    def finish(self, reason, snap=None, cnt=None):
        """ÚNICO ponto que encerra a missão. Registra a origem da chamada e recusa encerramento inválido."""
        cf = inspect.stack()[1]
        if snap is None:
            with db.connect() as c:
                snap = snapshot(c, self.goal)
        cnt = cnt or self.counters()
        ok = self.valid_stop(reason, snap, cnt)
        msg = (f"MISSION COMPLETION CALLED FROM: {os.path.basename(cf.filename)} {cf.function} line={cf.lineno} "
               f"stop_reason={reason} queries_pending={cnt['pending']} queries_processed={cnt['processed']} "
               f"target={self.goal} confirmed={snap['confirmados']} valid={ok}")
        log.info("[missão %s] %s", self.mid, msg)
        self.job.log(msg)
        if not ok:
            log.error("[missão %s] ENCERRAMENTO BLOQUEADO: %s não é condição válida (pending=%s running=%s retry=%s)", self.mid,
                      reason, cnt["pending"], cnt["running"], cnt["retry"])
            return False
        self.stop_reason = reason
        self.final_status = STOP_STATUS[reason]
        with db.connect() as c:
            c.execute("UPDATE missions SET status=?, stop_reason=?, ended_at=?, updated_at=? WHERE id=?",
                      (self.final_status, reason, now_iso(), now_iso(), self.mid))
            pipeline.log_search(c, "missão", f"MISSION_STOP_REASON={reason}", "missão", 0, 0, 0, 0, "", time.time())
        log.info("[missão %s] MISSION_STOP_REASON=%s status=%s", self.mid, reason, self.final_status)
        self.job.log(f"MISSION_STOP_REASON={reason}")
        return True

    def run(self):
        while True:
            snap, cnt = self.publish()
            reason = self.decide(snap, cnt)
            if reason:
                if self.finish(reason, snap, cnt):
                    break
                self.guard_blocks += 1                      # nunca deveria ocorrer: decide() e valid_stop() usam os mesmos critérios
                if self.guard_blocks > 3:
                    self.finish(STOP_FATAL, snap, cnt)
                    break
                continue
            t, src = self.next_task()
            if t is None:
                self.idle += 1
                if self.idle > 200 and self.next_wake is None:   # nada executável e nenhuma espera pendente: destrava
                    log.error("[missão %s] fila sem tarefa executável e sem espera; liberando esperas", self.mid)
                    for x in self.tasks.values():
                        x["wait"] = {}
                    for st in self.states.values():
                        if st.state == "PAUSED":
                            st.paused_until = 0
                    self.idle = 0
                d = max(0.01, min(1.0, (self.next_wake or self.clock() + 1) - self.clock()))
                self.sleeper(d)                              # aguarda cooldown/backoff (a fila permanece intacta)
                continue
            self.idle = 0
            self.attempt(t, src)
        snap, cnt = self.publish()
        return self.result(snap, cnt)

    def result(self, snap, cnt):
        return {"ok": True, "mission_id": self.mid, "status": self.final_status, "stop_reason": self.stop_reason,
                "motivo": STOP_TEXT.get(self.stop_reason, self.stop_reason), "consultas": cnt["processed"],
                "queries": cnt, **snap, "fontes_pausadas": list(self.ever_paused), "fontes_reduzidas": sorted(self.deprior),
                "fontes_indisponiveis": [k for k, st in self.states.items() if st.state == "UNAVAILABLE"]}


# ------------------------------------------------------------------ montagem da missão (nova ou retomada)
def _setup_sources(job, s, sources_list, precheck):
    order = sources.by_priority([x for x in sources_list if x in sources.SOURCE_LABELS]) or ["bing", "ddg", "tiktok"]
    net_states = {}
    if precheck:
        from . import envcheck
        net_states = envcheck.test_all()
    estados, states, deprior = {}, {}, set()
    for src in order:
        label = sources.SOURCE_LABELS.get(src, src)
        if src == "tiktok_local":
            est = net_states.get("TIKTOK SEARCH LOCAL") or tiktok_local.check(s)
            estados[label] = est["estado"]
            if est["estado"] == tiktok_local.OK:
                states[src] = SourceState(src, s)
            else:
                job.log(f"TikTok Search Local {est['estado']}: missão segue com as demais fontes")
        elif src == "commercial":
            ok = commercial.usable(s) and (not precheck or (net_states.get("TIKTOK COMMERCIAL API") or {}).get("estado") == "OK")
            estados[label] = (net_states.get("TIKTOK COMMERCIAL API") or {}).get("estado") or ("OK" if ok else "NÃO CONFIGURADA")
            if ok:
                states[src] = SourceState(src, s)
            else:
                job.log("TikTok Commercial Content API indisponível/NÃO CONFIGURADA: fonte ignorada nesta missão")
        else:
            states[src] = SourceState(src, s)
            if precheck:
                est = (net_states.get(ENV_NAME[src]) or {}).get("estado", "OK")
                estados[label] = est
                if est != "OK":
                    deprior.add(src)         # não-OK: executa por último/só nas consultas prioritárias
    for k, v in estados.items():
        job.log(f"{k}: {v}")
    return states, estados, deprior


def run_mission(job, goal, depth, mode, sources_list, precheck=False, mission_id=None, clock=None, sleeper=None):
    """Executa (ou RETOMA, com mission_id) uma missão. Termina somente por condição global válida (ver MissionRun.decide)."""
    clock, sleeper = clock or time.time, sleeper or time.sleep
    with db.connect() as c:
        s = db.get_settings(c)
        if mission_id:
            m = _mission_row(c, mission_id)
            p = jl(m["params"], {})
            goal, depth, mode, sources_list = p.get("goal", goal), p.get("depth", depth), p.get("mode", mode), p.get("sources", sources_list)
    depth = max(0, min(3, int(depth)))
    eff = dict(s) if mode == "completo" else dict(s, resolve_links=False, enrich_after_search=False)
    states, estados, deprior = _setup_sources(job, s, sources_list, precheck)
    run = MissionRun(job, mission_id, goal, depth, mode, s, eff, states, estados, deprior, clock, sleeper)
    if mission_id:
        job.mission_id = mission_id
        STARTED_HERE.add(mission_id)
        run.load()
        job.log(f"missão #{mission_id} RETOMADA: {run.counters()['open']} consultas abertas de {run.counters()['total']}")
    else:
        with db.connect() as c:
            base, seen = _initial_tasks(c, s)
            seeds = c.execute("SELECT * FROM candidates WHERE status='CONFIRMADO' ORDER BY score DESC LIMIT 60").fetchall() if depth >= 1 else []
            cur = c.execute("INSERT INTO missions(params,status,started_at,updated_at) VALUES(?,?,?,?)",
                            (jd({"goal": goal, "depth": depth, "mode": mode, "sources": list(sources_list)}), M_RUNNING, now_iso(), now_iso()))
            run.mid = mission_id = cur.lastrowid
            for i, (q, origin, only, tier) in enumerate(base, 1):
                c.execute("INSERT INTO mission_tasks(mission_id,seq,q,origin,only_src,tier,dp) VALUES(?,?,?,?,?,?,0)",
                          (run.mid, i, q, origin or "", ",".join(only or ()), tier))
        job.mission_id = run.mid
        STARTED_HERE.add(run.mid)
        run.load()
        for r in seeds:  # perfis já confirmados são as melhores sementes
            run.derived_from.add(r["id"])
            run.push_derived(r, 1)
    hours = s.get("mission_skip_recent_hours", 0)
    if hours:
        cutoff = (datetime.now().astimezone() - timedelta(hours=hours)).isoformat(timespec="seconds")
        with db.connect() as c:
            run.recent = {(r["query"], r["source"]) for r in c.execute(
                "SELECT query, source FROM search_log WHERE ts>=? AND errors=0 AND hunt NOT LIKE 'visual'", (cutoff,))}
        run.skip_recent = True
    return run.run()


def run_supervised(job, goal, depth, mode, sources_list, precheck=False, mission_id=None, **kw):
    """Worker com WATCHDOG: se o worker morrer (exceção) com a missão ainda aberta (pending > 0, sem stop_reason),
    reinicia a MESMA missão retomando a fila — sem recomeçar do zero."""
    with db.connect() as c:
        max_r = db.get_settings(c).get("mission_watchdog_restarts", 3)
    restarts = 0
    while True:
        try:
            return run_mission(job, goal, depth, mode, sources_list, precheck=precheck and restarts == 0, mission_id=mission_id, **kw)
        except Exception:
            log.exception("worker da missão caiu")
            mission_id = getattr(job, "mission_id", None) or mission_id
            restarts += 1
            if job.cancelled:
                raise
            if mission_id is None or restarts > max_r:
                if mission_id:
                    with db.connect() as c:
                        c.execute("UPDATE missions SET status=?, stop_reason=?, ended_at=?, updated_at=? WHERE id=?",
                                  (M_FAILED, STOP_FATAL, now_iso(), now_iso(), mission_id))
                        pipeline.log_search(c, "missão", f"MISSION_STOP_REASON={STOP_FATAL}", "missão", 0, 0, 0, 0, "", time.time())
                log.error("MISSION_STOP_REASON=%s (watchdog esgotado)", STOP_FATAL)
                job.log(f"MISSION_STOP_REASON={STOP_FATAL}")
                job.stats = {**job.stats, "stop_reason": STOP_FATAL, "mission_status": M_FAILED, "motivo_fim": STOP_FATAL}
                return {"ok": False, "mission_id": mission_id, "status": M_FAILED, "stop_reason": STOP_FATAL,
                        "motivo": STOP_TEXT[STOP_FATAL], "consultas": 0}
            job.log(f"watchdog: worker reiniciado ({restarts}/{max_r}) — retomando a fila da missão #{mission_id}")
