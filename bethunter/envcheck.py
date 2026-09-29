"""STATUS DO AMBIENTE. Estado negativo NUNCA é fatal (exceto o banco local): só informa."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from . import db, net, sources, visual

OK, ERRO, BLOQ = "OK", "ERRO", "BLOQUEADO"


def static_status():
    """Sem rede: YT-DLP, ANTHROPIC, TESSERACT e BANCO."""
    yt = sources.ytdlp_cmd()
    tess = visual.tesseract_path()
    try:
        with db.connect() as c:
            c.execute("SELECT COUNT(*) FROM candidates").fetchone()
        banco = {"estado": OK, "detalhe": db._path()}
    except (sqlite3.Error, OSError) as e:
        banco = {"estado": ERRO, "detalhe": f"{type(e).__name__}: {e}"}
    return {
        "YT-DLP": {"estado": "INSTALADO" if yt else "NÃO INSTALADO", "detalhe": "coleta complementar de vídeos ativa" if yt else "opcional: pip install yt-dlp"},
        "ANTHROPIC": {"estado": "CONFIGURADO" if visual.api_key() else "NÃO CONFIGURADO",
                      "detalhe": "análise visual multimodal ativa" if visual.api_key() else "opcional: variável ANTHROPIC_API_KEY (.env)"},
        "TESSERACT": {"estado": "INSTALADO" if tess else "NÃO INSTALADO", "detalhe": tess or "opcional: OCR leve das thumbnails"},
        "BANCO": banco,
        "VISUAL_ANALYSIS": {"estado": "DISPONÍVEL" if visual.engine() else "NÃO DISPONÍVEL",
                            "detalhe": {"multimodal": "API multimodal", "ocr": "OCR (tesseract)", None: "segue só com texto"}[visual.engine()]},
    }


def _judge(r, text_rx=None):
    if r["error"]:
        return {"estado": ERRO, "detalhe": r["error"]}
    st = r["status"]
    body = (r.get("text") or "")[:8000]
    if st in (202, 403, 429) or (st == 200 and sources.BLOCK_RX.search(body) and not (text_rx and text_rx in body)):
        return {"estado": BLOQ, "detalhe": sources.http_error(st) if st != 200 else "captcha/anti-bot"}
    if st and 200 <= st < 400:
        return {"estado": OK, "detalhe": f"HTTP {st}"}
    return {"estado": ERRO, "detalhe": f"HTTP {st}"}


def _internet():
    return _judge(net.fetch("https://www.cloudflare.com/cdn-cgi/trace", timeout=6, throttle=False, allow_redirects=True))


def _ddg():
    return _judge(net.fetch("https://html.duckduckgo.com/html/", params={"q": "site:tiktok.com teste"}, timeout=8,
                            throttle=False, allow_redirects=True), "result")


def _bing():
    return _judge(net.fetch("https://www.bing.com/search", params={"q": "site:tiktok.com teste"}, timeout=8,
                            throttle=False, allow_redirects=True), "b_results")


def _tiktok():
    r = net.fetch("https://www.tiktok.com/@tiktok", timeout=8, throttle=False, allow_redirects=True)
    j = _judge(r, "UNIVERSAL_DATA")
    if j["estado"] == OK and "UNIVERSAL_DATA_FOR_REHYDRATION" not in (r.get("text") or ""):
        return {"estado": BLOQ, "detalhe": "página sem dados públicos (exige JS/login ou bloqueio)"}
    return j


def test_all():
    """Testes rápidos (≤ ~10 s no total, em paralelo, sem retry longo). -> dict nome -> {estado, detalhe}"""
    old = net.CFG["max_retries"]
    net.CFG["max_retries"] = 0
    try:
        with ThreadPoolExecutor(4) as ex:
            f = {"INTERNET": ex.submit(_internet), "DUCKDUCKGO": ex.submit(_ddg), "BING": ex.submit(_bing),
                 "TIKTOK": ex.submit(_tiktok)}
            net_res = {}
            for k, fu in f.items():
                try:
                    net_res[k] = fu.result(timeout=20)
                except Exception as e:   # noqa: nunca fatal
                    net_res[k] = {"estado": ERRO, "detalhe": type(e).__name__}
    finally:
        net.CFG["max_retries"] = old
    return {**net_res, **static_status()}
