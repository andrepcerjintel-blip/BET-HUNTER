"""Exporta output/takedown.csv. Só entra perfil com classificação CONFIRMADO E decisão humana CONFIRMADO registrada."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

FIELDS = ["profile_url", "username", "followers", "reason", "evidence", "external_domain", "affiliate_code", "screenshot"]


def build_rows():
    import classify_profiles as cp
    from resume_collection import load_index
    fb, idx, rows = cp.load_feedback(), load_index(), []
    for u, f in fb.items():
        if f["decisao"] != cp.CONFIRMADO:
            continue
        an = common.read_json(common.P("output", "evidence", u, "analysis.json"), {}) or {}
        if not an:                                                # sem pacote de evidência não há o que exportar
            continue
        if an.get("classification_auto") not in (cp.CONFIRMADO, cp.PROVAVEL, cp.REVISAO):
            continue                                              # auto DESCARTADO + humano CONFIRMADO: exige reavaliação
        meta = common.read_json(common.P("output", "evidence", u, "metadata.json"), {}) or {}
        row = idx.get(u, {})
        fol = common.parse_count(meta.get("followers") if meta.get("followers") is not None else row.get("followers"))
        rows.append({"profile_url": meta.get("profile_url") or row.get("url_perfil") or f"https://www.tiktok.com/@{u}",
                     "username": u, "followers": fol if fol is not None else "NÃO IDENTIFICADO",
                     "reason": f["motivo"] or "NÃO IDENTIFICADO", "evidence": " | ".join(an.get("evidence") or []),
                     "external_domain": "; ".join(an.get("domains") or []) or "NÃO IDENTIFICADO",
                     "affiliate_code": "; ".join(f"{a['parameter']}={a['value']}" for a in an.get("affiliate_ids") or [] if a.get("strength") == "forte") or "NÃO IDENTIFICADO",
                     "screenshot": meta.get("screenshot") or row.get("screenshot") or "NÃO IDENTIFICADO"})
    return sorted(rows, key=lambda r: r["username"])


def run():
    rows = build_rows()
    common.write_csv(common.P("output", "takedown.csv"), rows, FIELDS)
    return rows


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    print(f"{len(run())} perfis aprovados manualmente exportados para output/takedown.csv")


if __name__ == "__main__":
    main()
