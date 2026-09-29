"""Provider: TIKTOK_SEARCH_LOCAL — consome por HTTP local o serviço externo `axmedbek/tiktok-search-api`
(processo separado, outra porta; o RINO NÃO incorpora nem replica o código dele). Mesma interface dos demais providers:
`search(q) -> (hits, erro|None, endpoint)`.

Contrato usado (README/API_REFERENCE do projeto externo; nada além disso é enviado):
  POST {url}/search  {"type": keyword|hashtag|user, "query": str, "limit": int, "page_token"?: str,
                      "filters"?: {"sort_type": "0"|"1", "publish_time": "0"|"1"|"7"|"30"|"90"|"180"}}   (filters: só vídeo)
  resposta: {count, has_more, page_token|null, next_cursor (informativo), results:[...]}
  paginação: reenviar `page_token` (NUNCA next_cursor). Fim: page_token null / has_more false / count 0 (200 normal).
  GET {url}/health -> {"status": "ok", "capacity_remaining_today": n, ...}
  HTTP: 200 ok (results pode ser vazio) · 422 corpo/token inválido · 429 limite/rate limit · 502 TikTok recusou · 503 dispositivos ocupados.
Métricas de engajamento são só CONTEXTO (ficam no meta da evidência; nunca entram no score)."""
import json
import logging
import time

from . import db, net
from .util import USERNAME_RE, clip, profile_url

log = logging.getLogger("bethunter")

SOURCE = "TIKTOK_SEARCH_LOCAL"
SOURCE_TYPE = "BUSCA_NATIVA_TIKTOK"
RECENCY = {"24h": "1", "7d": "7", "30d": "30", "90d": "90", "180d": "180", "all": "0"}   # -> filters.publish_time
NOT_CONFIGURED, OFFLINE, OK, ERRO, RATE = "NÃO CONFIGURADO", "OFFLINE", "OK", "ERRO", "RATE LIMITED"


def _settings():
    with db.connect() as c:
        return db.get_settings(c)


def base_url(s):
    return (s.get("tiktok_search_local_url") or "").strip().rstrip("/")


def endpoint(s):
    return base_url(s) + "/search"


def classify_query(q):
    """'#tag' -> hashtag · '@user' -> user · resto -> keyword. Aspas de consulta booleana são removidas."""
    q = (q or "").strip()
    if q.startswith("#"):
        typ, q = "hashtag", q[1:]
    elif q.startswith("@"):
        typ, q = "user", q[1:]
    else:
        typ = "keyword"
    q = " ".join(q.replace('"', " ").split())[:200]
    return typ, q


# ------------------------------------------------------------------ disponibilidade
def check(s=None, timeout=4):
    """-> {'estado': NÃO CONFIGURADO|OFFLINE|OK|ERRO|RATE LIMITED, 'detalhe'}. Rápido; nunca levanta."""
    s = s or _settings()
    if not base_url(s):
        return {"estado": NOT_CONFIGURED, "detalhe": "informe a URL do serviço em CONFIG (padrão http://127.0.0.1:8000)"}
    old = net.CFG["max_retries"]
    net.CFG["max_retries"] = 0
    try:
        r = net.fetch(base_url(s) + "/health", timeout=timeout, throttle=False, allow_local=True)
    finally:
        net.CFG["max_retries"] = old
    if r["error"]:
        return {"estado": OFFLINE, "detalhe": f"serviço local não respondeu em {base_url(s)} ({r['error']})"}
    st = r["status"]
    if st == 429:
        return {"estado": RATE, "detalhe": "HTTP 429"}
    if st != 200:
        return {"estado": ERRO, "detalhe": f"HTTP {st} em /health"}
    try:
        j = json.loads(r["text"])
    except ValueError:
        return {"estado": ERRO, "detalhe": "/health devolveu resposta inválida (é o serviço certo?)"}
    if not isinstance(j, dict) or j.get("status") != "ok":
        return {"estado": ERRO, "detalhe": "/health não informou status ok"}
    rem = j.get("capacity_remaining_today")
    if isinstance(rem, int) and rem <= 0:
        return {"estado": RATE, "detalhe": "limite diário dos dispositivos do serviço atingido"}
    extra = f" (capacidade restante hoje: {rem})" if isinstance(rem, int) else ""
    return {"estado": OK, "detalhe": "TikTok Search Local disponível." + extra}


def usable(s=None):
    """Configurado (URL preenchida). A disponibilidade real é verificada por check()."""
    return bool(base_url(s or _settings()))


