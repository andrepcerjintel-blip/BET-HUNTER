"""Coleta de perfis do TikTok numa ÚNICA sessão persistente do Chrome (Playwright).

Coleta passiva: abre a página pública do perfil, fecha o modal de login com "Pular/Skip/Agora não" quando existir,
lê os dados visíveis e tira screenshot. Captcha/verificação/bloqueio NÃO são contornados: o perfil fica pendente
(status BLOQUEADO) e a coleta pode ser retomada. Seletores baseados na estrutura pública observada (`data-e2e`);
NÃO foram validados contra o TikTok real neste ambiente — o parser tem fallback por JSON embutido e é coberto
por testes com HTML de exemplo.
"""
import json
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SKIP_LABELS = ["Pular", "Skip", "Agora não", "Not now", "Fechar", "Close"]
PRIVATE_MARKERS = ["esta conta e privada", "this account is private"]
MISSING_MARKERS = ["nao foi possivel encontrar esta conta", "couldn't find this account", "could not find this account",
                   "conta nao encontrada", "account not found", "conta foi banida", "this account was banned"]
BLOCK_MARKERS = ["verifique para continuar", "verify to continue", "captcha", "arraste o controle", "drag the slider",
                 "too many requests", "muitas solicitacoes", "access denied"]


def _text(el):
    return el.get_text(" ", strip=True) if el else None


def _json_state(soup):
    tag = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__") or soup.find("script", id="SIGI_STATE")
    try:
        return json.loads(tag.string) if tag and tag.string else None
    except ValueError:
        return None


