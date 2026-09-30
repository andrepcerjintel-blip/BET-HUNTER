"""Extrai indicadores (termos, links, referrals, plataformas) e monta o pacote de evidência de cada perfil."""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import resolve_urls  # noqa: E402

CATS = {"plataforma": "plataforma", "afiliacao": "afiliação", "ganho": "ganho", "cadastro": "cadastro", "tema": "tema",
        "neg_jornalistico": "negativo jornalístico", "neg_denuncia": "negativo denúncia", "neg_critico": "negativo crítico"}
# "não é golpe" costuma aparecer em promoção; não pode contar como denúncia
NEG_SAFE = re.compile(r"\bnao (e|eh) (golpe|fraude)\b|\bsem golpe\b|\bnada de golpe\b")
TEXT_CODE = re.compile(r"(?i:c[oó]digo|code|cupom|convite)(?i:\s+de\s+\w+)?\s*[:\-=]?\s*([A-Z0-9]{4,12})\b")
STOP = set("para como mais muito voce esta isso esse essa nosso ainda pelo pela tudo aqui entao onde quando".split())


# ---------------------------------------------------------------- vocabulário
def load_vocab(path=None):
    path = path or common.knowledge("vocabulary.md")
    vocab, cur = {k: [] for k in CATS}, None
    sugeridos = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if ln.startswith("## "):
            head = common.norm(ln[3:])
            cur = next((k for k, v in CATS.items() if common.norm(v) == head), "sugeridos" if "sugeridos" in head else None)
        elif ln.startswith("- ") and cur:
            (sugeridos if cur == "sugeridos" else vocab[cur]).append(ln[2:].strip())
    vocab["tema"] += sugeridos                       # termos sugeridos entram como tema (indicador fraco)
    return vocab


def find_terms(text, terms):
    t = common.norm(text)
    return sorted({x for x in terms if common.term_regex(x).search(t)})


def load_platforms(path=None):
    d = common.read_json(path or common.knowledge("platforms.json"), {}) or {}
    return d.get("platforms", []), [k.lower() for k in d.get("gambling_domain_keywords", [])]


def platform_for(host, platforms=None):
    platforms = platforms if platforms is not None else load_platforms()[0]
    for p in platforms:
        if any(host == d or host.endswith("." + d) for d in p.get("domains", [])):
            return p["name"]
    return None


def gambling_domain(host, platforms, keywords, learned_domains=()):
    """-> (bool, motivo). Só o que é sinal objetivo; hubs/encurtadores/redes/contatos nunca contam."""
    if not host or common.link_kind("https://" + host) != "outro":
        return False, None
    name = platform_for(host, platforms)
    if name:
        return True, f"domínio {host} é a plataforma {name} (platforms.json)"
    if common.registrable(host) in learned_domains or host in learned_domains:
        return True, f"domínio {host} já confirmado em perfil revisado (golden set)"
    kw = next((k for k in keywords if k in host), None)
    if kw:
        return True, f"domínio {host} contém a palavra-chave '{kw}'"
    return False, None


# ---------------------------------------------------------------- features
def text_codes(text):
    return [{"parameter": "codigo_texto", "value": m.group(1), "strength": "forte", "domain": "", "url": ""}
            for m in TEXT_CODE.finditer(str(text or "")) if re.search(r"\d", m.group(1)) or m.group(1).isupper()]


def build_features(meta, redirects, vocab=None, platforms=None, keywords=None, learned_domains=()):
    """meta: dict do perfil (bio, display_name, content[] ...). redirects: lista de resolve(). -> features."""
    vocab = vocab or load_vocab()
    if platforms is None:
        platforms, keywords = load_platforms()
    bio = meta.get("bio") or ""
    content = [str(c) for c in (meta.get("content") or [])]
    all_text = " ".join([bio, meta.get("display_name") or ""] + content)
    norm_all = NEG_SAFE.sub(" ", common.norm(all_text))
    hits = {k: find_terms(bio, v) for k, v in vocab.items()}
    hits_c = {k: find_terms(" ".join(content), v) for k, v in vocab.items()}
    hits_all = {k: sorted(set(hits[k]) | set(hits_c[k])) for k in vocab}
    for k in ("neg_jornalistico", "neg_denuncia", "neg_critico"):
        hits_all[k] = find_terms(norm_all, vocab[k])
    feats = {"hits_bio": hits, "hits_content": hits_c, "hits": hits_all, "hashtags": common.hashtags(all_text),
             "cpa_chines": bool(re.search(r"\bcpa\s+chin", common.norm(all_text))),
             "plataforma_chinesa": bool(re.search(r"\bplataformas?\s+chinesas?\b", common.norm(all_text))),
             "text_codes": text_codes(all_text)}
    aff, finals, platnames, shorts, hubs, contacts, landing, links_seen = [], [], [], [], [], [], [], []
    for r in redirects:
        for u in r.get("redirect_chain") or [r.get("short_url")]:
            aff += [{**a, "source": r.get("short_url")} for a in common.affiliate_params(u)]
        if r.get("kind") == "encurtador":
            shorts.append(common.host_of(r["short_url"]))
        if r.get("kind") == "hub":
            hubs.append(r["short_url"])
        if r.get("kind") in ("telegram", "whatsapp"):
            contacts.append(r["short_url"])
            continue
        if r.get("kind") == "social" or not r.get("final_domain"):
            continue
        fd = r["final_domain"]
        if common.link_kind(r["final_url"]) == "outro":
            finals.append(fd)
            ok, why = gambling_domain(fd, platforms, keywords, learned_domains)
            pn = platform_for(fd, platforms)
            if ok:
                links_seen.append({"domain": fd, "motivo": why, "short_url": r["short_url"], "final_url": r["final_url"],
                                   "erro": r.get("error")})
                platnames.append(pn or fd)
            lt = find_terms(r.get("final_title", "") + " " + r.get("final_text", ""), vocab["tema"] + vocab["cadastro"])
            if len(find_terms(r.get("final_title", "") + " " + r.get("final_text", ""), vocab["tema"])) >= 2:
                landing.append({"domain": fd, "termos": lt, "final_url": r["final_url"], "short_url": r["short_url"]})
    seen, uniq = set(), []
    for a in aff + feats["text_codes"]:
        k = (a["parameter"], a["value"])
        if k not in seen:
            seen.add(k)
            uniq.append(a)
    feats.update({"affiliate": uniq, "final_domains": sorted(set(finals)), "platforms": sorted(set(platnames)),
                  "gambling_links": links_seen, "landing_gambling": landing, "shorteners": sorted(set(shorts)),
                  "hubs": hubs, "contacts": contacts})
    return feats


