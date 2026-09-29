"""Fontes de descoberta/coleta. Toda fonte pode falhar (bloqueio, JS, captcha): nesse caso devolve
erro registrado e o restante da ferramenta segue funcionando (importação manual, evidência manual...)."""
import importlib.util
import json
import logging
import re
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from bs4 import BeautifulSoup

from . import net
from .util import (canonical_video_url, clip, extract_hashtags, extract_mentions, parse_tiktok_url, profile_url)

log = logging.getLogger("bethunter")
SOURCE_LABELS = {"ddg": "DuckDuckGo", "bing": "Bing", "tiktok": "TikTok Search", "tiktok_tag": "TikTok Hashtag",
                 "commercial": "TIKTOK_COMMERCIAL_CONTENT_API", "tiktok_local": "TIKTOK_SEARCH_LOCAL"}
SEARCH_SOURCES = ["tiktok_local", "ddg", "bing", "tiktok"]
RETRY_AFTER = {}    # rótulo da fonte -> segundos pedidos pelo servidor em HTTP 429 (a missão respeita ao pausar a fonte)
# Prioridade de execução (menor = primeiro). tiktok_playwright: reservado, não implementado.
SOURCE_PRIORITY = ["tiktok_local", "commercial", "tiktok_playwright", "bing", "ddg", "tiktok", "tiktok_tag"]


def by_priority(srcs):
    """Ordena fontes pela prioridade oficial; fontes desconhecidas vão ao fim (ordem original preservada)."""
    rank = {s: i for i, s in enumerate(SOURCE_PRIORITY)}
    return sorted(dict.fromkeys(srcs), key=lambda s: rank.get(s, len(rank)))


BLOCK_RX = re.compile(r"(captcha|unusual traffic|verify you are human|are you a robot|anomaly|access denied|"
                      r"confirm you.re not a robot)", re.I)


def http_error(status):
    """Mensagem curta para status HTTP; bloqueios são registrados, nunca contornados."""
    if status in (202, 403, 429):
        return f"HTTP {status} (bloqueio ou limite de requisições da fonte)"
    return f"HTTP {status}"


def _hit(url, text, source, query, source_url):
    r = parse_tiktok_url(url)
    if not r:
        return None
    return {"username": r["username"], "profile_url": profile_url(r["username"]),
            "video_url": canonical_video_url(url) if r["video_id"] else "",
            "text": clip(text, 600), "source": source, "query": query, "source_url": source_url}


def build_engine_query(q):
    q = q.strip()
    return q if "site:" in q else f"site:tiktok.com {q}"


def _ddg_target(href):
    if "uddg=" in href:
        return unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
    return href


def search_ddg(q):
    url = "https://html.duckduckgo.com/html/"
    r = net.fetch(url, params={"q": build_engine_query(q)}, timeout=15, allow_redirects=True,
                  headers={"Referer": "https://duckduckgo.com/"})
    src_url = f"{url}?q={quote_plus(build_engine_query(q))}"
    if r["error"]:
        return [], r["error"], src_url
    if r["status"] != 200:
        return [], http_error(r["status"]), src_url
    soup = BeautifulSoup(r["text"], "html.parser")
    hits = []
    for res in soup.select(".result, .web-result"):
        a = res.select_one("a.result__a") or res.select_one("a[href]")
        if not a:
            continue
        target = _ddg_target(a.get("href", ""))
        sn = res.select_one(".result__snippet")
        text = (a.get_text(" ") + " — " + (sn.get_text(" ") if sn else "")).strip()
        h = _hit(target, text, "DuckDuckGo", q, src_url)
        if h:
            hits.append(h)
    if not hits and BLOCK_RX.search(r["text"][:8000]):
        return [], "bloqueado por captcha/anti-bot", src_url
    return hits, None, src_url


def search_bing(q):
    url = "https://www.bing.com/search"
    r = net.fetch(url, params={"q": build_engine_query(q), "setlang": "pt-BR", "count": 50}, timeout=15,
                  allow_redirects=True)
    src_url = f"{url}?q={quote_plus(build_engine_query(q))}"
    if r["error"]:
        return [], r["error"], src_url
    if r["status"] != 200:
        return [], http_error(r["status"]), src_url
    soup = BeautifulSoup(r["text"], "html.parser")
    hits = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a:
            continue
        cap = li.select_one(".b_caption p, p")
        text = a.get_text(" ") + " — " + (cap.get_text(" ") if cap else "")
        h = _hit(a.get("href", ""), text, "Bing", q, src_url)
        if h:
            hits.append(h)
    if not hits and BLOCK_RX.search(r["text"][:8000]):
        return [], "bloqueado por captcha/anti-bot", src_url
    return hits, None, src_url


