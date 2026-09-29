"""Provider: TIKTOK COMMERCIAL CONTENT API (fonte adicional, OPCIONAL — mesma interface dos demais providers).

Base documental usada (informada pelo projeto): endpoint POST https://open.tiktokapis.com/v2/research/adlib/ad/query/,
consulta com `fields=ad.id`, filtros `ad_published_date_range` e `country_code`, `search_term`, `max_count` e paginação por
`has_more` + `search_id`. Nada além disso é assumido:
  * só o campo `ad.id` é solicitado por padrão (`commercial_api_fields`); o payload bruto de cada item fica preservado;
  * a URL do endpoint de TOKEN não consta na base documental do projeto -> `commercial_api_token_url` (CONFIG) começa
    vazia e a autenticação fica "pendente de configuração" até o analista informar a URL oficial;
  * credenciais só por variável de ambiente (TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET); o token vive só em memória.
Pontos marcados CONFIRMAR devem ser verificados na documentação oficial na execução local."""
import logging
import os
import time
from datetime import date, datetime, timedelta

from . import db, net
from .util import norm

log = logging.getLogger("bethunter")

ENDPOINT = "https://open.tiktokapis.com/v2/research/adlib/ad/query/"
SOURCE = "TIKTOK_COMMERCIAL_CONTENT_API"
SOURCE_TYPE = "CONTEUDO_COMERCIAL"
EVIDENCE_TEXT = "Resultado identificado por meio da TikTok Commercial Content API."
CANDIDATE_PREFIX = "ad:"        # ':' não existe em usernames do TikTok -> nunca colide com perfil real
NOISE_TERMS = {"bet", "bets", "aposta", "apostas"}   # ruído alto: nunca como termo principal isolado
RANGES = {"hoje": 0, "24h": 1, "3d": 3, "7d": 7}
_token = {"value": None, "exp": 0.0}                # somente memória


# ------------------------------------------------------------------ estado / credenciais
def credentials():
    return ((os.environ.get("TIKTOK_CLIENT_KEY") or "").strip(), (os.environ.get("TIKTOK_CLIENT_SECRET") or "").strip())


def configured():
    k, s = credentials()
    return bool(k and s)


def _settings():
    with db.connect() as c:
        return db.get_settings(c)


def usable(s=None):
    """Credenciais presentes e fonte habilitada em CONFIG."""
    s = s or _settings()
    return configured() and bool(s.get("commercial_api_enabled", True))


def static_state(s=None):
    """Sem rede -> (estado, detalhe)."""
    s = s or _settings()
    if not configured():
        return "NÃO CONFIGURADA", "opcional: TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET no .env"
    if not s.get("commercial_api_enabled", True):
        return "NÃO CONFIGURADA", "desativada em CONFIG (commercial_api_enabled)"
    if not (s.get("commercial_api_token_url") or "").strip():
        return "CREDENCIAIS ENCONTRADAS", "autenticação pendente de configuração: informe em CONFIG a URL do endpoint de token da documentação oficial"
    return "CREDENCIAIS ENCONTRADAS", "use TESTAR AMBIENTE para validar autenticação e permissão"


# ------------------------------------------------------------------ autenticação
def authenticate(s=None, force=False):
    """-> (token|None, estado, mensagem). Nunca devolve/loga credenciais."""
    s = s or _settings()
    if not configured():
        return None, "NÃO CONFIGURADA", "credenciais ausentes"
    if not force and _token["value"] and time.time() < _token["exp"]:
        return _token["value"], "OK", ""
    url = (s.get("commercial_api_token_url") or "").strip()
    if not url:
        return None, "CREDENCIAIS ENCONTRADAS", "autenticação pendente de configuração (URL do endpoint de token não informada em CONFIG)"
    key, secret = credentials()
    # CONFIRMAR: nomes/formato do corpo do token conforme a documentação oficial
    r = net.post(url, headers={"Content-Type": "application/x-www-form-urlencoded"},
                 data={"client_key": key, "client_secret": secret, "grant_type": s.get("commercial_api_grant_type", "client_credentials")})
    st = r["status"]
    if r["error"]:
        return None, "ERRO", r["error"]
    if st == 429:
        return None, "RATE LIMITED", "HTTP 429 no endpoint de token"
    if st is None or st >= 500:
        return None, "ERRO", f"HTTP {st} no endpoint de token"
    j = r["json"] if isinstance(r["json"], dict) else {}
    tok = j.get("access_token")
    if st != 200 or not tok:
        return None, "AUTENTICAÇÃO FALHOU", f"HTTP {st}" + (f" ({j['error']})" if isinstance(j.get("error"), str) else "")
    try:
        ttl = float(j.get("expires_in")) - 60
    except (TypeError, ValueError):
        ttl = 3000.0
    _token["value"], _token["exp"] = tok, time.time() + max(ttl, 30)
    return tok, "OK", ""


