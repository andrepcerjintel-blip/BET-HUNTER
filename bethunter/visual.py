"""VISUAL_ANALYSIS opcional: thumbnail -> sinais (slot/roleta/Aviator/Mines/Tigrinho, saldo, valor, botão de aposta,
saque, PIX, logomarca, QR, código). Motores, em ordem: API multimodal (se ANTHROPIC_API_KEY) e OCR leve (se `tesseract`).
Sem motor => 'não disponível' e o candidato segue normalmente."""
import base64
import json
import os
import re
import shutil
import subprocess

import requests

from . import db, extract, net, sources
from .util import norm, now_iso

KEYS = ["slot_ui", "roulette", "aviator", "mines", "tigrinho", "balance", "money_value", "bet_button", "withdraw",
        "pix", "platform_logo", "qr_code", "promo_code"]
GAME_KEYS = ("slot_ui", "roulette", "aviator", "mines", "tigrinho")
PROMPT = ("Analise esta miniatura/frame de um vídeo. Responda APENAS um JSON com as chaves: "
          + ", ".join(KEYS) + ". Booleanos para: slot_ui (interface típica de slot), roulette, aviator, mines, tigrinho "
          "(Fortune Tiger), balance (saldo visível), money_value (valor monetário R$), bet_button (botão de aposta/girar), "
          "withdraw (tela/menção de saque), pix, qr_code. platform_logo = nome da plataforma se houver logomarca legível, "
          "senão null. promo_code = código promocional legível, senão null. Não invente: na dúvida, false/null.")
_warned = {"done": False}


def api_key():
    """Chave só via variável de ambiente (ou .env carregado no ambiente). Nunca é gravada no banco/código."""
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def tesseract_path():
    exe = shutil.which("tesseract")
    if exe:
        return exe
    for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.isfile(p):
            return p
    return None


def engine():
    if api_key():
        return "multimodal"
    if tesseract_path():
        return "ocr"
    return None


def _media_type(b):
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return None


def _norm_signals(d):
    out = {k: bool(d.get(k)) for k in KEYS if k not in ("platform_logo", "promo_code")}
    out["platform_logo"] = (d.get("platform_logo") or None) if isinstance(d.get("platform_logo"), str) else None
    out["promo_code"] = (d.get("promo_code") or None) if isinstance(d.get("promo_code"), str) else None
    return out


def analyze_multimodal(img):
    mt = _media_type(img)
    if not mt:
        return None
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
            "x-api-key": api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": os.environ.get("BETHUNTER_VISION_MODEL", "claude-haiku-4-5-20251001"), "max_tokens": 400,
                  "messages": [{"role": "user", "content": [
                      {"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(img).decode()}},
                      {"type": "text", "text": PROMPT}]}]})
        if r.status_code != 200:
            return None
        txt = r.json()["content"][0]["text"]
        m = re.search(r"\{.*\}", txt, re.S)
        return {"engine": "multimodal", "signals": _norm_signals(json.loads(m.group(0))), "text": ""}
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def analyze_ocr(img):
    exe = tesseract_path()
    if not exe:
        return None
    text = ""
    for langs in (["-l", "por+eng"], []):
        try:
            p = subprocess.run([exe, "stdin", "stdout", *langs], input=img, capture_output=True, timeout=40)
            if p.returncode == 0:
                text = p.stdout.decode("utf-8", "replace")
                break
        except (subprocess.SubprocessError, OSError):
            return None
    if not text.strip():
        return {"engine": "ocr", "signals": _norm_signals({}), "text": ""}
    g = norm(text)
    sig = {
        "tigrinho": bool(re.search(r"fortune\s*tiger|tigrinho", g)), "aviator": "aviator" in g,
        "mines": bool(re.search(r"(?<![a-z])mines(?![a-z])", g)), "roulette": "roleta" in g or "roulette" in g,
        "slot_ui": bool(re.search(r"(?<![a-z])(spin|girar|autoplay|auto play)(?![a-z])", g) and re.search(r"(saldo|balance|aposta|bet)", g)),
        "balance": bool(re.search(r"(saldo|balance)", g)), "money_value": bool(re.search(r"r\$\s?\d", g)),
        "bet_button": bool(re.search(r"(apostar|girar|jogar agora|spin)", g)),
        "withdraw": bool(re.search(r"(saque|sacar|withdraw)", g)), "pix": "pix" in g, "qr_code": False,
        "platform_logo": None, "promo_code": (extract.find_codes(text) or [None])[0],
    }
    return {"engine": "ocr", "signals": _norm_signals(sig), "text": text[:600]}


def analyze_image(img):
    eng = engine()
    if eng == "multimodal":
        r = analyze_multimodal(img)
        if r:
            return r
    if tesseract_path():
        return analyze_ocr(img)
    return None


def fetch_thumb(video_url):
    """-> (thumb_url, bytes|None). Rede; isolado para testes."""
    v = sources.fetch_video(video_url)
    if not v.get("thumbnail"):
        return "", None
    return v["thumbnail"], net.fetch_bytes(v["thumbnail"])


def run_visual(cid, settings, max_n=None):
    """Analisa até `max_n` vídeos ainda não analisados do candidato. -> {'status','novos'}. Nunca levanta."""
    from . import pipeline
    max_n = max_n or settings.get("visual_max_per_candidate", 3)
    if engine() is None:
        if not _warned["done"]:
            _warned["done"] = True
            with db.connect() as c:
                pipeline.log_search(c, "visual", "análise visual", "visual", 0, 0, 0, 1,
                                    "sem motor visual (defina ANTHROPIC_API_KEY ou instale tesseract): segue só com texto", __import__("time").time())
        return {"status": "não disponível", "novos": 0}
    with db.connect() as c:
        vids = [r[0] for r in c.execute(
            "SELECT DISTINCT url_video FROM evidences WHERE candidate_id=? AND url_video!='' AND kind!='relacao' "
            "AND url_video NOT IN (SELECT url_video FROM visuals WHERE candidate_id=?) ORDER BY id LIMIT ?", (cid, cid, max_n))]
    n = 0
    for v in vids:
        try:
            turl, img = fetch_thumb(v)
            res = analyze_image(img) if img else None
        except Exception:
            res = None
        if not res:
            continue
        with db.connect() as c:
            c.execute("INSERT OR IGNORE INTO visuals(candidate_id,url_video,thumb_url,engine,signals,text,collected_at) VALUES(?,?,?,?,?,?,?)",
                      (cid, v, turl, res["engine"], json.dumps(res["signals"]), res["text"], now_iso()))
            n += 1
    if n:
        with db.connect() as c:
            pipeline.refresh_candidate(c, cid, settings)
    return {"status": "disponível" if n else "não disponível", "novos": n}
