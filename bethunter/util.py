"""Utilitários: normalização de texto, datas, URLs e usernames."""
import re
import time
import unicodedata
from datetime import datetime
from urllib.parse import urlparse

NAO_IDENTIFICADO = "NÃO IDENTIFICADO"


def norm(s):
    """minúsculas + sem acentos (para casar léxicos)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def tz_label():
    return time.strftime("%Z") + " (UTC" + time.strftime("%z")[:3] + ":" + time.strftime("%z")[3:] + ")"


def split_iso(iso):
    """'2026-01-02T10:11:12-03:00' -> ('2026-01-02', '10:11:12', '-03:00')"""
    if not iso:
        return "", "", ""
    try:
        d = datetime.fromisoformat(iso)
        off = d.strftime("%z")
        off = (off[:3] + ":" + off[3:]) if off else ""
        return d.strftime("%Y-%m-%d"), d.strftime("%H:%M:%S"), off
    except ValueError:
        return iso, "", ""


def term_regex(terms):
    """Compila lista de termos (já sem acento ou não) em uma regex com fronteira de palavra.
    Sufixo '*' = prefixo (cadastr* casa cadastre, cadastrar...)."""
    parts = []
    for t in terms:
        t = norm(t).strip()
        if not t:
            continue
        wild = t.endswith("*")
        t = t.rstrip("*")
        p = re.escape(t).replace(r"\ ", r"\s+").replace(r"\-", r"[-\s]?")
        parts.append(p + ("[a-z0-9]*" if wild else ""))
    if not parts:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])")


_RX_CACHE = {}


def find_terms(text_norm, terms):
    """Retorna lista (única, ordem de aparição) dos trechos que casaram."""
    key = tuple(terms)
    rx = _RX_CACHE.get(key)
    if rx is None:
        rx = _RX_CACHE[key] = term_regex(terms) or re.compile(r"(?!x)x")
    out, seen = [], set()
    for m in rx.finditer(text_norm or ""):
        g = re.sub(r"\s+", " ", m.group(0))
        if g not in seen:
            seen.add(g)
            out.append(g)
    return out


_TLDS = (r"com\.br|net\.br|org\.br|bet\.br|com|net|org|bet|io|me|co|br|app|link|site|xyz|vip|cc|ly|ee|to|gg|tv|"
         r"info|club|online|top|live|bio|fun|win|casino|games|store|click|page|cloud|one|pro|ws|lat|in")
URL_RE = re.compile(
    r"(?i)(?<![@\w.\-/])((?:https?://|www\.)[^\s<>\"'\)\]]+|"
    r"(?:[a-z0-9][a-z0-9\-]*\.)+(?:" + _TLDS + r")(?![a-z0-9\-])(?:/[^\s<>\"'\)\]]*)?)")


def extract_urls(text):
    """URLs (com ou sem esquema) de um texto; preserva o texto original."""
    out, seen = [], set()
    for m in URL_RE.finditer(text or ""):
        u = m.group(1).rstrip(".,;:!?)»\"'")
        if not u or len(u) < 4:
            continue
        full = u if re.match(r"(?i)https?://", u) else "https://" + u
        if full.lower() not in seen:
            seen.add(full.lower())
            out.append(full)
    return out


HASHTAG_RE = re.compile(r"#([\wÀ-ÿ]{2,60})", re.UNICODE)
MENTION_RE = re.compile(r"(?<![\w.])@([A-Za-z0-9._]{2,24})")


def extract_hashtags(text):
    out, seen = [], set()
    for m in HASHTAG_RE.finditer(text or ""):
        h = m.group(1).lower()
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def extract_mentions(text):
    out, seen = [], set()
    for m in MENTION_RE.finditer(text or ""):
        u = m.group(1).lower().rstrip(".")
        if len(u) >= 2 and u not in seen:
            seen.add(u)
            out.append(u)
    return out


USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{2,24}$")
TT_HOSTS = ("tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com")


def parse_tiktok_url(url):
    """-> dict(username, video_id, kind) ou None. kind: profile|video."""
    try:
        p = urlparse(url if "://" in url else "https://" + url)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if not (host == "tiktok.com" or host.endswith(".tiktok.com")):
        return None
    m = re.match(r"^/@([A-Za-z0-9._]{2,24})(?:/(video|photo)/(\d+))?", p.path)
    if not m:
        return None
    return {"username": m.group(1).lower(), "video_id": m.group(3),
            "kind": "video" if m.group(2) else "profile"}


def normalize_username(s):
    """'@Fulano', 'tiktok.com/@fulano/video/1', 'fulano' -> 'fulano' | None"""
    s = (s or "").strip()
    if not s:
        return None
    if "tiktok.com" in s.lower():
        r = parse_tiktok_url(s)
        return r["username"] if r else None
    s = s.lstrip("@").strip().lower()
    return s if USERNAME_RE.match(s) else None


def profile_url(username):
    return f"https://www.tiktok.com/@{username}"


def canonical_video_url(url):
    r = parse_tiktok_url(url)
    if r and r["video_id"]:
        return f"https://www.tiktok.com/@{r['username']}/video/{r['video_id']}"
    return url


_SLD = {"com", "net", "org", "gov", "edu", "co", "ac"}


def host_of(url):
    try:
        h = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def base_domain(host):
    """Aproximação simples de domínio registrável (xyz.com.br, xyz.bet.br, xyz.com)."""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if parts[-1] == "br" and parts[-2] in _SLD | {"bet"}:
        return ".".join(parts[-3:])
    if len(parts[-1]) == 2 and parts[-2] in _SLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def clip(s, n):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"