# ------------------------------------------------------------------ normalização
def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def normalize(rec, typ, term, s):
    """Registro do serviço -> hit do pipeline (ou None). URL do vídeo só quando username E id são válidos."""
    if not isinstance(rec, dict):
        return None
    ep = endpoint(s)
    if typ == "user":
        uname = str(rec.get("username") or "").strip().lower()
        if not USERNAME_RE.match(uname):
            return None
        name = rec.get("display_name") or ""
        meta = {"source": SOURCE, "source_type": SOURCE_TYPE, "search_type": "user", "term": term,
                "author_id": rec.get("user_id") or rec.get("id"), "display_name": name,
                "followers": _int(rec.get("follower_count")), "verified": bool(rec.get("verified")), "raw": rec}
        return {"username": uname, "profile_url": profile_url(uname), "video_url": "", "text": "", "hashtags": [],
                "source": SOURCE, "query": term, "source_url": ep, "meta": meta}
    uname = str(rec.get("author_username") or "").strip().lower().lstrip("@")
    if not USERNAME_RE.match(uname):
        return None                                   # sem author_username válido: não inventa perfil
    vid = str(rec.get("id") or "").strip()
    url = f"https://www.tiktok.com/@{uname}/video/{vid}" if vid.isdigit() else ""
    tags = [str(t).lower() for t in (rec.get("hashtags") or []) if isinstance(t, str) and t.strip()]
    meta = {"source": SOURCE, "source_type": SOURCE_TYPE, "search_type": typ, "term": term,
            "recency": s.get("tiktok_search_recency"), "video_id": vid or None, "author_id": rec.get("author_id"),
            "create_time": rec.get("create_time"), "region_code": rec.get("region_code"),
            "metrics": {"views": _int(rec.get("view_count")), "likes": _int(rec.get("like_count")),
                        "comments": _int(rec.get("comment_count")), "shares": _int(rec.get("share_count"))},
            "source_term": rec.get("source_term"), "raw": rec}
    return {"username": uname, "profile_url": profile_url(uname), "video_url": url,
            "text": str(rec.get("description") or ""), "hashtags": tags, "source": SOURCE, "query": term,
            "source_url": ep, "meta": meta}


# ------------------------------------------------------------------ consulta
def search(q, settings=None, stop=None):
    """Executa a consulta com paginação por page_token. -> (hits, erro|None, endpoint).
    Zero resultados (HTTP 200, results=[]) NÃO é erro. Resultados parciais são sempre devolvidos."""
    s = settings or _settings()
    ep = endpoint(s)
    if not base_url(s):
        return [], "TikTok Search Local NÃO CONFIGURADO", ep
    typ, term = classify_query(q)
    if not term:
        return [], None, ep
    if stop is None:
        from . import pipeline
        stop = pipeline.any_cancel
    body = {"type": typ, "query": term, "limit": max(1, int(s.get("tiktok_search_limit", 20)))}
    if typ != "user":                                   # filtros não são aceitos em busca de usuário (422)
        body["filters"] = {"sort_type": str(s.get("tiktok_search_sort", "0")),
                           "publish_time": RECENCY.get(s.get("tiktok_search_recency", "7d"), "7")}
    max_pages = max(1, int(s.get("tiktok_search_max_pages", 10)))
    timeout = float(s.get("tiktok_search_timeout", 40))
    hits, seen, token, page, rl, busy = [], set(), None, 0, 0, 0

    def fail(status, text):
        log.warning("FONTE=%s | TERMO=%s | HTTP=%s | ERRO=%s", SOURCE, term, status, text)
        return hits, (f"{text} (HTTP {status})" if status else text), ep

    while page < max_pages:
        if stop():
            return hits, "interrompida pelo usuário", ep
        b = dict(body)
        if token:
            b["page_token"] = token                     # mesma consulta + token; nunca next_cursor
        r = net.post(ep, json_body=b, timeout=timeout, allow_local=True)
        st = r["status"]
        if r["error"]:
            return fail(None, ("OFFLINE: " if "conexão" in r["error"] else "") + r["error"])
        if st == 429:
            if rl >= 1:
                if r["retry_after"]:
                    from . import sources
                    sources.RETRY_AFTER[sources.SOURCE_LABELS["tiktok_local"]] = r["retry_after"]
                return fail(st, "RATE LIMITED")
            rl += 1
            time.sleep(min(r["retry_after"] or 5.0, 30.0))
            continue
        if st == 503:
            if busy >= 1:
                return fail(st, "serviço ocupado (todos os dispositivos em uso)")
            busy += 1
            time.sleep(3.0)
            continue
        if st != 200:
            detail = ""
            if isinstance(r["json"], dict) and isinstance(r["json"].get("detail"), str):
                detail = ": " + clip(r["json"]["detail"], 90)
            return fail(st, {422: "requisição/page_token recusado", 502: "TikTok recusou/falhou a requisição"}.get(st, "ERRO") + detail)
        j = r["json"]
        if not isinstance(j, dict) or not isinstance(j.get("results"), list):
            return fail(st, "resposta inválida")
        for rec in j["results"]:
            h = normalize(rec, typ, term, s)
            key = (h["username"], h["video_url"] or (h["meta"].get("video_id") or "")) if h else None
            if h and key not in seen:
                seen.add(key)
                hits.append(h)
        page += 1
        nxt = j.get("page_token")
        if not nxt or not j.get("has_more") or not j["results"] or nxt == token:   # fim do stream / não retomável / guarda
            break
        token = nxt
    else:
        log.info("FONTE=%s | TERMO=%s | limite de %s páginas atingido", SOURCE, term, max_pages)
    return hits, None, ep
