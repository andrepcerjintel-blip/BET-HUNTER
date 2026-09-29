"""Regressão do bug crítico: a missão NÃO pode encerrar por zero resultados, falha/bloqueio/timeout/429 de consulta ou de fonte.
falha de RESULTADO ≠ falha de QUERY ≠ falha de FONTE ≠ falha de MISSÃO. Só condição global válida encerra."""
import time

import pytest

from bethunter import db, mission, pipeline, sources
from bethunter.web import create_app

_real_sleep = time.sleep


class Clock:
    """Relógio falso: esperas de backoff/cooldown avançam o tempo sem dormir de verdade."""
    def __init__(self):
        self.t = 1_000.0
    def now(self):
        return self.t
    def sleep(self, s):
        self.t += max(s, 0.0)


class Crash(BaseException):
    """Simula o worker morrendo no meio da missão (não é capturada por `except Exception`)."""


def hit(q, i=0, src="Bing"):
    u = f"res_{abs(hash(q)) % 100000}_{i}"
    return {"username": u, "profile_url": f"https://www.tiktok.com/@{u}", "video_url": f"https://www.tiktok.com/@{u}/video/{abs(hash(q)) % 10**8}{i}",
            "text": "Fortune Tiger cadastre-se pelo link na bio saque", "source": src, "query": q, "source_url": "x"}


def plan(n, **extra):
    """n consultas prioritárias independentes; sem matriz; sem caças; tempos realistas (relógio falso)."""
    with db.connect() as c:
        c.execute("UPDATE hunts SET enabled=0")
        db.save_settings(c, {"priority_queries": [f"consulta {i}" for i in range(1, n + 1)],
                             "matrix": {"A": [], "B": [], "C": [], "D": []}, "request_delay": 1.0, "enrich_after_search": False,
                             "query_retry_delays": [2, 5, 15], "rate_limit_cooldowns": [30, 60, 120, 300], "source_pause_seconds": 30,
                             **extra})


def num(q):
    return int(q.split()[-1])


def run(monkeypatch, fn, n, srcs=("bing",), goal=200, clock=None, mission_id=None, job=None, **kw):
    clock = clock or Clock()
    monkeypatch.setattr(sources, "run_source", fn)
    job = job or pipeline.Job("m")
    r = mission.run_mission(job, goal, 0, "rapido", list(srcs), clock=clock.now, sleeper=clock.sleep, mission_id=mission_id, **kw)
    return r, job, clock


def mission_row():
    with db.connect() as c:
        return c.execute("SELECT * FROM missions ORDER BY id DESC LIMIT 1").fetchone()


# ---------------------------------------------------------------- 16. teste crítico: zero, 403, timeout, resultados
def test_zero_403_timeout_results_mission_keeps_running_until_real_end(env, monkeypatch):
    plan(323, query_retry_delays=[50, 50, 50])          # a retentativa da consulta 3 só ocorre bem depois (ela fica em RETRY)
    seen, snap = [], {}
    job = pipeline.Job("m")
    state = {"timeouts": 0}

    def fn(src, q):
        k = num(q)
        seen.append(k)
        if len(seen) == 5:                                        # foto da missão no início da 5ª chamada (após as 4 primeiras consultas)
            m = mission_row()
            snap.update(status=m["status"], stats=dict(job.stats), stop=m["stop_reason"])
        if k == 1:
            return [], None, "x"                                   # zero resultados (válido)
        if k == 2:
            return [], "HTTP 403 (bloqueio ou limite de requisições da fonte)", "x"
        if k == 3 and state["timeouts"] < 1:
            state["timeouts"] += 1
            return [], "tempo esgotado", "x"                       # timeout na 1ª tentativa
        return [hit(q)], None, "x"
    r, job, _ = run(monkeypatch, fn, 323, job=job)
    assert seen[:4] == [1, 2, 3, 4] and seen[4] == 5               # após 4 consultas o worker seguiu para a 5ª
    assert snap["status"] in ("RUNNING", "DEGRADED") and snap["stop"] == ""     # missão continua RUNNING
    st = snap["stats"]
    assert st["queries_empty"] == 1 and st["queries_failed"] == 1 and st["queries_retry"] == 1 and st["queries_total"] == 323
    assert st["queries_pending"] > 300 and st["mission_status"] in ("RUNNING", "DEGRADED")
    # fim real: fila esgotada (todas processadas), nunca "consultas esgotadas" enquanto havia pendentes
    assert r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED" and r["status"] == "COMPLETED" and r["consultas"] == 323
    assert r["queries"] == {**r["queries"], "pending": 0, "running": 0, "retry": 0, "total": 323, "empty": 1, "failed": 1}
    with db.connect() as c:
        by = dict(c.execute("SELECT status, COUNT(*) FROM mission_tasks GROUP BY status").fetchall())
    assert by == {"SUCCESS": 321, "EMPTY": 1, "BLOCKED": 1}          # q3 foi retentada (backoff) e teve sucesso; q2 = BLOCKED
    assert any("MISSION_STOP_REASON=QUERY_QUEUE_EXHAUSTED" in l for l in job.lines)