# ---------------------------------------------------------------- pacote de evidência por perfil
def collect_links(meta):
    links, seen = [], set()
    for u in list(meta.get("bio_links") or []) + common.extract_urls(meta.get("bio") or ""):
        u = common.unwrap_tiktok(u)
        if u not in seen:
            seen.add(u)
            links.append({"url": u, "origem": "bio"})
    return links


def enrich_profile(meta, fetcher=None, resolve=True):
    """Coleta links (bio → hubs → encurtadores), resolve as cadeias e grava external_links/redirects/metadata.
    Reaproveita redirects.json já existente (retomada sem repetir requisições)."""
    user = meta["username"]
    ed = common.evidence_dir(user)
    cached = {r["short_url"]: r for r in (common.read_json(ed / "redirects.json", []) or [])}
    links = collect_links(meta)
    ext, redirects = [], []

    def do(url, origem):
        ext.append({"url": url, "origem": origem, "tipo": common.link_kind(url), "dominio": common.host_of(url)})
        kind = common.link_kind(url)
        if url in cached and not cached[url].get("error"):
            redirects.append(cached[url])
        elif kind in ("telegram", "whatsapp", "social") or not resolve:
            redirects.append({"short_url": url, "redirect_chain": [url], "final_url": url, "final_domain": common.host_of(url),
                              "status": None, "hops": 0, "error": None, "final_title": "", "final_text": "", "kind": kind})
        else:
            redirects.append(resolve_urls.resolve(url, fetcher))

    for l in links:
        do(l["url"], l["origem"])
        if common.link_kind(l["url"]) == "hub" and resolve:
            inner, err = resolve_urls.expand_hub(l["url"], fetcher)
            if err:
                ext[-1]["erro"] = err
            for u in inner[:15]:
                do(u, f"hub:{common.host_of(l['url'])}")
    common.write_json(ed / "external_links.json", ext)
    common.write_json(ed / "redirects.json", [{k: v for k, v in r.items() if k != "final_text"} | {"final_text": r.get("final_text", "")[:1500]} for r in redirects])
    common.write_json(ed / "metadata.json", {
        "username": user, "display_name": meta.get("display_name"), "followers": common.parse_count(meta.get("followers")),
        "following": common.parse_count(meta.get("following")), "likes": common.parse_count(meta.get("likes")),
        "bio": meta.get("bio"), "profile_url": meta.get("url_perfil"), "status": meta.get("status"),
        "private": meta.get("private"), "collected_at": meta.get("collected_at"), "content": meta.get("content") or [],
        "screenshot": meta.get("screenshot")})
    return redirects


# ---------------------------------------------------------------- expansão do vocabulário
def expand_vocabulary(pos_texts, neg_texts, path=None, min_count=2):
    """Acrescenta a 'Sugeridos (auto)' bigramas frequentes nos positivos e ausentes nos negativos e no vocabulário."""
    path = Path(path or common.knowledge("vocabulary.md"))
    vocab = load_vocab(path)
    known = {common.norm(t) for v in vocab.values() for t in v}

    def grams(texts):
        c = Counter()
        for t in texts:
            w = [x for x in re.findall(r"[a-z0-9]{3,}", common.norm(t)) if x not in STOP]
            c.update({" ".join(w[i:i + 2]) for i in range(len(w) - 1)})
        return c
    pos, neg = grams(pos_texts), grams(neg_texts)
    new = [g for g, n in pos.most_common(60) if n >= min_count and g not in known and g not in neg]
    if new:
        with open(path, "a", encoding="utf-8") as f:
            f.write("".join(f"- {g}\n" for g in new))
    return new


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--expand", action="store_true", help="sugere termos a partir de datasets/positivos e negativos")
    a = ap.parse_args(argv)
    if a.expand:
        def texts(f):
            return [" ".join([r.get("bio", ""), r.get("content", "")]) for r in common.read_csv(common.P("datasets", f))]
        print("termos sugeridos:", expand_vocabulary(texts("positivos_confirmados.csv"), texts("negativos_confirmados.csv")))


if __name__ == "__main__":
    main()
