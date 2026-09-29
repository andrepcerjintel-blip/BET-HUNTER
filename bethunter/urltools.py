"""Análise de URLs: parâmetros de afiliado, domínios, cadeia de redirecionamento e agregadores."""
import re
from urllib.parse import parse_qsl, urlparse, urljoin, unquote

from bs4 import BeautifulSoup

from . import config, net
from .util import base_domain, host_of, clip, norm

MAX_HOPS = 8


def parse_params(url):
    """Lista de parâmetros relevantes {param, value, type, dominio}. Valor preservado como encontrado."""
    try:
        p = urlparse(url)
    except ValueError:
        return []
    out = []
    dom = host_of(url)
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        t = config.AFF_PARAMS.get(k.lower())
        if t and v != "":
            out.append({"param": k, "value": v, "type": t, "dominio": dom})
    return out


def raw_query(url):
    try:
        return urlparse(url).query
    except ValueError:
        return ""


def domain_kind(host, settings):
    """aggregator | shortener | messenger | tiktok | other"""
    b = base_domain(host)
    def inl(lst):
        return any(host == d or host.endswith("." + d) or b == d for d in lst)
    if inl(settings["aggregators"]):
        return "aggregator"
    if inl(settings["shorteners"]):
        return "shortener"
    if inl(settings["messengers"]):
        return "messenger"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    return "other"