def _rehydration(html):
    m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {})
    except ValueError:
        return None


def search_tiktok(q, tag=False):
    """Busca pública do TikTok. Costuma exigir JS/assinatura; tenta o JSON embutido e, se não houver, falha com aviso."""
    if tag or q.startswith("#"):
        url = f"https://www.tiktok.com/tag/{quote_plus(q.lstrip('#').lower())}"
    else:
        url = f"https://www.tiktok.com/search/video?q={quote_plus(q)}"
    r = net.fetch(url, timeout=15, allow_redirects=True)
    if r["error"]:
        return [], r["error"], url
    if r["status"] != 200:
        return [], http_error(r["status"]), url
    scope = _rehydration(r["text"])
    hits = []
    if scope:
        def walk(o):
            if isinstance(o, dict):
                if "desc" in o and isinstance(o.get("author"), (dict, str)) and o.get("id"):
                    au = o["author"]
                    uname = au.get("uniqueId") if isinstance(au, dict) else au
                    if uname:
                        vurl = f"https://www.tiktok.com/@{uname}/video/{o['id']}"
                        h = _hit(vurl, o.get("desc", ""), "TikTok Search", q, url)
                        if h:
                            hits.append(h)
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(scope)
    # fallback: links /@user/video/ no HTML
    if not hits:
        for m in re.finditer(r'https://www\.tiktok\.com/@[A-Za-z0-9._]{2,24}/video/\d+', r["text"]):
            h = _hit(m.group(0), "", "TikTok Search", q, url)
            if h:
                hits.append(h)
    if not hits:
        if scope:      # página estruturada carregou e não trouxe itens: consulta válida sem resultados
            return [], None, url
        return [], "TikTok não retornou resultados públicos (exige JS/login) — use importação ou buscadores", url
    return hits, None, url


def run_source(source, q):
    if source == "ddg":
        return search_ddg(q)
    if source == "bing":
        return search_bing(q)
    if source == "tiktok":
        return search_tiktok(q)
    if source == "tiktok_tag":
        return search_tiktok(q, tag=True)
    if source == "commercial":       # provider oficial, mesma interface: (hits, erro, url)
        from . import commercial
        return commercial.search(q)
    if source == "tiktok_local":     # serviço local externo (HTTP em 127.0.0.1)
        from . import tiktok_local
        return tiktok_local.search(q)
    return [], f"fonte desconhecida: {source}", ""


def manual_search_urls(q):
    """Links para o analista abrir manualmente quando a fonte automática estiver indisponível."""
    eq = quote_plus(build_engine_query(q))
    return [
        {"fonte": "TikTok (vídeos)", "url": f"https://www.tiktok.com/search/video?q={quote_plus(q)}"},
        {"fonte": "TikTok (usuários)", "url": f"https://www.tiktok.com/search/user?q={quote_plus(q)}"},
        {"fonte": "Google", "url": f"https://www.google.com/search?q={eq}&num=50"},
        {"fonte": "Bing", "url": f"https://www.bing.com/search?q={eq}"},
        {"fonte": "DuckDuckGo", "url": f"https://duckduckgo.com/?q={eq}"},
    ]


# ------------------------------------------------------------------ perfil / vídeo

def _video_from_item(it, uname):
    vid = it.get("id")
    desc = it.get("desc") or ""
    tags = [t.get("hashtagName", "").lower() for t in (it.get("textExtra") or []) if t.get("hashtagName")]
    ments = [t.get("userUniqueId", "").lower() for t in (it.get("textExtra") or []) if t.get("userUniqueId")]
    return {"url": f"https://www.tiktok.com/@{uname}/video/{vid}" if vid else "", "desc": desc,
            "hashtags": sorted(set(tags + extract_hashtags(desc))),
            "mentions": sorted(set(ments + extract_mentions(desc)))}