def reset_token():
    _token["value"], _token["exp"] = None, 0.0


# ------------------------------------------------------------------ consulta
def date_range(s, today=None):
    """-> (min 'AAAAMMDD', max 'AAAAMMDD'). Vem das configurações; nada fixo no código."""
    today = today or date.today()
    dfrom = dto = None
    if s.get("commercial_api_range") == "custom":
        try:
            dfrom = datetime.strptime(s.get("commercial_api_date_from") or "", "%Y-%m-%d").date()
            dto = datetime.strptime(s.get("commercial_api_date_to") or "", "%Y-%m-%d").date()
        except ValueError:
            dfrom = dto = None
    if not (dfrom and dto):
        dto, dfrom = today, today - timedelta(days=RANGES.get(s.get("commercial_api_range"), 7))
    if dfrom > dto:
        dfrom, dto = dto, dfrom
    return dfrom.strftime("%Y%m%d"), dto.strftime("%Y%m%d")   # CONFIRMAR: formato da data na documentação


def build_body(term, country, dr, max_count, search_id=None):
    body = {"filters": {"ad_published_date_range": {"min": dr[0], "max": dr[1]}, "country_code": country},
            "search_term": term, "max_count": max_count}
    if search_id:
        body["search_id"] = search_id
    return body


def _find(obj, key):
    """Primeiro valor de `key` em dict aninhado (has_more/search_id)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find(v, key)
            if r is not None:
                return r
    return None


def _items(j):
    """Lista de itens de anúncio: primeira lista de dicts encontrada em `data`."""
    data = j.get("data") if isinstance(j, dict) else None
    for v in (data.values() if isinstance(data, dict) else []):
        if isinstance(v, list) and (not v or isinstance(v[0], dict)):
            return v
    return []


def _ad_id(item):
    ad = item.get("ad")
    val = ad.get("id") if isinstance(ad, dict) else item.get("ad.id", item.get("id"))
    return str(val) if val not in (None, "") else None


def _hit(item, ad_id, term, country, dr):
    return {"commercial": True, "ad_id": ad_id, "raw": item, "term": term, "country": country,
            "date_range": f"{dr[0]}-{dr[1]}", "username": None, "profile_url": "", "video_url": "", "text": "",
            "source": SOURCE, "query": term, "source_url": ENDPOINT}


def search(q, settings=None, stop=None):
    """Executa a consulta com paginação. -> (hits, erro|None, endpoint). Resultados parciais são sempre devolvidos."""
    s = settings or _settings()
    term = (q or "").strip().strip('"').strip()
    if not usable(s):
        return [], "TikTok Commercial API NÃO CONFIGURADA", ENDPOINT
    if not term or norm(term) in NOISE_TERMS:
        log.info("FONTE=%s | TERMO=%s | ignorado (termo genérico isolado)", SOURCE, term)
        return [], None, ENDPOINT
    if stop is None:
        from . import pipeline
        stop = pipeline.any_cancel
    token, est, msg = authenticate(s)
    if not token:
        log.warning("FONTE=%s | TERMO=%s | ERRO=%s: %s", SOURCE, term, est, msg)
        return [], f"{est}: {msg}", ENDPOINT
    country = (s.get("commercial_api_country") or "BR").strip().upper()
    dr = date_range(s)
    max_pages = max(1, int(s.get("commercial_api_max_pages", 20)))
    mc = max(1, int(s.get("commercial_api_max_count", 20)))
    fields = (s.get("commercial_api_fields") or "ad.id").strip()
    hits, seen, sid, page = [], set(), None, 0
    reauth = rl = e5 = reduced = 0

    def fail(status, text):
        log.warning("FONTE=%s | TERMO=%s | HTTP=%s | ERRO=%s", SOURCE, term, status, text)
        return hits, (f"{text} (HTTP {status})" if status else text), ENDPOINT

    while page < max_pages:
        if stop():
            return hits, "interrompida pelo usuário", ENDPOINT
        r = net.post(ENDPOINT, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                     params={"fields": fields}, json_body=build_body(term, country, dr, mc, sid))
        st = r["status"]
        if r["error"]:
            return fail(None, r["error"])
        if st == 401:
            if reauth:
                return fail(st, "AUTENTICAÇÃO FALHOU")
            reauth += 1
            reset_token()
            token, est, msg = authenticate(s, force=True)
            if not token:
                return fail(st, f"{est}: {msg}")
            continue
        if st == 403:
            return fail(st, "SEM PERMISSÃO PARA O ENDPOINT")
        if st == 429:                      # retry conservador: 1 nova tentativa, espera limitada
            if rl >= 1:
                return fail(st, "RATE LIMITED")
            rl += 1
            time.sleep(min(r["retry_after"] or 5.0, 30.0))
            continue
        if st is not None and st >= 500:
            if e5 >= 1:
                return fail(st, "ERRO do servidor da API")
            e5 += 1
            time.sleep(2.0)
            continue
        if st == 400 and mc > 1 and reduced < 3:   # possível limite de max_count: reduz de forma segura
            mc = max(1, mc // 2)
            reduced += 1
            log.warning("FONTE=%s | TERMO=%s | HTTP=400 | reduzindo max_count para %s", SOURCE, term, mc)
            continue
        if st != 200:
            return fail(st, "requisição recusada" if st == 400 else "ERRO")
        j = r["json"]
        if not isinstance(j, dict):
            return fail(st, "resposta inválida")
        items = _items(j)
        for it in items:
            aid = _ad_id(it) if isinstance(it, dict) else None
            if aid and aid not in seen:
                seen.add(aid)
                hits.append(_hit(it, aid, term, country, dr))
        page += 1
        has_more = _find(j, "has_more")
        nsid = _find(j, "search_id")
        if not has_more:
            break
        if not nsid or (not items and nsid == sid):    # proteção contra loop infinito
            log.warning("FONTE=%s | TERMO=%s | paginação interrompida (search_id ausente/repetido)", SOURCE, term)
            break
        sid = nsid
    else:
        log.info("FONTE=%s | TERMO=%s | limite de %s páginas atingido", SOURCE, term, max_pages)
    return hits, None, ENDPOINT


# ------------------------------------------------------------------ TESTAR AMBIENTE
def check(s=None):
    """-> {estado, detalhe}. Uma autenticação + uma consulta mínima (max_count=1)."""
    s = s or _settings()
    est, det = static_state(s)
    if est != "CREDENCIAIS ENCONTRADAS" or not (s.get("commercial_api_token_url") or "").strip():
        return {"estado": est, "detalhe": det}
    token, est, msg = authenticate(s, force=True)
    if not token:
        return {"estado": est if est in ("AUTENTICAÇÃO FALHOU", "RATE LIMITED") else "ERRO", "detalhe": msg}
    term = (s.get("commercial_api_terms") or ["cassino"])[0]
    r = net.post(ENDPOINT, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=8,
                 params={"fields": (s.get("commercial_api_fields") or "ad.id").strip()},
                 json_body=build_body(term, (s.get("commercial_api_country") or "BR").upper(), date_range(s), 1))
    st = r["status"]
    if r["error"]:
        return {"estado": "ERRO", "detalhe": r["error"]}
    if st == 200 and isinstance(r["json"], dict):
        return {"estado": "OK", "detalhe": "autenticação e endpoint de consulta respondendo"}
    return {"estado": {401: "AUTENTICAÇÃO FALHOU", 403: "SEM PERMISSÃO PARA O ENDPOINT", 429: "RATE LIMITED"}.get(st, "ERRO"),
            "detalhe": f"HTTP {st}"}