def unwrap(url):
    """Extrai destino de links de saída conhecidos (TikTok, Google, Facebook/Instagram)."""
    try:
        p = urlparse(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    q = dict(parse_qsl(p.query))
    if host.endswith("tiktok.com") and p.path.startswith("/link"):
        return q.get("target") or q.get("url")
    if host.endswith("google.com") and p.path == "/url":
        return q.get("q") or q.get("url")
    if host in ("l.facebook.com", "l.instagram.com", "lm.facebook.com"):
        return q.get("u")
    return None


def _visible_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    title = clip(soup.title.get_text(), 200) if soup.title else ""
    desc = ""
    m = soup.find("meta", attrs={"name": re.compile("^description$", re.I)}) or \
        soup.find("meta", attrs={"property": "og:description"})
    if m and m.get("content"):
        desc = clip(m["content"], 300)
    body = clip(soup.get_text(" "), 1500)
    return title, clip((desc + " " + body).strip(), 1600)


def _meta_refresh(html, base):
    m = re.search(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^"\']*url=([^"\'>\s]+)', html, re.I)
    return urljoin(base, m.group(1)) if m else None


def _outbound_links(html, base_url, limit):
    """Links externos de uma página agregadora (Linktree e similares)."""
    soup = BeautifulSoup(html, "html.parser")
    base_host = base_domain(host_of(base_url))
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        if not href.lower().startswith(("http://", "https://")):
            continue
        if base_domain(host_of(href)) == base_host:
            continue
        key = href.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(href)
        if len(out) >= limit:
            break
    return out


def follow(url, settings, depth=0):
    """Segue redirecionamentos. -> dict(chain=[urls intermediárias], final, error, html, status)."""
    chain = []
    cur = url
    last_html, last_status, err = "", None, None
    seen = set()
    for _ in range(MAX_HOPS):
        if cur in seen:
            err = "loop de redirecionamento"
            break
        seen.add(cur)
        w = unwrap(cur)
        if w:
            chain.append(cur)
            cur = w
            continue
        r = net.fetch(cur, timeout=10)
        if r.get("error"):
            err = r["error"]
            break
        last_status = r["status"]
        loc = r.get("location")
        if r["status"] and 300 <= r["status"] < 400 and loc:
            chain.append(cur)
            cur = urljoin(cur, loc)
            continue
        last_html = r.get("text") or ""
        mr = _meta_refresh(last_html, cur) if last_html else None
        if mr and mr != cur:
            chain.append(cur)
            cur = mr
            continue
        break
    return {"chain": chain, "final": cur, "error": err, "html": last_html, "status": last_status}


def expand_link(url, settings, depth=0):
    """Resolve a URL e devolve 1..N registros de link (N>1 quando passa por agregador).
    Registro: url_original, chain (intermediárias), url_final, domain_final, params, params_raw,
    page_title, page_text, aggregator, error."""
    res = follow(url, settings)
    final = res["final"]
    fhost = host_of(final)
    kind = domain_kind(fhost, settings)
    records = []
    all_urls = [url] + res["chain"] + [final]

    if kind == "aggregator" and res["html"] and depth == 0:
        outs = _outbound_links(res["html"], final, settings.get("aggregator_max_links", 10))
        for o in outs:
            for rec in expand_link_child(o, settings, url, res["chain"] + [final], fhost):
                records.append(rec)
        if records:
            return records
    title, text = ("", "")
    if res["html"] and kind not in ("aggregator", "messenger", "tiktok"):
        title, text = _visible_text(res["html"])
    params = []
    for u in all_urls:
        params.extend(parse_params(u))
    records.append({
        "url_original": url, "chain": res["chain"], "url_final": final, "domain_final": fhost,
        "params": _dedupe_params(params), "params_raw": raw_query(final) or raw_query(url),
        "page_title": title, "page_text": text,
        "aggregator": fhost if kind == "aggregator" else "", "error": res["error"] or "",
    })
    return records


def expand_link_child(child_url, settings, origin_url, parent_chain, agg_host):
    """Resolve link de saída de um agregador; a cadeia registra o agregador como intermediário."""
    res = follow(child_url, settings)
    final = res["final"]
    fhost = host_of(final)
    kind = domain_kind(fhost, settings)
    title, text = ("", "")
    if res["html"] and kind not in ("aggregator", "messenger", "tiktok"):
        title, text = _visible_text(res["html"])
    params = []
    for u in [origin_url] + parent_chain + [child_url] + res["chain"] + [final]:
        params.extend(parse_params(u))
    yield {
        "url_original": origin_url,
        "chain": parent_chain + ([child_url] if child_url != final else []) + res["chain"],
        "url_final": final, "domain_final": fhost, "params": _dedupe_params(params),
        "params_raw": raw_query(final) or raw_query(child_url),
        "page_title": title, "page_text": text, "aggregator": agg_host, "error": res["error"] or "",
    }


def _dedupe_params(params):
    out, seen = [], set()
    for p in params:
        k = (p["param"].lower(), p["value"], p["dominio"])
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def bet_link_level(link, settings, bet_page_terms):
    """Classifica o destino: known | page | hint_strong | hint_weak | None."""
    host = link.get("domain_final") or ""
    b = base_domain(host)
    if not host:
        return None
    known = [d.lower() for d in settings.get("bet_domains", [])]
    for p in settings.get("platforms", []):
        parts = p.split("|", 1)
        if len(parts) == 2:
            known += [d.strip().lower() for d in parts[1].split(",") if d.strip()]
    if any(host == d or host.endswith("." + d) or b == d for d in known):
        return "known"
    plat_names = [norm(p.split("|")[0]).replace(" ", "") for p in settings.get("platforms", [])]
    if any(len(n) >= 5 and n in norm(host).replace("-", "") for n in plat_names):
        return "known"
    if host.endswith(".bet.br") or host.endswith(".bet") or re.search(
            r"(cassino|casino|tigrinho|aviator|jackpot|apostas?|slots?)", host):
        return "hint_strong"
    text = norm((link.get("page_title") or "") + " " + (link.get("page_text") or ""))
    if text:
        from .util import find_terms
        hits = find_terms(text, bet_page_terms)
        if len(hits) >= 3:
            return "page"
    if re.search(r"(^|[.\-])(bet|bets)([.\-]|$)|bet(s)?\.", host) or re.search(r"[a-z0-9]{3,}bets?\.", host + "."):
        return "hint_weak"
    return None