def test_query_status_transitions_and_retry_backoff(env, monkeypatch):
    plan(3, request_delay=0)
    times = []
    clock = Clock()
    def fn(src, q):
        if q == "consulta 1":
            times.append(clock.now())
            return [], "tempo esgotado", "x"                       # sempre falha: 3 tentativas com backoff 2s, 5s
        return [hit(q)], None, "x"
    r, job, _ = run(monkeypatch, fn, 3, clock=clock)
    assert len(times) == 3 and 1.5 <= times[1] - times[0] <= 2.6 and 4.0 <= times[2] - times[1] <= 6.1     # 2s e 5s (com jitter)
    with db.connect() as c:
        st = {row["q"]: row["status"] for row in c.execute("SELECT q, status FROM mission_tasks")}
        att = c.execute("SELECT attempts FROM mission_tasks WHERE q='consulta 1'").fetchone()[0]
    assert st == {"consulta 1": "FAILED", "consulta 2": "SUCCESS", "consulta 3": "SUCCESS"} and '"bing": 3' in att
    assert r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED" and r["queries"]["failed"] == 1     # QUERY FAILED ≠ MISSION FAILED


# ---------------------------------------------------------------- 17. rate limit
def test_rate_limit_pauses_only_that_source_and_requeues(env, monkeypatch):
    plan(6)
    seen, snaps = [], []
    job = pipeline.Job("m")
    clock = Clock()

    def fn(src, q):
        seen.append((src, num(q)))
        if src == "tiktok":
            return [], "RATE LIMITED (HTTP 429)", "x"
        if len(seen) == 4:
            snaps.append((mission_row()["status"], dict(job.stats)))
        return [hit(q, src=src)], None, "x"
    sources.RETRY_AFTER[sources.SOURCE_LABELS["tiktok"]] = 45
    r, job, _ = run(monkeypatch, fn, 6, srcs=("tiktok", "bing"), clock=clock, job=job)
    assert [s for s in seen[:2]] == [("bing", 1), ("tiktok", 1)] or seen[0][0] == "bing"        # prioridade: bing antes de tiktok
    assert [k for s, k in seen if s == "bing"] == [1, 2, 3, 4, 5, 6]                           # próxima fonte atende TODAS as consultas
    status, st = snaps[0]
    assert status in ("RUNNING", "DEGRADED") and st["fontes_situacao"]["TikTok Search"].startswith("PAUSADA")   # só a fonte pausa
    assert r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED" and "tiktok" in r["fontes_pausadas"]
    tk = [t for t in seen if t[0] == "tiktok"]
    assert 1 <= len(tk) <= 6                                                                   # tiktok voltou após cooldown (retry), sem martelar


def test_rate_limit_respects_retry_after(env, monkeypatch):
    plan(2, request_delay=0)
    clock, marks = Clock(), []
    def fn(src, q):
        marks.append((src, q, clock.now()))
        if src == "tiktok":
            sources.RETRY_AFTER[sources.SOURCE_LABELS["tiktok"]] = 200
            return [], "RATE LIMITED (HTTP 429)", "x"
        return [hit(q)], None, "x"
    r, _, _ = run(monkeypatch, fn, 2, srcs=("tiktok", "bing"), clock=clock)
    tk = [m for m in marks if m[0] == "tiktok"]
    assert len(tk) >= 2 and tk[1][2] - tk[0][2] >= 200                                         # Retry-After (200s) > cooldown padrão (30s)
    assert r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED"


