"""Utilitários compartilhados: caminhos, CSV atômico, números, normalização de texto e de links."""
import csv
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

SKILL_DIR = Path(__file__).resolve().parent.parent


def data_root():
    """Raiz de datasets/ e output/. RINO_ROOT permite isolar execuções (testes, campanhas)."""
    return Path(os.environ.get("RINO_ROOT") or SKILL_DIR)


def P(*parts):
    return data_root().joinpath(*parts)


def knowledge(name):
    return SKILL_DIR / "knowledge" / name


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- CSV / JSON
def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    """Escrita atômica (arquivo temporário + replace): uma queda nunca deixa o CSV truncado."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append_csv(path, row, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in fields})


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def evidence_dir(username):
    d = P("output", "evidence", username)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- números
_SUFFIX = {"k": 1_000, "mil": 1_000, "m": 1_000_000, "mi": 1_000_000, "b": 1_000_000_000, "bi": 1_000_000_000}


def parse_count(value):
    """'238.2K'->238200, '1,2 mi'->1200000, '31.480'->31480, '-'/''/None->None (nunca assume zero)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower().replace("\xa0", " ")
    if s in ("", "-", "—", "–", "n/a", "null", "none"):
        return None
    m = re.fullmatch(r"([\d.,\s]+?)\s*(k|mil|m|mi|b|bi)?", s)
    if not m or not re.search(r"\d", m.group(1)):
        return None
    num, suf = m.group(1).replace(" ", ""), m.group(2)
    if suf:
        num = num.replace(",", ".")
        try:
            return int(round(float(num) * _SUFFIX[suf]))
        except ValueError:
            return None
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", num):          # separador de milhar
        return int(re.sub(r"[.,]", "", num))
    if re.fullmatch(r"\d+", num):
        return int(num)
    return None


# ---------------------------------------------------------------- texto
def norm(text):
    """minúsculas, sem acentos, espaços colapsados."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", t).strip()


def term_regex(term):
    return re.compile(r"(?<![a-z0-9])" + re.escape(norm(term)).replace(r"\ ", r"\s+") + r"(?![a-z0-9])")


def hashtags(text):
    return sorted({h.lower() for h in re.findall(r"#([\wÀ-ÿ]{2,40})", str(text or ""))})


# ---------------------------------------------------------------- links
SHORTENERS = {"encr.pw", "bit.ly", "tinyurl.com", "cutt.ly", "l1nq.com", "acesse.one", "encurtador.dev", "is.gd",
              "t.co", "rebrand.ly", "shorturl.at", "tiny.cc", "ow.ly", "goo.gl", "rb.gy", "s.id", "v.gd", "4br.me"}
HUBS = {"linktr.ee", "beacons.ai", "bio.link", "linkin.bio", "lnk.bio", "allmylinks.com", "taplink.cc",
        "campsite.bio", "carrd.co", "linkr.bio", "stan.store", "solo.to", "hoo.be"}
CONTACTS = {"t.me", "telegram.me", "telegram.org", "wa.me", "api.whatsapp.com", "chat.whatsapp.com", "whatsapp.com"}
SOCIAL = {"tiktok.com", "instagram.com", "youtube.com", "youtu.be", "facebook.com", "twitter.com", "x.com", "kwai.com"}
_SLD = {"com", "net", "org", "gov", "edu", "co"}

STRONG_AFF = {"inviter", "invite", "invite_code", "invitecode", "ref", "referral", "referrer", "refcode", "affiliate",
              "affiliate_id", "agent", "agentid", "partner", "promo", "pid"}
WEAK_AFF = {"uid", "source", "campaign", "utm_source", "utm_campaign", "utm_medium"}
# parâmetros que, sozinhos, já indicam convite/afiliação (ref/pid/promo exigem domínio de apostas)
DIRECT_AFF = {"inviter", "invite", "invite_code", "invitecode", "referral", "affiliate", "affiliate_id", "agent", "agentid"}

URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.I)
BARE_RE = re.compile(r"(?<![\w/@.])((?:%s)/[^\s<>\"'\)\]]*)" % "|".join(
    re.escape(d) for d in sorted(SHORTENERS | HUBS | {"t.me", "wa.me", "chat.whatsapp.com"}, key=len, reverse=True)), re.I)


def host_of(url):
    try:
        h = (urlparse(url if "//" in url else "//" + url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def registrable(host):
    parts = host.split(".")
    if len(parts) >= 3 and (parts[-2] in _SLD and len(parts[-1]) == 2):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def in_set(host, group):
    return any(host == d or host.endswith("." + d) for d in group)


def link_kind(url):
    h = host_of(url)
    if in_set(h, SHORTENERS):
        return "encurtador"
    if in_set(h, HUBS):
        return "hub"
    if in_set(h, CONTACTS):
        return "whatsapp" if "whatsapp" in h or h == "wa.me" else "telegram"
    if in_set(h, SOCIAL):
        return "social"
    return "outro"


def unwrap_tiktok(url):
    """O TikTok embrulha links de bio em tiktok.com/link/v2?...target=<url>. Desembrulha se houver `target`."""
    try:
        u = urlparse(url)
        if host_of(url).endswith("tiktok.com") and u.path.startswith("/link"):
            for k, v in parse_qsl(u.query):
                if k in ("target", "url") and v.startswith("http"):
                    return unquote(v)
    except ValueError:
        pass
    return url


def extract_urls(text):
    out, seen = [], set()
    t = str(text or "")
    cands = [m.group(0) for m in URL_RE.finditer(t)] + ["https://" + m.group(1) for m in BARE_RE.finditer(t)]
    for u in cands:
        u = unwrap_tiktok(u.rstrip(".,;:!?"))
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def affiliate_params(url):
    res = []
    try:
        u = urlparse(url)
    except ValueError:
        return res
    for k, v in parse_qsl(u.query, keep_blank_values=False) + parse_qsl(u.fragment.split("?")[-1] if "=" in u.fragment else ""):
        kl = k.lower()
        if v and (kl in STRONG_AFF or kl in WEAK_AFF):
            res.append({"parameter": kl, "value": v, "strength": "forte" if kl in STRONG_AFF else "fraco",
                        "domain": host_of(url), "url": url})
    return res
