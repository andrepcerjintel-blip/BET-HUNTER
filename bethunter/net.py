"""Camada HTTP única (facilita testes e controle). Nunca levanta exceção: devolve dict com `error`."""
import ipaddress
import os
import socket
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


def fetch(url, method="GET", allow_redirects=False, timeout=10, headers=None, max_bytes=400_000, params=None):
    """-> {'status': int|None, 'url': str, 'location': str|None, 'text': str, 'error': str|None}"""
    out = {"status": None, "url": url, "location": None, "text": "", "error": None}
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        out["error"] = "esquema/URL inválido"
        return out
    if not _public_host(p.hostname):
        out["error"] = "host privado bloqueado"
        return out
    try:
        h = dict(HEADERS)
        h.update(headers or {})
        r = requests.request(method, url, headers=h, timeout=timeout, allow_redirects=allow_redirects,
                             stream=True, params=params)
        out["status"] = r.status_code
        out["url"] = r.url
        out["location"] = r.headers.get("Location")
        if method != "HEAD":
            raw = b""
            for chunk in r.iter_content(16384):
                raw += chunk
                if len(raw) >= max_bytes:
                    break
            r.close()
            enc = r.encoding or "utf-8"
            out["text"] = raw.decode(enc, errors="replace")
    except requests.RequestException as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    return out