# ---------------------------------------------------------------- 18. exceção
def test_exception_in_source_marks_query_failed_and_worker_takes_next(env, monkeypatch):
    plan(5)
    seen = []
    def fn(src, q):
        seen.append(num(q))
        if q == "consulta 1":
            raise RuntimeError("boom " * 40)
        return [hit(q)], None, "x"
    r, job, _ = run(monkeypatch, fn, 5)
    assert seen.count(1) == 3 and 2 in seen[:2] and r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED"   # 3 tentativas (com backoff) e a fila segue
    with db.connect() as c:
        st = dict(c.execute("SELECT q, status FROM mission_tasks").fetchall())
    assert st["consulta 1"] == "FAILED" and all(st[f"consulta {i}"] == "SUCCESS" for i in range(2, 6))
    assert mission_row()["status"] == "COMPLETED"


# ---------------------------------------------------------------- 19. consultas vazias
def test_first_50_empty_queries_do_not_stop_or_pause(env, monkeypatch):
    plan(60)
    calls = []
    def fn(src, q):
        calls.append((src, num(q)))
        return ([], None, "x") if num(q) <= 50 else ([hit(q, src=src)], None, "x")
    r, job, _ = run(monkeypatch, fn, 60, srcs=("bing", "ddg"))
    assert r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED" and r["consultas"] == 60
    assert {k for _, k in calls if k > 50} == set(range(51, 61)) and len(calls) == 120         # chegou à 51 e além, nas duas fontes
    assert r["fontes_pausadas"] == [] and r["fontes_indisponiveis"] == [] and r["queries"]["failed"] == 0
    with db.connect() as c:
        assert dict(c.execute("SELECT status, COUNT(*) FROM mission_tasks GROUP BY status").fetchall()) == {"EMPTY": 50, "SUCCESS": 10}


def test_independent_queries_zero_result_does_not_discard_related_queries(env, monkeypatch):
    plan(0, priority_queries=['"roleta" "sinais"', "roleta plataforma", "roleta ao vivo", "grupo sinais", "tigrinho pagando", "link na bio"])
    got = []
    def fn(src, q):
        got.append(q)
        return ([], None, "x") if q == '"roleta" "sinais"' else ([hit(q)], None, "x")
    r, _, _ = run(monkeypatch, fn, 0)
    assert got == ['"roleta" "sinais"', "roleta plataforma", "roleta ao vivo", "grupo sinais", "tigrinho pagando", "link na bio"]
    assert r["queries"]["empty"] == 1 and r["queries"]["completed"] == 6


# ---------------------------------------------------------------- 20. retomada
def test_worker_crash_resumes_without_restarting_from_zero(env, monkeypatch):
    plan(323)
    first, second = [], []
    def crashing(src, q):
        first.append(num(q))
        if len(first) == 100:
            raise Crash()
        return [hit(q)], None, "x"
    monkeypatch.setattr(sources, "run_source", crashing)
    job1 = pipeline.Job("m")
    clock = Clock()
    with pytest.raises(Crash):
        mission.run_mission(job1, 200, 0, "rapido", ["bing"], clock=clock.now, sleeper=clock.sleep)
    m = mission_row()
    assert m["status"] in ("RUNNING", "DEGRADED") and m["stop_reason"] == ""                    # missão continua aberta
    with db.connect() as c:
        d = dict(c.execute("SELECT status, COUNT(*) FROM mission_tasks GROUP BY status").fetchall())
    assert d["SUCCESS"] == 99 and d["RUNNING"] == 1 and d["PENDING"] == 223                     # 99 feitas, a 100 estava em execução
    def resumed(src, q):
        second.append(num(q))
        return [hit(q)], None, "x"
    r, job2, _ = run(monkeypatch, resumed, 323, mission_id=m["id"], clock=clock)
    assert second[0] == 100 and second[-1] == 323 and len(second) == 224                        # reprocessa só a RUNNING e segue da 101
    assert not (set(second) & set(range(1, 100)))                                                # nada foi refeito do zero
    assert r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED" and r["consultas"] == 323 and r["mission_id"] == m["id"]
    assert job2.stats["mission_id"] == m["id"] and job2.stats["mission_status"] == "COMPLETED" and mission_row()["status"] == "COMPLETED"
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 1 and c.execute("SELECT COUNT(*) FROM mission_tasks").fetchone()[0] == 323


