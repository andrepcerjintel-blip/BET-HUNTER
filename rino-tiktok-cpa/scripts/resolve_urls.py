"""Resolve cadeias de redirecionamento (encurtadores) e expande hubs de links. Somente GET passivo:
nunca preenche formulário, cadastra, deposita ou contorna bloqueio (403/429 são registrados, não burlados)."""
import argparse
import ipaddress
import json
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "pt-BR,pt;q=0.9"}
META_REFRESH = re.compile(r"http-equiv=[\"']?refresh[\"']?[^>]*content=[\"']?\s*\d+\s*;\s*url=([^\"'>\s]+)", re.I)


def _public(host):
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
    except (socket.gaierror, ValueError):
        return True
    return True


def default_fetcher(url, timeout=12):
    """Uma requisição, sem seguir redirecionamento. -> {'status','location','text','error'}"""
    import requests
    out = {"status": None, "location": None, "text": "", "error": None}
    host = common.host_of(url)
    if not url.lower().startswith(("http://", "https://")) or not host:
        out["error"] = "URL inválida"
        return out
    if not _public(host):
        out["error"] = "host privado bloqueado"
        return out
    try:
        r = requests.get(url, headers=UA, timeout=(6, timeout), allow_redirects=False, stream=True)
    except requests.Timeout:
        out["error"] = "tempo esgotado"
        return out
    except requests.RequestException as e:
        out["error"] = f"falha de rede ({type(e).__name__})"
        return out
    try:
        out["status"], out["location"] = r.status_code, r.headers.get("Location")
        raw = b""
        for chunk in r.iter_content(16384):
            raw += chunk
            if len(raw) >= 300_000:
                break
        out["text"] = raw.decode(r.encoding or "utf-8", errors="replace")
    except requests.RequestException as e:
        out["error"] = f"leitura interrompida ({type(e).__name__})"
    finally:
        r.close()
    return out


def visible_text(html, limit=3000):
    try:
        from bs4 import BeautifulSoup
        s = BeautifulSoup(html or "", "html.parser")
        for t in s(["script", "style", "noscript"]):
            t.decompose()
        title = (s.title.get_text(" ", strip=True) if s.title else "")
        return title, re.sub(r"\s+", " ", s.get_text(" ", strip=True))[:limit]
    except Exception:                                   # HTML malformado nunca derruba a coleta
        return "", ""


def resolve(url, fetcher=None, max_hops=10):
    """Segue a cadeia manualmente. Registra cada salto, erro e a página final (título + trecho de texto)."""
    fetcher = fetcher or default_fetcher
    chain, cur, err, status, text = [url], url, None, None, ""
    for _ in range(max_hops):
        r = fetcher(cur)
        status, text = r.get("status"), r.get("text") or ""
        if r.get("error"):
            err = r["error"]
            break
        nxt = None
        if status and 300 <= status < 400 and r.get("location"):
            nxt = urljoin(cur, r["location"])
        elif status == 200:
            m = META_REFRESH.search(text)
            if m:
                nxt = urljoin(cur, m.group(1))
        elif status in (401, 403, 429) or (status and status >= 400):
            err = f"HTTP {status} (não contornado)"
            break
        if not nxt:
            break
        if nxt in chain:
            err = "loop de redirecionamento"
            break
        chain.append(nxt)
        cur = nxt
    else:
        err = f"limite de {max_hops} saltos"
    final = chain[-1]
    title, vis = visible_text(text)
    return {"short_url": url, "redirect_chain": chain, "final_url": final, "final_domain": common.host_of(final),
            "status": status, "hops": len(chain) - 1, "error": err, "final_title": title, "final_text": vis,
            "kind": common.link_kind(url)}


def expand_hub(url, fetcher=None, limit=30):
    """Hub (Linktree, Beacons...): devolve links externos da página. Passivo."""
    fetcher = fetcher or default_fetcher
    r = fetcher(url)
    if r.get("error") or not r.get("text"):
        return [], r.get("error") or f"HTTP {r.get('status')}"
    try:
        from bs4 import BeautifulSoup
        hrefs = [urljoin(url, a["href"]) for a in BeautifulSoup(r["text"], "html.parser").find_all("a", href=True)]
    except Exception:
        hrefs = common.extract_urls(r["text"])
    own, out = common.registrable(common.host_of(url)), []
    for h in hrefs + common.extract_urls(r["text"]):
        h = common.unwrap_tiktok(h)
        if h.startswith("http") and common.registrable(common.host_of(h)) != own and h not in out:
            out.append(h)
    return out[:limit], None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url")
    a = ap.parse_args(argv)
    r = resolve(a.url)
    r.pop("final_text", None)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
