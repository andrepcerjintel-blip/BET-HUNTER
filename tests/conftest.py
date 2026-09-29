"""Rede simulada + dados FICTÍCIOS (nenhum perfil/domínio real)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bethunter import db, net  # noqa: E402


def tt_profile_html(uid, uname, nick, bio, link=None, items=()):
    user = {"id": uid, "uniqueId": uname, "nickname": nick, "signature": bio}
    if link:
        user["bioLink"] = {"link": link}
    scope = {"webapp.user-detail": {"statusCode": 0, "userInfo": {"user": user}, "itemList": list(items)}}
    data = json.dumps({"__DEFAULT_SCOPE__": scope})
    return f'<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{data}</script></html>'


def item(vid, desc, tags=()):
    return {"id": vid, "desc": desc, "textExtra": [{"hashtagName": t} for t in tags]}


PROMO_ITEMS = [
    item("1001", "Ganhei no Fortune Tiger! Saque na hora, cadastre-se pelo link na bio #tigrinho #slots", ["tigrinho", "slots"]),
    item("1002", "Plataforma pagando! Use o código TIGRE10 e ganhe bônus. Link na bio", []),
    item("1003", "Horário pagante do tigrinho agora, entra no grupo vip de sinais 🐯", []),
    item("1004", "Aviator estratégia: girando e lucrando, cadastre-se no link", []),
]


def make_pages():
    pages = {}
    def page(url, status=200, text="", location=None):
        pages[url] = {"status": status, "url": url, "location": location, "text": text, "error": None}
    for n, aff in (("promo_fake1", "981"), ("promo_fake2", "223"), ("promo_fake3", "516")):
        page(f"https://www.tiktok.com/@{n}", text=tt_profile_html(
            f"9{aff}", n, n.title(), f"Cadastre-se pelo link na bio 👇 Bônus de cadastro. Código TIGRE{aff}",
            f"https://linktr.ee/{n}", PROMO_ITEMS))
        page(f"https://linktr.ee/{n}", text=f'<html><title>{n}</title><a href="https://bit.ly/{n}">Jogue agora</a>'
                                          f'<a href="https://t.me/grupovip_{n}">Grupo VIP</a><a href="https://linktr.ee/x">x</a></html>')
        page(f"https://bit.ly/{n}", status=301, location=f"https://xyzbet.bet.br/?aff={aff}&utm_source=tiktok")
    page("https://xyzbet.bet.br/?aff=981&utm_source=tiktok", text="<html><title>XYZBet Cassino</title>Aposte no cassino, slots, bônus de depósito e saque via pix</html>")
    page("https://xyzbet.bet.br/?aff=223&utm_source=tiktok", text="<html><title>XYZBet Cassino</title>Aposte no cassino, slots, bônus de depósito e saque via pix</html>")
    page("https://xyzbet.bet.br/?aff=516&utm_source=tiktok", text="<html><title>XYZBet Cassino</title>Aposte no cassino, slots, bônus de depósito e saque via pix</html>")
    page("https://t.me/grupovip_promo_fake1", text="<html>telegram</html>")
    page("https://t.me/grupovip_promo_fake2", text="<html>telegram</html>")
    page("https://t.me/grupovip_promo_fake3", text="<html>telegram</html>")
    page("https://www.tiktok.com/@noticias_fake", text=tt_profile_html(
        "7777", "noticias_fake", "Jornal Fictício", "Reportagem e notícias. Projeto de lei sobre as bets.", None, [
            item("2001", "Reportagem: projeto de lei prevê regulamentação das bets, segundo a polícia há investigação de golpe", ["bets"]),
            item("2002", "Especialista alerta sobre ludopatia e vício em jogo. Procure ajuda. #bets", ["bets"])]))
    page("https://www.tiktok.com/@gone_fake", status=404, text="")
    page("https://www.tiktok.com/@mention_fake", text=tt_profile_html("5555", "mention_fake", "Menção", "Sem nada."))
    page("https://www.tiktok.com/@blocked_fake", text="<html>nada estruturado</html>")
    # buscador simulado
    ddg = ('<html><div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.tiktok.com%2F%40novo_fake1%2Fvideo%2F3001">'
           'Fortune Tiger pagando | TikTok</a><a class="result__snippet">Cadastre-se pelo link na bio e ganhe bônus. Fortune Tiger saque rápido</a></div>'
           '<div class="result"><a class="result__a" href="https://www.tiktok.com/@novo_fake2">Perfil novo</a><a class="result__snippet">Tigrinho ao vivo, link na bio</a></div>'
           '<div class="result"><a class="result__a" href="https://exemplo.com/x">site</a></div>'
           '<div class="result"><a class="result__a" href="https://www.tiktok.com/@promo_fake1/video/1001">dup</a><a class="result__snippet">Ganhei no Fortune Tiger, link na bio</a></div></html>')
    pages["ddg"] = {"status": 200, "url": "ddg", "location": None, "text": ddg, "error": None}
    return pages


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BETHUNTER_DB", str(tmp_path / "t.db"))
    db.init_db()
    pages = make_pages()
    calls = []

    def fake_fetch(url, method="GET", allow_redirects=False, timeout=10, headers=None, max_bytes=400_000, params=None, **_kw):
        calls.append(url)
        if "html.duckduckgo.com" in url:
            return dict(pages["ddg"])
        if "bing.com/search" in url or "tiktok.com/search" in url or "tiktok.com/tag" in url:
            return {"status": 200, "url": url, "location": None, "text": "<html></html>", "error": None}
        if "oembed" in url:
            return {"status": 404, "url": url, "location": None, "text": "", "error": None}
        if url in pages:
            return dict(pages[url])
        return {"status": None, "url": url, "location": None, "text": "", "error": "ConnectionError: simulado"}

    monkeypatch.setattr(net, "fetch", fake_fetch)
    monkeypatch.setattr("bethunter.sources.ytdlp_videos", lambda *a, **k: [])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("bethunter.visual.engine", lambda: None)
    monkeypatch.setitem(__import__("bethunter.visual", fromlist=["x"])._warned, "done", False)
    with db.connect() as c:
        db.save_settings(c, {"request_delay": 0, "source_pause_seconds": 0.01, "rate_limit_cooldowns": [0.01],
                              "query_retry_delays": [0, 0, 0]})
    return calls