def test_watchdog_restarts_dead_worker_and_continues_same_mission(env, monkeypatch):
    plan(40)
    calls = []
    def fn(src, q):
        calls.append(num(q))
        return [hit(q)], None, "x"
    monkeypatch.setattr(sources, "run_source", fn)
    orig, state = mission.MissionRun.publish, {"n": 0}
    def flaky(self):
        state["n"] += 1
        if state["n"] == 20:
            raise RuntimeError("falha interna do worker")
        return orig(self)
    monkeypatch.setattr(mission.MissionRun, "publish", flaky)
    clock, job = Clock(), pipeline.Job("m")
    r = mission.run_supervised(job, 200, 0, "rapido", ["bing"], clock=clock.now, sleeper=clock.sleep)
    assert any("watchdog: worker reiniciado (1/3)" in l for l in job.lines)
    assert r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED" and r["consultas"] == 40 and len(calls) == 40   # continuou; nada refeito
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 1


def test_watchdog_gives_up_with_fatal_reason_only_after_restarts(env, monkeypatch):
    plan(5)
    monkeypatch.setattr(sources, "run_source", lambda s, q: ([hit(q)], None, "x"))
    monkeypatch.setattr(mission.MissionRun, "publish", lambda self: (_ for _ in ()).throw(RuntimeError("sempre")))
    job = pipeline.Job("m")
    r = mission.run_supervised(job, 200, 0, "rapido", ["bing"])
    assert r["stop_reason"] == "FATAL_INTERNAL_ERROR" and r["status"] == "FAILED" and mission_row()["status"] == "FAILED"
    assert sum("watchdog: worker reiniciado" in l for l in job.lines) == 3


# ---------------------------------------------------------------- condição global única de encerramento
def test_completion_origin_is_logged_and_invalid_completion_is_blocked(env, monkeypatch):
    plan(8)
    orig, state = mission.MissionRun.decide, {"n": 0}
    def premature(self, snap, cnt):
        state["n"] += 1
        return mission.STOP_EXHAUSTED if state["n"] in (2, 3) else orig(self, snap, cnt)     # tentativa indevida de encerrar com pendentes
    monkeypatch.setattr(mission.MissionRun, "decide", premature)
    r, job, _ = run(monkeypatch, lambda s, q: ([hit(q)], None, "x"), 8)
    blocked = [l for l in job.lines if "MISSION COMPLETION CALLED FROM" in l and "valid=False" in l]
    assert len(blocked) == 2 and "queries_pending=" in blocked[0] and "stop_reason=QUERY_QUEUE_EXHAUSTED" in blocked[0]
    assert "mission.py run line=" in blocked[0] and "target=200" in blocked[0] and "confirmed=0" in blocked[0]
    assert r["consultas"] == 8 and r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED"                 # o encerramento indevido foi ignorado
    final = [l for l in job.lines if "MISSION COMPLETION CALLED FROM" in l and "valid=True" in l]
    assert len(final) == 1 and "queries_pending=0" in final[0] and "queries_processed=8" in final[0]


def test_valid_stop_reasons_and_no_pool_stop_by_default(env, monkeypatch):
    plan(30)
    r, job, _ = run(monkeypatch, lambda s, q: ([hit(q, i) for i in range(200)], None, "x"), 30)   # 200 resultados por consulta
    assert r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED" and r["consultas"] == 30 and r["unicos"] >= 6000   # NÃO para em "pool" (bug original)
    plan(30)
    with db.connect() as c:
        c.execute("DELETE FROM missions")
    j = pipeline.Job("m"); j.cancelled = True
    assert run(monkeypatch, lambda s, q: ([hit(q)], None, "x"), 30, job=j)[0]["stop_reason"] == "USER_CANCELLED"
    assert mission_row()["status"] == "CANCELLED"
    plan(30, mission_max_minutes=0.2)                       # limite global de tempo explícito (12 s no relógio falso)
    r, _, _ = run(monkeypatch, lambda s, q: ([hit(q)], None, "x"), 30)
    assert r["stop_reason"] == "GLOBAL_TIMEOUT" and r["queries"]["pending"] > 0 and mission_row()["stop_reason"] == "GLOBAL_TIMEOUT"


def test_target_reached_by_analyst_confirmations(env, monkeypatch):
    plan(20)
    n = {"c": 0}
    def fn(src, q):
        n["c"] += 1
        if n["c"] == 5:                                      # o analista confirma 3 perfis durante a missão
            with db.connect() as c:
                for i in range(1, 4):
                    pipeline.set_status(c, i, "CONFIRMADO")
        return [hit(q, 0)], None, "x"
    r, _, _ = run(monkeypatch, fn, 20, goal=3)
    assert r["stop_reason"] == "TARGET_REACHED" and r["status"] == "TARGET_REACHED" and r["queries"]["pending"] > 0