def fetch_profile(username, max_videos=12):
    """-> {ok, error, unavailable, user_id, display_name, bio, bio_link, videos:[{url,desc,hashtags,mentions}]}"""
    url = profile_url(username)
    out = {"ok": False, "error": None, "unavailable": False, "user_id": "", "display_name": "", "bio": "",
           "bio_link": "", "videos": [], "source_url": url}
    r = net.fetch(url, timeout=15, allow_redirects=True)
    if r["error"]:
        out["error"] = r["error"]
    elif r["status"] == 404:
        out["unavailable"], out["error"] = True, "perfil não encontrado (404)"
    elif r["status"] != 200:
        out["error"] = http_error(r["status"])
    else:
        scope = _rehydration(r["text"])
        detail = (scope or {}).get("webapp.user-detail")
        if detail:
            if detail.get("statusCode") in (10221, 10222, 10202):
                out["unavailable"], out["error"] = True, "perfil indisponível/inexistente"
            ui = (detail.get("userInfo") or {})
            u = ui.get("user") or {}
            if u:
                out.update(ok=True, user_id=str(u.get("id") or ""), display_name=u.get("nickname") or "",
                           bio=u.get("signature") or "", bio_link=((u.get("bioLink") or {}).get("link")) or "")
            for it in (detail.get("itemList") or ui.get("itemList") or [])[:max_videos]:
                out["videos"].append(_video_from_item(it, username))
        else:
            out["error"] = "página sem dados estruturados (possível bloqueio/JS)"
    if len(out["videos"]) == 0:
        vids = ytdlp_videos(username, max_videos)
        if vids:
            out["videos"] = vids
            out["ok"] = out["ok"] or True
    return out


def ytdlp_cmd():
    """Comando do yt-dlp (executável no PATH ou módulo Python) ou None se não estiver instalado."""
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    return None


def ytdlp_videos(username, n=12):
    """Opcional: se yt-dlp estiver instalado, lista vídeos públicos do perfil (metadados apenas)."""
    cmd = ytdlp_cmd()
    if not cmd:
        return []
    try:
        p = subprocess.run([*cmd, "-J", "--flat-playlist", "--playlist-end", str(n), profile_url(username)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        data = json.loads(p.stdout or "{}")
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        log.warning("yt-dlp falhou para @%s: %s", username, e)
        return []
    out = []
    for e in data.get("entries") or []:
        desc = e.get("description") or e.get("title") or ""
        url = e.get("url") or e.get("webpage_url") or ""
        if url and desc:
            out.append({"url": canonical_video_url(url), "desc": desc, "hashtags": extract_hashtags(desc),
                        "mentions": extract_mentions(desc)})
    return out


def fetch_video(url):
    """Legenda de um vídeo público (página -> JSON embutido; fallback oEmbed)."""
    out = {"ok": False, "error": None, "desc": "", "author": "", "hashtags": [], "mentions": [], "thumbnail": ""}
    r = net.fetch(url, timeout=15, allow_redirects=True)
    if not r["error"] and r["status"] == 200:
        scope = _rehydration(r["text"]) or {}
        st = ((scope.get("webapp.video-detail") or {}).get("itemInfo") or {}).get("itemStruct")
        if st:
            v = _video_from_item(st, (st.get("author") or {}).get("uniqueId", ""))
            out.update(ok=True, desc=v["desc"], hashtags=v["hashtags"], mentions=v["mentions"],
                       author=(st.get("author") or {}).get("uniqueId", ""),
                       thumbnail=(st.get("video") or {}).get("cover") or (st.get("video") or {}).get("originCover") or "")
            return out
    o = net.fetch("https://www.tiktok.com/oembed", params={"url": url}, timeout=15, allow_redirects=True)
    if not o["error"] and o["status"] == 200:
        try:
            j = json.loads(o["text"])
            desc = j.get("title") or ""
            out.update(ok=True, desc=desc, hashtags=extract_hashtags(desc), mentions=extract_mentions(desc),
                       author=(j.get("author_unique_id") or ""), thumbnail=j.get("thumbnail_url") or "")
            return out
        except ValueError:
            pass
    out["error"] = r["error"] or o["error"] or f"HTTP {r['status']}/{o['status']}"
    return out
