"""Relatório final da missão (texto + JSON) e métricas."""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def br(n):
    return f"{n:,}".replace(",", ".")


def metrics():
    from resume_collection import load_index, DONE
    cands = common.read_csv(common.P("output", "candidates.csv"))
    idx = load_index()
    analyzed = [u for u, r in idx.items() if r["status"] in DONE]
    ans, fol_known, unknown, shorts, doms, plats, aff = {}, 0, 0, set(), set(), set(), set()
    cls = Counter()
    for u in analyzed:
        an = common.read_json(common.P("output", "evidence", u, "analysis.json"), {}) or {}
        ans[u] = an
        cls[an.get("classification", "SEM ANÁLISE")] += 1
        f = common.parse_count(idx[u].get("followers"))
        if f is None:
            unknown += 1
        else:
            fol_known += f
        shorts.update(an.get("shorteners") or [])
        doms.update(an.get("domains") or [])
        plats.update(an.get("platforms") or [])
        aff.update(f"{a['parameter']}={a['value']}" for a in an.get("affiliate_ids") or [] if a.get("strength") == "forte")
    cl = common.read_json(common.P("output", "clusters.json"), {"clusters": []})["clusters"]
    tk = common.read_csv(common.P("output", "takedown.csv"))
    st = Counter(r["status"] for r in idx.values())
    return {"perfis_encontrados": len(cands), "perfis_coletados": len(idx), "perfis_analisados": len(analyzed),
            "status_coleta": dict(st), "confirmados": cls["CONFIRMADO"], "provaveis": cls["PROVÁVEL"],
            "revisao": cls["REVISÃO HUMANA"], "descartados": cls["DESCARTADO"], "seguidores_conhecidos": fol_known,
            "perfis_sem_seguidores": unknown, "alcance_minimo_conhecido": fol_known,
            "plataformas": sorted(plats), "dominios": sorted(doms), "codigos_afiliado": sorted(aff),
            "encurtadores": sorted(shorts), "clusters": len(cl), "aprovados_takedown": [r["username"] for r in tk]}


def render(m):
    L = ["MISSÃO TIKTOK CPA", "",
         f"Perfis encontrados: {m['perfis_encontrados']}", f"Perfis analisados: {m['perfis_analisados']}",
         f"Confirmados: {m['confirmados']}", f"Prováveis: {m['provaveis']}", f"Revisão: {m['revisao']}",
         f"Descartados: {m['descartados']}", "",
         f"Seguidores conhecidos: {br(m['seguidores_conhecidos'])}",
         f"Seguidores desconhecidos: {m['perfis_sem_seguidores']} perfis sem contagem (não assumidos como zero)",
         f"Alcance mínimo conhecido: {br(m['alcance_minimo_conhecido'])}", "",
         f"Plataformas encontradas: {', '.join(m['plataformas']) or 'NÃO IDENTIFICADO'}",
         f"Domínios: {', '.join(m['dominios']) or 'NÃO IDENTIFICADO'}",
         f"Links de afiliados: {', '.join(m['codigos_afiliado']) or 'NÃO IDENTIFICADO'}",
         f"Encurtadores utilizados: {', '.join(m['encurtadores']) or 'nenhum'}",
         f"Clusters: {m['clusters']}", "",
         "Perfis aprovados para takedown: " + (", ".join("@" + u for u in m["aprovados_takedown"]) or "nenhum (aguardando revisão humana)")]
    pend = m["perfis_coletados"] - m["perfis_analisados"] + max(0, m["perfis_encontrados"] - m["perfis_coletados"])
    if pend:
        L += ["", f"ATENÇÃO: {pend} perfis pendentes/bloqueados — rode novamente para retomar. Status da coleta: {m['status_coleta']}"]
    return "\n".join(L)


def run():
    m = metrics()
    txt = render(m)
    common.P("output", "relatorio_missao.txt").write_text(txt, encoding="utf-8")
    common.write_json(common.P("output", "relatorio_missao.json"), m)
    return m, txt


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    print(run()[1])


if __name__ == "__main__":
    main()
