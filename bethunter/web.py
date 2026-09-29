"""API + UI (Flask). Local, sem autenticação: pensado para uso na própria máquina do analista."""
import json
import os

from flask import Flask, Response, jsonify, render_template, request

from . import db, envcheck, exporter, mission, net, pipeline, queries, sources, tiktok_local, visual
from .util import export_dir, setup_logging
from .db import jl

FILTER_KEYS = ["view", "min_score", "status", "classification", "platform", "domain", "game", "hashtag", "ev_type",
               "with_link", "with_aff", "date_from", "date_to", "source", "cluster", "q"]


def create_app(db_path=None):
    if db_path:
        os.environ["BETHUNTER_DB"] = db_path
    db.init_db()
    setup_logging()
    with db.connect() as c:
        _s = db.get_settings(c)
    net.configure(_s["http_timeout"], _s["http_max_retries"], _s["http_min_interval"])
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False

    def filters():
        f = {k: request.args.get(k) for k in FILTER_KEYS if request.args.get(k) not in (None, "")}
        return f

    def body():
        return request.get_json(silent=True) or {}

    @app.errorhandler(Exception)
    def err(e):
        code = getattr(e, "code", 500) if hasattr(e, "code") and isinstance(getattr(e, "code"), int) else 500
        if code >= 500:
            setup_logging().exception("erro em %s", request.path)
        return jsonify({"ok": False, "error": (f"{type(e).__name__}: {str(e)[:160]}" if code < 500 else
                                               "erro interno (detalhes em logs/bethunter.log)")}), code

    @app.get("/favicon.ico")
    def favicon():
        return Response(status=204)

    @app.get("/")
    def index():
        return render_template("index.html")

    # ---------------------------------------------------------------- painel / listagem
    @app.get("/api/stats")
    def api_stats():
        with db.connect() as c:
            return jsonify({**queries.stats(c), "mission": queries.mission(c)})

    @app.get("/api/candidates")
    def api_candidates():
        with db.connect() as c:
            return jsonify(queries.list_candidates(
                c, filters(), int(request.args.get("page", 1)), int(request.args.get("per_page", 50)),
                request.args.get("sort", "priority")))

    @app.get("/api/candidates/<int:cid>")
    def api_candidate(cid):
        with db.connect() as c:
            d = queries.candidate_detail(c, cid)
        return (jsonify(d), 200) if d else (jsonify({"ok": False, "error": "não encontrado"}), 404)

    @app.get("/api/queue")
    def api_queue():
        """Próximos a revisar (modo revisão rápida): com evidência, NOVO/REVISAR, mais importantes primeiro."""
        with db.connect() as c:
            f = {"view": "results", "status": ["NOVO", "REVISAR"], "min_score": request.args.get("min_score") or None}
            f = {k: v for k, v in f.items() if v}
            res = queries.list_candidates(c, f, 1, int(request.args.get("n", 30)), "priority")
            return jsonify({"total": res["total"], "ids": [i["id"] for i in res["items"]]})

    # ---------------------------------------------------------------- ações sobre candidatos
    @app.post("/api/candidates/<int:cid>/status")
    def api_status(cid):
        b = body()
        try:
            with db.connect() as c:
                pipeline.set_status(c, cid, b.get("status"), b.get("note"))
                r = c.execute("SELECT status, score FROM candidates WHERE id=?", (cid,)).fetchone()
            return jsonify({"ok": True, "status": r["status"], "score": r["score"]})
        except PermissionError as e:
            return jsonify({"ok": False, "error": str(e)}), 409
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.post("/api/candidates/<int:cid>/note")
    def api_note(cid):
        with db.connect() as c:
            c.execute("UPDATE candidates SET analyst_note=? WHERE id=?", (body().get("note", ""), cid))
        return jsonify({"ok": True})

    @app.post("/api/candidates/<int:cid>/evidence")
    def api_evidence(cid):
        b = body()
        try:
            ok = pipeline.add_manual_evidence(cid, text=b.get("text", ""), url_video=b.get("url_video", ""),
                                              link=b.get("link", ""), tags=b.get("tags") or [])
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        with db.connect() as c:
            return jsonify({"ok": True, "novo": ok, "candidate": queries.candidate_detail(c, cid)})

    @app.post("/api/manual")
    def api_manual_candidate():
        b = body()
        try:
            cid = pipeline.add_manual_candidate(b.get("username", ""), text=b.get("text", ""),
                                                url_video=b.get("url_video", ""), link=b.get("link", ""),
                                                tags=b.get("tags") or [])
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "id": cid})

    @app.post("/api/candidates/<int:cid>/reanalyze")
    def api_reanalyze(cid):
        with db.connect() as c:
            u = c.execute("SELECT username FROM candidates WHERE id=?", (cid,)).fetchone()
        if not u:
            return jsonify({"ok": False, "error": "não encontrado"}), 404
        return jsonify(pipeline.investigate(u["username"], force=True, source=None))

    @app.post("/api/bulk/status")
    def api_bulk():
        b = body()
        f = b.get("filters") or {}
        if b.get("ids"):
            f = {"ids": b["ids"], "view": "all"}
        where, args = queries.build_where(f)
        st = b.get("status")
        done = skipped = 0
        with db.connect() as c:
            for r in c.execute(f"SELECT id FROM candidates WHERE {where}", args).fetchall():
                try:
                    pipeline.set_status(c, r["id"], st)
                    done += 1
                except PermissionError:
                    skipped += 1
        return jsonify({"ok": True, "alterados": done, "ignorados_sem_evidencia": skipped})

    # ---------------------------------------------------------------- investigar / expandir / importar
    @app.post("/api/investigate")
    def api_investigate():
        b = body()
        return jsonify(pipeline.investigate(b.get("target", ""), force=bool(b.get("force")), expand=False))

    @app.post("/api/expand")
    def api_expand():
        b = body()
        cid = b.get("id")
        if not cid:
            from .util import normalize_username
            u = normalize_username(b.get("target", ""))
            if not u:
                return jsonify({"ok": False, "error": "username inválido"}), 400
            r = pipeline.investigate(u, force=False)
            cid = r.get("id")
            if not cid:
                return jsonify(r), 400
        job = pipeline.start_job(f"Expandir candidato #{cid}", lambda j: pipeline.expand_candidate(int(cid), job=j))
        return jsonify({"ok": True, "job": job.id})

    @app.post("/api/import")
    def api_import():
        text, fname = "", ""
        if request.files.get("file"):
            fl = request.files["file"]
            fname = fl.filename or ""
            text = fl.read().decode("utf-8-sig", errors="replace")
        else:
            text = body().get("text", "") or request.form.get("text", "")
        opts = body() if request.is_json else request.form
        source = (opts.get("source") or "lista importada").strip()
        as_bet = str(opts.get("domains_as_bet", "1")) not in ("0", "false", "False")
        summ = pipeline.import_items(text, filename=fname, source=source, domains_as_bet=as_bet)
        fetch = str(opts.get("fetch", "0")) in ("1", "true", "True")
        job_id = None
        if fetch and summ["novos_usernames"]:
            names = list(summ["novos_usernames"])
            def go(job):
                with db.connect() as c:
                    st = db.get_settings(c)
                for i, u in enumerate(names, 1):
                    r = pipeline.investigate(u, force=True, source=None)
                    job.progress(i, len(names), f"@{u}: {'ok' if r.get('profile_fetched') else 'perfil não coletado'}")
                    import time
                    time.sleep(st.get("request_delay", 0))
                return {"perfis": len(names)}
            job_id = pipeline.start_job("Coletar perfis importados", go).id
        return jsonify({"ok": True, **summ, "job": job_id})

    # ---------------------------------------------------------------- buscas / caças
    @app.post("/api/search")
    def api_search():
        b = body()
        qs = [q.strip() for q in b.get("queries", []) if q and q.strip()]
        srcs = sources.by_priority(b.get("sources") or sources.SEARCH_SOURCES)
        if b.get("combine_a") and b.get("combine_b"):
            qs += [f'"{a.strip()}" "{c.strip()}"' if " " in a.strip() or " " in c.strip() else f"{a.strip()} {c.strip()}"
                   for a in b["combine_a"] for c in b["combine_b"] if a.strip() and c.strip()]
        qs = list(dict.fromkeys(qs))
        if not qs:
            return jsonify({"ok": False, "error": "nenhuma consulta"}), 400
        with db.connect() as c:
            settings = db.get_settings(c)

        def go(job):
            nonlocal srcs
            if "tiktok_local" in srcs:            # offline não é fatal: segue com as demais fontes
                est = tiktok_local.check(settings)
                if est["estado"] != tiktok_local.OK:
                    srcs = [x for x in srcs if x != "tiktok_local"]
                    job.log(f"TikTok Search Local {est['estado']}: fonte ignorada")
            tot = {"found": 0, "new": 0, "dups": 0, "errors": 0}
            new_ids, n, steps = [], 0, len(qs) * len(srcs)
            import time
            for q in qs:
                for s in srcs:
                    st = pipeline.run_query(q, s, settings, hunt=b.get("hunt", "nova busca"))
                    for k in ("found", "new", "dups"):
                        tot[k] += st[k]
                    tot["errors"] += 1 if st.get("error") else 0
                    new_ids += st["new_ids"]
                    n += 1
                    job.progress(n, steps, f"{q} [{s}] → {st['found']} ({st['new']} novos)" +
                                 (f" ⚠ {st['error']}" if st.get("error") else ""))
                    time.sleep(settings.get("request_delay", 0))
            if settings.get("enrich_after_search") and new_ids:
                tot["enriched"] = pipeline.enrich(new_ids, settings, job)
            return tot
        return jsonify({"ok": True, "job": pipeline.start_job("Nova busca", go).id})

    @app.post("/api/search/indicator")
    def api_search_indicator():
        b = body()
        job = pipeline.start_job(f"Buscar {b.get('tipo')}: {b.get('valor')}",
                                 lambda j: pipeline.indicator_search(b.get("tipo", ""), b.get("valor", ""), j))
        return jsonify({"ok": True, "job": job.id})

    @app.post("/api/mission/start")
    def api_mission_start():
        b = body()
        goal = max(1, int(b.get("goal", 200)))
        depth = max(0, min(3, int(b.get("depth", 2))))
        mode = b.get("mode", "rapido") if b.get("mode") in ("rapido", "completo") else "rapido"
        with db.connect() as c:
            s = db.save_settings(c, {"goal": goal, "mission_depth": depth, "mission_mode": mode,
                                     "mission_sources": b.get("sources") or db.get_settings(c)["mission_sources"]})
        job = pipeline.start_job(f"MISSÃO meta {goal} · profundidade {depth} · {mode.upper()}",
                                 lambda j: mission.run_supervised(j, goal, depth, mode, s["mission_sources"], precheck=True))
        return jsonify({"ok": True, "job": job.id})

    def _resume(mid):
        with db.connect() as c:
            m = mission.current(c) if not mid else None
            row = c.execute("SELECT * FROM missions WHERE id=?", (mid or (m or {}).get("id"),)).fetchone()
        if not row or row["status"] not in mission.M_OPEN:
            return None
        if any(getattr(j, "mission_id", None) == row["id"] and j.status == "running" for j in pipeline.JOBS.values()):
            return None                                  # já há worker vivo
        p = json.loads(row["params"] or "{}")
        job = pipeline.start_job(f"MISSÃO #{row['id']} RETOMADA", lambda j: mission.run_supervised(
            j, p.get("goal", 200), p.get("depth", 2), p.get("mode", "rapido"), p.get("sources", []), precheck=False,
            mission_id=row["id"]))
        job.mission_id = row["id"]
        return job

    @app.post("/api/mission/resume")
    def api_mission_resume():
        job = _resume(body().get("id"))
        if not job:
            return jsonify({"ok": False, "error": "nenhuma missão aberta sem worker para retomar"}), 409
        return jsonify({"ok": True, "job": job.id})

    @app.get("/api/mission/current")
    def api_mission_current():
        """Estado da última missão. WATCHDOG: missão aberta (pending>0), iniciada NESTE processo, sem worker vivo -> reinicia."""
        with db.connect() as c:
            m = mission.current(c)
        if m and m["status"] in mission.M_OPEN and not m["alive"] and m["counters"].get("open", 0) > 0 and m["id"] in mission.STARTED_HERE:
            job = _resume(m["id"])
            if job:
                m["watchdog_restarted"] = job.id
                m["alive"], m["orphan"] = True, False
        return jsonify(m or {})

    @app.post("/api/jobs/<jid>/cancel")
    def api_job_cancel(jid):
        j = pipeline.JOBS.get(jid)
        if not j:
            return jsonify({"ok": False, "error": "job inexistente"}), 404
        j.cancelled = True
        return jsonify({"ok": True})

    @app.post("/api/candidates/<int:cid>/restore")
    def api_restore(cid):
        """Desfazer (Ctrl+Z): devolve status e flag manual anteriores."""
        b = body()
        if b.get("status") not in pipeline.STATUSES:
            return jsonify({"ok": False, "error": "status inválido"}), 400
        with db.connect() as c:
            c.execute("UPDATE candidates SET status=?, status_manual=? WHERE id=?", (b["status"], 1 if b.get("manual") else 0, cid))
            pipeline.refresh_peers(c, cid, db.get_settings(c))
        return jsonify({"ok": True})

    @app.post("/api/candidates/<int:cid>/visual")
    def api_visual(cid):
        with db.connect() as c:
            s = db.get_settings(c)
        return jsonify({"ok": True, **visual.run_visual(cid, s, max_n=6)})

    @app.get("/api/manual-search-urls")
    def api_manual_urls():
        return jsonify(sources.manual_search_urls(request.args.get("q", "")))

    @app.get("/api/jobs/<jid>")
    def api_job(jid):
        j = pipeline.JOBS.get(jid)
        return (jsonify(j.to_dict()), 200) if j else (jsonify({"ok": False, "error": "job inexistente"}), 404)

    @app.get("/api/hunts")
    def api_hunts():
        with db.connect() as c:
            rows = c.execute("SELECT * FROM hunts ORDER BY position, id").fetchall()
        return jsonify([{**dict(r), "queries": jl(r["queries"]), "sources": jl(r["sources"]),
                         "enabled": bool(r["enabled"])} for r in rows])

    @app.post("/api/hunts")
    def api_hunt_create():
        b = body()
        with db.connect() as c:
            pos = c.execute("SELECT COALESCE(MAX(position),0)+1 FROM hunts").fetchone()[0]
            cur = c.execute("INSERT INTO hunts(name,kind,queries,sources,enabled,position) VALUES(?,?,?,?,1,?)",
                            (b.get("name", "Nova caça"), "queries", db.jd(b.get("queries", [])),
                             db.jd(b.get("sources") or sources.SEARCH_SOURCES), pos))
        return jsonify({"ok": True, "id": cur.lastrowid})

    @app.put("/api/hunts/<int:hid>")
    def api_hunt_update(hid):
        b = body()
        with db.connect() as c:
            if "name" in b:
                c.execute("UPDATE hunts SET name=? WHERE id=?", (b["name"], hid))
            if "queries" in b:
                c.execute("UPDATE hunts SET queries=? WHERE id=?", (db.jd([q.strip() for q in b["queries"] if q.strip()]), hid))
            if "sources" in b:
                c.execute("UPDATE hunts SET sources=? WHERE id=?", (db.jd(b["sources"]), hid))
            if "enabled" in b:
                c.execute("UPDATE hunts SET enabled=? WHERE id=?", (1 if b["enabled"] else 0, hid))
        return jsonify({"ok": True})

    @app.delete("/api/hunts/<int:hid>")
    def api_hunt_delete(hid):
        with db.connect() as c:
            c.execute("DELETE FROM hunts WHERE id=?", (hid,))
        return jsonify({"ok": True})

    @app.post("/api/hunts/run")
    def api_hunts_run():
        ids = body().get("ids")
        with db.connect() as c:
            if not ids:
                ids = [r[0] for r in c.execute("SELECT id FROM hunts WHERE enabled=1 ORDER BY position")]
        def go(job):
            out = []
            for i in ids:
                job.log(f"▶ caça #{i}")
                out.append(pipeline.run_hunt(i, job))
            return out
        return jsonify({"ok": True, "job": pipeline.start_job(f"Caças {ids}", go).id})

    # ---------------------------------------------------------------- clusters / métricas / log / config
    @app.get("/api/clusters")
    def api_clusters():
        with db.connect() as c:
            return jsonify(queries.clusters(c, int(request.args.get("min", 2)), request.args.get("tipo") or None))

    @app.get("/api/metrics")
    def api_metrics():
        with db.connect() as c:
            return jsonify(queries.metrics(c))

    @app.get("/api/source-metrics")
    def api_source_metrics():
        with db.connect() as c:
            return jsonify(queries.source_metrics(c))

    @app.get("/api/log")
    def api_log():
        with db.connect() as c:
            return jsonify(queries.search_log(c))

    @app.get("/api/settings")
    def api_settings_get():
        with db.connect() as c:
            return jsonify(db.get_settings(c))

    @app.put("/api/settings")
    def api_settings_put():
        with db.connect() as c:
            s = db.save_settings(c, body())
            net.configure(s["http_timeout"], s["http_max_retries"], s["http_min_interval"])
            n = pipeline.refresh_all(c, s) if request.args.get("reanalyze") == "1" else 0
        return jsonify({"ok": True, "settings": s, "reanalisados": n})

    @app.get("/api/env")
    def api_env():
        return jsonify(envcheck.static_status())

    @app.post("/api/env/test")
    def api_env_test():
        return jsonify(envcheck.test_all())

    @app.get("/api/facets")
    def api_facets():
        with db.connect() as c:
            from collections import Counter
            out = {}
            for key, col in (("platforms", "platforms"), ("games", "games"), ("hashtags", "hashtags")):
                cnt = Counter()
                for (v,) in c.execute(f"SELECT {col} FROM candidates WHERE {col}!='[]'"):
                    cnt.update(jl(v))
                out[key] = [k for k, _ in cnt.most_common(60)]
            out["sources"] = sorted({s for (v,) in c.execute("SELECT sources FROM candidates") for s in jl(v)})
            out["ev_types"] = sorted({s for (v,) in c.execute("SELECT ev_types FROM candidates") for s in jl(v)})
            out["domains"] = sorted({s for (v,) in c.execute("SELECT domains FROM candidates") for s in jl(v)})
        return jsonify(out)

    # ---------------------------------------------------------------- exportação
    @app.get("/api/export")
    def api_export():
        with db.connect() as c:
            data, mime, name = exporter.export(c, filters(), fmt=request.args.get("format", "csv"),
                                               kind=request.args.get("kind", "simple"),
                                               scope=request.args.get("scope", "confirmed"))
        saved = ""
        try:  # cópia local em exportacoes/ (além do download do navegador)
            os.makedirs(export_dir(), exist_ok=True)
            with open(os.path.join(export_dir(), name), "wb") as fh:
                fh.write(data)
            saved = os.path.join(export_dir(), name)
        except OSError:
            pass
        return Response(data, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                     "X-Saved-To": saved.encode("ascii", "replace").decode()})

    @app.get("/api/copy")
    def api_copy():
        with db.connect() as c:
            txt = exporter.copy_list(c, filters(), request.args.get("what", "profiles"), request.args.get("scope", "confirmed"))
        return Response(txt, mimetype="text/plain; charset=utf-8")

    return app
