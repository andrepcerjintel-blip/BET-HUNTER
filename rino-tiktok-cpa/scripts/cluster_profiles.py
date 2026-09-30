"""Clusteriza perfis por chaves FORTES compartilhadas: domínio final, código de afiliado, Telegram/WhatsApp,
hub/URL idêntica e texto de bio repetido. Encurtador, hashtag e campanha entram só como sinais descritivos
(um bit.ly ou #fy compartilhado não prova vínculo)."""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SKIP = {"discarded"}


def _keys(meta, an, redirects):
    ks = set()
    for d in an.get("domains") or []:
        ks.add(("dominio", common.registrable(d)))
    for a in an.get("affiliate_ids") or []:
        if a.get("strength") == "forte":
            ks.add(("afiliado", f"{a['value']}"))
    for r in redirects:
        if r.get("kind") in ("telegram", "whatsapp"):
            ks.add((r["kind"], re.sub(r"[?#].*", "", r["short_url"]).lower().rstrip("/")))
        elif r.get("kind") == "hub":
            ks.add(("hub", re.sub(r"[?#].*", "", r["short_url"]).lower().rstrip("/")))
        if r.get("final_url") and r.get("kind") == "encurtador" and not r.get("error"):
            ks.add(("url_final", re.sub(r"#.*", "", r["final_url"]).lower()))
    bio = common.norm(meta.get("bio") or "")
    if len(bio) >= 40:
        ks.add(("bio", bio))
    return ks


def build_clusters(profiles):
    """profiles: [{'meta','analysis','redirects'}]. -> lista de clusters (≥2 perfis) + perfis isolados."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    by_key, info = {}, {}
    for p in profiles:
        u = p["meta"]["username"]
        find(u)
        info[u] = p
        for k in _keys(p["meta"], p["analysis"], p["redirects"]):
            by_key.setdefault(k, []).append(u)
    for k, users in by_key.items():
        for other in users[1:]:
            parent[find(other)] = find(users[0])
    groups = {}
    for u in info:
        groups.setdefault(find(u), []).append(u)
    clusters, single = [], []
    for users in groups.values():
        if len(users) < 2:
            single.extend(users)
            continue
        keys = {k: v for k, v in by_key.items() if len(set(v) & set(users)) >= 2}
        plats, doms, refs, shorts, tags, fol, unk, edges = set(), set(), set(), set(), {}, 0, 0, []
        for u in users:
            an, m = info[u]["analysis"], info[u]["meta"]
            plats.update(an.get("platforms") or [])
            doms.update(an.get("domains") or [])
            refs.update(f"{a['parameter']}={a['value']}" for a in an.get("affiliate_ids") or [] if a.get("strength") == "forte")
            shorts.update(an.get("shorteners") or [])
            for h in an.get("hashtags") or []:
                tags[h] = tags.get(h, 0) + 1
            f = common.parse_count(m.get("followers"))
            fol, unk = (fol + f, unk) if f is not None else (fol, unk + 1)
            for r in info[u]["redirects"]:
                if r.get("kind") in ("encurtador", "hub") and r.get("final_domain"):
                    edges.append(f"TikTok @{u} → {common.host_of(r['short_url'])} → {r['final_domain']}")
        clusters.append({"perfis": sorted(users), "n_perfis": len(users), "plataformas": sorted(plats) or ["NÃO IDENTIFICADO"],
                         "dominios": sorted(doms), "referrals": sorted(refs), "encurtadores": sorted(shorts),
                         "hashtags_comuns": sorted(h for h, n in tags.items() if n >= 2),
                         "seguidores_conhecidos": fol, "perfis_sem_seguidores": unk,
                         "chaves_compartilhadas": sorted(f"{t}:{v[:60]}" for t, v in keys),
                         "relacoes": sorted(set(edges))})
    clusters.sort(key=lambda c: (-c["n_perfis"], -c["seguidores_conhecidos"]))
    for i, c in enumerate(clusters, 1):
        c["cluster"] = f"CLUSTER {i:02d}"
    return clusters, single


def run():
    from classify_profiles import classify_all, meta_from_index
    from resume_collection import load_index, DONE
    rows = [r for r in load_index().values() if r["status"] in DONE]
    profiles = []
    for r in rows:
        m = meta_from_index(r)
        an = common.read_json(common.P("output", "evidence", m["username"], "analysis.json")) or {}
        profiles.append({"meta": m, "analysis": an, "redirects": common.read_json(common.P("output", "evidence", m["username"], "redirects.json"), []) or []})
    profiles = [p for p in profiles if p["analysis"].get("classification") != "DESCARTADO"]
    clusters, single = build_clusters(profiles)
    common.write_json(common.P("output", "clusters.json"), {"clusters": clusters, "isolados": single})
    common.write_csv(common.P("output", "clusters.csv"), [{
        "cluster": c["cluster"], "plataformas": "; ".join(c["plataformas"]), "dominios": "; ".join(c["dominios"]),
        "referrals": "; ".join(c["referrals"]), "n_perfis": c["n_perfis"], "perfis": "; ".join("@" + u for u in c["perfis"]),
        "seguidores_conhecidos": c["seguidores_conhecidos"], "perfis_sem_seguidores": c["perfis_sem_seguidores"]} for c in clusters],
        ["cluster", "plataformas", "dominios", "referrals", "n_perfis", "perfis", "seguidores_conhecidos", "perfis_sem_seguidores"])
    return clusters, single


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    cl, single = run()
    for c in cl:
        print(f"{c['cluster']}: {', '.join(c['plataformas'])} | {', '.join(c['dominios'])} | {', '.join(c['referrals'])} | "
              f"perfis={c['n_perfis']} seguidores_conhecidos={c['seguidores_conhecidos']:,}".replace(",", "."))
    print(f"{len(single)} perfis isolados (sem chave compartilhada)")


if __name__ == "__main__":
    main()
