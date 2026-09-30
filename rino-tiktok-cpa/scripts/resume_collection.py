"""Loop de coleta com retomada: lê output/index_perfis.csv, pula concluídos, salva o CSV após CADA perfil."""
import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

FIELDS = ["seq", "username", "url_original", "url_perfil", "display_name", "followers", "following", "likes", "bio",
          "bio_links", "content", "status", "private", "collected_at", "screenshot", "attempts", "error"]
DONE = {"OK", "PRIVADA", "INDISPONIVEL"}          # estados finais; BLOQUEADO/ERRO_TEMPORARIO voltam a ser tentados
MAX_ATTEMPTS = 3
BACKOFF = (30, 60, 120)


def index_path():
    return common.P("output", "index_perfis.csv")


def load_index(path=None):
    return {r["username"]: r for r in common.read_csv(path or index_path())}


def is_done(row, max_attempts=MAX_ATTEMPTS):
    if row["status"] in DONE:
        return True
    try:
        return int(row.get("attempts") or 0) >= max_attempts          # desistência explícita e registrada
    except ValueError:
        return False


def pending(candidates, index, max_attempts=MAX_ATTEMPTS):
    return [c for c in candidates if c["username"] not in index or not is_done(index[c["username"]], max_attempts)]


def _row(cand, seq, rec, attempts):
    return {"seq": seq, "username": cand["username"], "url_original": cand.get("url_original"),
            "url_perfil": cand.get("url_perfil"), "display_name": rec.get("display_name"), "followers": rec.get("followers"),
            "following": rec.get("following"), "likes": rec.get("likes"), "bio": rec.get("bio"),
            "bio_links": json.dumps(rec.get("bio_links") or [], ensure_ascii=False),
            "content": json.dumps(rec.get("content") or [], ensure_ascii=False), "status": rec.get("status", "ERRO_TEMPORARIO"),
            "private": rec.get("private"), "collected_at": common.now_iso(), "screenshot": rec.get("screenshot"),
            "attempts": attempts, "error": rec.get("error")}


def run_collection(candidates, collector, delay=(3.0, 7.0), max_attempts=MAX_ATTEMPTS, stop_after_blocks=3,
                   sleeper=time.sleep, limit=None, log=print):
    """Coleta os pendentes. Bloqueios consecutivos pausam com backoff e, persistindo, encerram a rodada SEM perder
    progresso (nenhum bypass). -> {'coletados','pendentes','parou_por_bloqueio'}"""
    idx = load_index()
    order = {c["username"]: i + 1 for i, c in enumerate(candidates)}
    todo = pending(candidates, idx, max_attempts)
    if limit:
        todo = todo[:limit]
    blocks = done = 0
    for c in todo:
        u = c["username"]
        prev = idx.get(u)
        attempts = int(prev["attempts"] or 0) + 1 if prev else 1
        try:
            rec = collector.collect(u, order[u])
        except Exception as e:                                   # nenhuma falha do coletor derruba a rodada
            rec = {"status": "ERRO_TEMPORARIO", "error": f"exceção ({type(e).__name__})"}
        idx[u] = _row(c, order[u], rec, attempts)
        common.write_csv(index_path(), sorted(idx.values(), key=lambda r: int(r["seq"])), FIELDS)   # salva a CADA perfil
        log(f"[{order[u]}/{len(candidates)}] @{u} -> {idx[u]['status']}" + (f" ({rec.get('error')})" if rec.get("error") else ""))
        if rec.get("status") == "BLOQUEADO":
            blocks += 1
            if blocks >= stop_after_blocks:
                return {"coletados": done, "pendentes": len(pending(candidates, idx, max_attempts)), "parou_por_bloqueio": True}
            sleeper(BACKOFF[min(blocks - 1, len(BACKOFF) - 1)] + random.uniform(0, 5))
            continue
        blocks = 0
        if rec.get("status") in DONE:
            done += 1
        sleeper(random.uniform(*delay))
    return {"coletados": done, "pendentes": len(pending(candidates, idx, max_attempts)), "parou_por_bloqueio": False}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--manual-wait", type=int, default=0, help="segundos para um humano resolver verificação no navegador")
    ap.add_argument("--fixtures", help="JSON offline em vez do navegador")
    a = ap.parse_args(argv)
    import collect_profiles
    cands = common.read_csv(common.P("output", "candidates.csv"))
    col = collect_profiles.FixtureCollector(a.fixtures) if a.fixtures else collect_profiles.BrowserCollector(
        headless=a.headless, manual_wait=a.manual_wait)
    col.open()
    try:
        print(run_collection(cands, col))
    finally:
        col.close()


if __name__ == "__main__":
    main()
