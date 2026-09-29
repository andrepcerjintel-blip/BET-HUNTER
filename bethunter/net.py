"""Camada HTTP única (facilita testes e controle). Nunca levanta exceção: devolve dict com `error`."""
import ipaddress
import logging
import os
import socket
import threading
import time
from urllib.parse import urlparse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
           "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}


def _public_host(host):
    if os.environ.get("BETHUNTER_ALLOW_PRIVATE") == "1":
        return True
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
    except (socket.gaierror, ValueError):
        return True  # falha de DNS será reportada pelo requests
    return True


CFG = {"timeout": 12, "max_retries": 2, "min_interval": 0.7}
_lock = threading.Lock()
_last = [0.0]
log = logging.getLogger("bethunter.net")


def configure(timeout=None, max_retries=None, min_interval=None):
    """Aplica limites (vindos das configurações). Valores inválidos são ignorados."""
    for k, v in (("timeout", timeout), ("max_retries", max_retries), ("min_interval", min_interval)):
        if v is not None:
            try:
                CFG[k] = max(0, float(v)) if k != "max_retries" else max(0, min(int(v), 5))
            except (TypeError, ValueError):
                pass


def _throttle():
    """Limite conservador: uma requisição externa por vez, com intervalo mínimo entre elas."""
    with _lock:
        wait = CFG["min_interval"] - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def short_error(e):
    if isinstance(e, requests.Timeout):
        return "tempo esgotado"
    if isinstance(e, requests.ConnectionError):
        return "falha de conexão (sem internet, DNS, proxy ou bloqueio de rede)"
    return f"erro de rede ({type(e).__name__})"


def _request(method, url, timeout, throttle, **kw):
    """requests com timeout e até CFG['max_retries'] novas tentativas (só para falha de conexão/timeout)."""
    attempts = 1 + int(CFG["max_retries"])
    last = None
    for n in range(attempts):
        if throttle:
            _throttle()
        try:
            return requests.request(method, url, timeout=(min(timeout, 6), timeout), **kw), None
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            log.warning("tentativa %d/%d falhou: %s %s | %s", n + 1, attempts, method, url, e)
            if n + 1 < attempts:
                time.sleep(min(1.0 * (n + 1), 3))
        except requests.RequestException as e:
            return None, e
    return None, last


def fetch(url, method="GET", allow_redirects=False, timeout=None, headers=None, max_bytes=400_000, params=None,
          throttle=True):
    """-> {'status': int|None, 'url': str, 'location': str|None, 'text': str, 'error': str|None}. Nunca levanta exceção."""
    timeout = timeout or CFG["timeout"]
    out = {"status": None, "url": url, "location": None, "text": "", "error": None}
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        out["error"] = "esquema/URL inválido"
        return out
    if not _public_host(p.hostname):
        out["error"] = "host privado bloqueado"
        return out
    h = dict(HEADERS)
    h.update(headers or {})
    r, err = _request(method, url, timeout, throttle, headers=h, allow_redirects=allow_redirects, stream=True, params=params)
    if r is None:
        out["error"] = short_error(err)
        return out
    try:
        out["status"] = r.status_code
        out["url"] = r.url
        out["location"] = r.headers.get("Location")
        if method != "HEAD":
            raw, t0 = b"", time.time()
            for chunk in r.iter_content(16384):
                raw += chunk
                if len(raw) >= max_bytes or time.time() - t0 > timeout * 2:
                    break
            out["text"] = raw.decode(r.encoding or "utf-8", errors="replace")
    except requests.RequestException as e:
        log.warning("leitura interrompida: %s | %s", url, e)
        out["error"] = short_error(e)
    finally:
        r.close()
    return out


def fetch_bytes(url, max_bytes=800_000, timeout=None):
    """Baixa binário (thumbnail). -> bytes | None. Mesmas proteções de fetch()."""
    timeout = timeout or CFG["timeout"]
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname or not _public_host(p.hostname):
        return None
    r, _ = _request("GET", url, timeout, True, headers=HEADERS, stream=True)
    if r is None or r.status_code != 200:
        return None
    try:
        raw, t0 = b"", time.time()
        for chunk in r.iter_content(16384):
            raw += chunk
            if len(raw) > max_bytes or time.time() - t0 > timeout * 2:
                return None
        return raw
    except requests.RequestException:
        return None
    finally:
        r.close()