def _find_user(obj, username):
    """Procura recursivamente o dict do usuário (uniqueId == username) sem depender de um caminho fixo."""
    if isinstance(obj, dict):
        if str(obj.get("uniqueId", "")).lower() == username.lower() and ("nickname" in obj or "signature" in obj):
            return obj, obj
        for v in obj.values():
            r = _find_user(v, username)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_user(v, username)
            if r:
                return r
    return None


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def parse_profile_html(html, username):
    """-> dict (display_name, followers, following, likes, bio, bio_links, content, status, private).
    Valores ausentes ficam None/[] — seguidores nunca viram 0 por falta de dado."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    page = common.norm(soup.get_text(" ", strip=True))
    rec = {"display_name": None, "followers": None, "following": None, "likes": None, "bio": None, "bio_links": [],
           "content": [], "private": None, "status": "OK", "error": None}
    if any(m in page for m in BLOCK_MARKERS) and not soup.select_one('[data-e2e="user-title"]'):
        rec.update(status="BLOQUEADO", error="verificação/captcha/limite detectado (não contornado)")
        return rec
    if any(m in page for m in MISSING_MARKERS):
        rec.update(status="INDISPONIVEL", error="conta não encontrada/removida")
        return rec

    rec["display_name"] = _text(soup.select_one('[data-e2e="user-subtitle"]'))
    rec["followers"] = common.parse_count(_text(soup.select_one('[data-e2e="followers-count"]')))
    rec["following"] = common.parse_count(_text(soup.select_one('[data-e2e="following-count"]')))
    rec["likes"] = common.parse_count(_text(soup.select_one('[data-e2e="likes-count"]')))
    rec["bio"] = _text(soup.select_one('[data-e2e="user-bio"]'))
    for a in soup.select('[data-e2e="user-link"]'):
        inner = a.find("a")
        href = a.get("href") or (inner.get("href") if inner else None)
        if href:
            rec["bio_links"].append(common.unwrap_tiktok(href))
    for d in soup.select('[data-e2e="user-post-item-desc"]')[:15]:
        t = d.get("title") or _text(d)
        if t:
            rec["content"].append(t)
    for img in soup.select('[data-e2e="user-post-item"] img[alt]')[:15]:
        if img.get("alt") and img["alt"] not in rec["content"]:
            rec["content"].append(img["alt"])

    if rec["followers"] is None and rec["bio"] is None:                       # fallback: JSON embutido
        st = _json_state(soup)
        found = _find_user(st, username) if st else None
        if found:
            u = found[0]
            rec["display_name"] = rec["display_name"] or u.get("nickname")
            rec["bio"] = u.get("signature")
            bl = u.get("bioLink")
            if isinstance(bl, dict) and bl.get("link"):
                rec["bio_links"].append(bl["link"])
            rec["private"] = u.get("privateAccount")
            for key, dst in (("followerCount", "followers"), ("followingCount", "following"), ("heartCount", "likes")):
                v = _find_key(st, key)
                rec[dst] = common.parse_count(v) if v is not None else None
    if any(m in page for m in PRIVATE_MARKERS):
        rec["private"] = True
        rec["status"] = "PRIVADA"
    elif rec["private"] is None and (rec["followers"] is not None or rec["bio"] is not None):
        rec["private"] = False
    if rec["status"] == "OK" and rec["followers"] is None and rec["bio"] is None and not rec["display_name"]:
        rec.update(status="ERRO_TEMPORARIO", error="página sem dados de perfil (carregamento incompleto?)")
    rec["bio_links"] = list(dict.fromkeys(rec["bio_links"]))
    return rec


class BrowserCollector:
    """Uma sessão Chrome persistente reaproveitada para todos os perfis."""

    def __init__(self, user_data_dir=None, channel="chrome", headless=False, screenshots_dir=None, timeout_ms=30000,
                 manual_wait=0):
        self.user_data_dir = str(user_data_dir or common.P("output", ".chrome_profile"))
        self.channel, self.headless, self.timeout_ms, self.manual_wait = channel, headless, timeout_ms, manual_wait
        self.shots = Path(screenshots_dir or common.P("output", "screenshots"))
        self._pw = self.ctx = self.page = None

    def open(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError("Playwright não instalado: pip install playwright (o Chrome local é usado; não baixe navegadores)") from e
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            self.user_data_dir, channel=self.channel, headless=self.headless, locale="pt-BR",
            viewport={"width": 1366, "height": 900})
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.set_default_timeout(self.timeout_ms)
        return self

    def close(self):
        for fn in (lambda: self.ctx and self.ctx.close(), lambda: self._pw and self._pw.stop()):
            try:
                fn()
            except Exception:
                pass

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    def dismiss_login_modal(self):
        """Fecha "Entrar no TikTok" usando os botões do próprio modal. Não autentica nem burla nada."""
        for _ in range(2):
            closed = False
            for label in SKIP_LABELS:
                try:
                    btn = self.page.get_by_role("button", name=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)).first
                    if btn.count() and btn.is_visible():
                        btn.click(timeout=1500)
                        closed = True
                        break
                except Exception:
                    continue
            if not closed:
                try:
                    x = self.page.locator('[data-e2e="modal-close-inner-button"]').first
                    if x.count() and x.is_visible():
                        x.click(timeout=1500)
                        closed = True
                except Exception:
                    pass
            if not closed:
                return
            time.sleep(0.6)

    def collect(self, username, seq):
        url = f"https://www.tiktok.com/@{username}"
        shot = self.shots / f"{seq:03d}_{username}_perfil.png"
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            try:
                self.page.wait_for_selector('[data-e2e="user-title"], [data-e2e="user-bio"]', timeout=8000)
            except Exception:
                pass
            time.sleep(random.uniform(1.0, 2.0))
            self.dismiss_login_modal()
            status = resp.status if resp else None
            rec = parse_profile_html(self.page.content(), username)
            if rec["status"] == "BLOQUEADO" and self.manual_wait and not self.headless:
                deadline = time.time() + self.manual_wait                  # humano resolve a verificação; nada é automatizado
                while time.time() < deadline and rec["status"] == "BLOQUEADO":
                    time.sleep(3)
                    rec = parse_profile_html(self.page.content(), username)
            if status in (403, 429) and rec["status"] not in ("OK", "PRIVADA"):
                rec.update(status="BLOQUEADO", error=f"HTTP {status} (não contornado)")
            if rec["status"] in ("OK", "PRIVADA", "INDISPONIVEL"):
                self.shots.mkdir(parents=True, exist_ok=True)
                self.page.screenshot(path=str(shot), full_page=False)
                rec["screenshot"] = str(shot)
            return rec
        except Exception as e:                                   # timeout / navegação: pendente, não fatal
            msg = "tempo esgotado" if "Timeout" in type(e).__name__ else f"erro de navegação ({type(e).__name__})"
            return {"status": "ERRO_TEMPORARIO", "error": msg}


class FixtureCollector:
    """Coletor offline para demonstração/testes: lê perfis de um JSON {username: {...}}."""

    def __init__(self, path_or_dict):
        self.data = path_or_dict if isinstance(path_or_dict, dict) else json.loads(Path(path_or_dict).read_text(encoding="utf-8"))

    def open(self):
        return self

    def close(self):
        pass

    def collect(self, username, seq):
        d = self.data.get(username)
        if d is None:
            return {"status": "INDISPONIVEL", "error": "não consta no arquivo de fixtures"}
        rec = {"display_name": None, "followers": None, "following": None, "likes": None, "bio": None, "bio_links": [],
               "content": [], "private": False, "status": "OK", "error": None}
        rec.update(d)
        rec["followers"] = common.parse_count(rec.get("followers"))
        rec["following"] = common.parse_count(rec.get("following"))
        rec["likes"] = common.parse_count(rec.get("likes"))
        return rec