def test_all_sources_unavailable_is_global_only_when_definitive(env, monkeypatch):
    plan(40)
    r, job, clock = run(monkeypatch, lambda s, q: ([], "HTTP 403 (bloqueio ou limite de requisições da fonte)", "x"), 40, srcs=("bing", "ddg"))
    assert r["stop_reason"] == "ALL_SOURCES_UNAVAILABLE" and r["status"] == "EXHAUSTED"       # só depois de pausar e esgotar as fontes de vez
    assert set(r["fontes_indisponiveis"]) == {"bing", "ddg"} and clock.now() > 1_000 + 30       # pausas com cooldown real, não encerramento imediato
    assert r["queries"]["pending"] > 0 and r["queries"]["failed"] >= 3                          # havia consultas pendentes quando as fontes se esgotaram
    assert mission_row()["stop_reason"] == "ALL_SOURCES_UNAVAILABLE"


def test_queries_without_source_are_recorded_not_silently_dropped(env, monkeypatch):
    monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False); monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)
    plan(3)
    with db.connect() as c:
        c.execute("UPDATE hunts SET enabled=1 WHERE kind='commercial'")
    r, _, _ = run(monkeypatch, lambda s, q: ([hit(q)], None, "x"), 3, srcs=("bing", "commercial"))
    assert r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED"
    with db.connect() as c:
        rows = c.execute("SELECT status, note FROM mission_tasks WHERE only_src='commercial'").fetchall()
    assert len(rows) == 10 and all(x["status"] == "FAILED" and "sem fonte" in x["note"] for x in rows)     # registradas, não descartadas
    assert r["queries"]["total"] == 13 and r["queries"]["failed"] == 10


def test_recent_queries_are_not_silently_skipped_by_default(env, monkeypatch):
    plan(5)
    calls = []
    fn = lambda s, q: (calls.append(q), ([hit(q)], None, "x"))[1]
    run(monkeypatch, fn, 5)
    run(monkeypatch, fn, 5)
    assert len(calls) == 10                                  # 2ª missão executa tudo de novo (nada de "esgotada" instantânea)
    plan(5, mission_skip_recent_hours=1)
    r, _, _ = run(monkeypatch, fn, 5)
    assert len(calls) == 10 and r["queries"]["completed"] == 5 and r["stop_reason"] == "QUERY_QUEUE_EXHAUSTED"   # cache só se pedido


# ---------------------------------------------------------------- API: estado atual, órfã, retomar, watchdog
def test_api_current_resume_orphan_and_watchdog(env, monkeypatch, tmp_path):
    plan(12, request_delay=0)
    fn_calls = []
    def crashing(src, q):
        fn_calls.append(num(q))
        if len(fn_calls) == 5:
            raise Crash()
        return [hit(q)], None, "x"
    monkeypatch.setattr(sources, "run_source", crashing)
    path = str(tmp_path / "t.db")
    app = create_app(path); c = app.test_client()
    with pytest.raises(Crash):
        mission.run_mission(pipeline.Job("m"), 200, 0, "rapido", ["bing"])
    mid = mission_row()["id"]
    monkeypatch.setattr(sources, "run_source", lambda s, q: (fn_calls.append(num(q)), ([hit(q)], None, "x"))[1])
    mission.STARTED_HERE.discard(mid)                        # simula missão de OUTRO processo (não reinicia sozinha)
    cur = c.get("/api/mission/current").json
    assert cur["id"] == mid and cur["status"] in ("RUNNING", "DEGRADED") and cur["orphan"] is True and cur["alive"] is False
    assert cur["counters"]["pending"] > 0 and "watchdog_restarted" not in cur
    rj = c.post("/api/mission/resume", json={}).json
    for _ in range(200):
        j = c.get(f"/api/jobs/{rj['job']}").json
        if j["status"] != "running":
            break
        _real_sleep(0.05)
    assert j["status"] == "done" and j["result"]["stop_reason"] == "QUERY_QUEUE_EXHAUSTED" and j["result"]["consultas"] == 12
    done = c.get("/api/mission/current").json
    assert done["status"] == "COMPLETED" and done["orphan"] is False and done["stop_reason"] == "QUERY_QUEUE_EXHAUSTED"
    assert c.post("/api/mission/resume", json={}).status_code == 409                          # nada aberto para retomar
