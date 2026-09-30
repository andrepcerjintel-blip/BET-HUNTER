#!/usr/bin/env python3
"""Orquestrador: python run_mission.py --input links.txt

DESCOBERTA → NORMALIZAÇÃO → COLETA (retomável) → LINKS/REDIRECTS → INDICADORES → CLASSIFICAÇÃO → CLUSTERS →
(revisão humana) → EXPORTAÇÃO → RELATÓRIO. Rodar de novo retoma de onde parou."""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import build_report  # noqa: E402
import classify_profiles  # noqa: E402
import cluster_profiles  # noqa: E402
import collect_profiles  # noqa: E402
import common  # noqa: E402
import export_takedown  # noqa: E402
import extract_indicators  # noqa: E402
import normalize_tiktok  # noqa: E402
import resolve_urls  # noqa: E402
import resume_collection  # noqa: E402


def run(lines, collector, fetcher=None, resolve=True, limit=None, delay=(3.0, 7.0), sleeper=None, log=print):
    cands, rejected, short = normalize_tiktok.load_inputs(lines)
    for s in short:                                            # vm.tiktok.com/... → segue o redirecionamento e normaliza
        r = resolve_urls.resolve(s, fetcher) if resolve else None
        n = normalize_tiktok.normalize(r["final_url"]) if r else None
        if n:
            n["url_original"] = s
            if n["username"] not in {c["username"] for c in cands}:
                cands.append(n)
        else:
            rejected.append(s)
    allc = normalize_tiktok.merge_candidates(cands)
    if rejected:
        log(f"{len(rejected)} linhas ignoradas (não são perfil/vídeo do TikTok): {rejected[:5]}")
    kw = {"sleeper": sleeper} if sleeper else {}
    col = resume_collection.run_collection(allc, collector, delay=delay, limit=limit, log=log, **kw)
    # enriquecimento (links/redirects/metadata) só dos perfis com coleta concluída
    done = [r for r in resume_collection.load_index().values() if r["status"] in resume_collection.DONE]
    for r in done:
        meta = classify_profiles.meta_from_index(r)
        extract_indicators.enrich_profile(meta, fetcher, resolve)
        if r.get("screenshot") and Path(r["screenshot"]).exists():
            shutil.copy(r["screenshot"], common.evidence_dir(r["username"]) / "profile.png")
    results = classify_profiles.classify_all()
    cluster_profiles.run()
    export_takedown.run()
    metrics, text = build_report.run()
    return {"coleta": col, "resultados": results, "metricas": metrics, "relatorio": text}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="arquivo com URLs do TikTok (vídeo/foto/perfil) ou @usuarios, um por linha")
    ap.add_argument("--fixtures", help="JSON offline {username: {...}} no lugar do navegador (demonstração/testes)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--manual-wait", type=int, default=0, help="segundos para um humano resolver verificação do TikTok")
    ap.add_argument("--no-resolve", action="store_true", help="não seguir links externos")
    ap.add_argument("--limit", type=int, help="máximo de perfis por rodada")
    ap.add_argument("--review", action="store_true", help="abre a revisão humana interativa ao final")
    a = ap.parse_args(argv)
    lines = Path(a.input).read_text(encoding="utf-8").splitlines()
    col = collect_profiles.FixtureCollector(a.fixtures) if a.fixtures else collect_profiles.BrowserCollector(
        headless=a.headless, manual_wait=a.manual_wait)
    col.open()
    try:
        out = run(lines, col, resolve=not a.no_resolve, limit=a.limit)
    finally:
        col.close()
    print("\n" + out["relatorio"])
    if a.review:
        classify_profiles.review_interactive(out["resultados"])
        classify_profiles.classify_all()
        export_takedown.run()
        print("\n" + build_report.run()[1])


if __name__ == "__main__":
    main()
