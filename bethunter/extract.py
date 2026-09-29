"""Extração de sinais a partir de texto (bio, legenda, snippet)."""
import re

from . import config
from .util import (norm, find_terms, extract_hashtags, extract_mentions, extract_urls)

_CODE_RX = re.compile(
    r"(?i)(?:c[oó]digo(?:\s+promocional|\s+de\s+b[oô]nus)?|cupom|promo\s?code|promocode|use\s+o\s+c[oó]digo|code)"
    r"\s*[:\-–]?\s*[\"“'‘]?([A-Za-z0-9_\-]{3,20})")


def lexicon(settings, key):
    return (settings.get("lexicons") or {}).get(key) or config.LEXICONS[key]


def games_index(settings):
    """[(nome_canônico, [apelidos...], generico?)]"""
    generic = {norm(g) for g in settings.get("generic_games", [])}
    out = []
    for line in settings.get("games", []):
        names = [n.strip() for n in line.split("|") if n.strip()]
        if names:
            out.append((names[0], names, norm(names[0]) in generic))
    return out


def platforms_index(settings):
    out = []
    for line in settings.get("platforms", []):
        nm, _, doms = line.partition("|")
        nm = nm.strip()
        if nm:
            out.append((nm, [d.strip().lower() for d in doms.split(",") if d.strip()]))
    return out


def find_games(text_norm, settings):
    found = []
    for canon, names, generic in games_index(settings):
        if find_terms(text_norm, names):
            found.append((canon, generic))
    return found


_FAKE_BET = {"alphabet", "alfabet", "abbet"}


def find_platforms(text_norm, settings):
    out = []
    for nm, doms in platforms_index(settings):
        if find_terms(text_norm, [nm]) or any(d in text_norm for d in doms):
            out.append(nm)
    for m in re.finditer(r"(?<![a-z0-9])([a-z0-9]{2,}bets?)(?![a-z0-9])", text_norm):
        w = m.group(1)
        if w not in _FAKE_BET and w not in ("bet", "bets"):
            out.append(w)
    seen, res = set(), []
    for p in out:
        if p.lower() not in seen:
            seen.add(p.lower())
            res.append(p)
    return res


def find_codes(raw_text):
    out = []
    for m in _CODE_RX.finditer(raw_text or ""):
        tok = m.group(1).strip("-_")
        if not tok or norm(tok) in config.CODE_STOPWORDS:
            continue
        # exige cara de código: dígito, ou maiúsculas, ou veio entre aspas
        quoted = raw_text[max(0, m.start(1) - 1):m.start(1)] in "\"“'‘"
        if any(ch.isdigit() for ch in tok) or tok.isupper() or quoted:
            if tok not in out:
                out.append(tok)
    return out


def extract_all(text, settings):
    """Sinais textuais de um trecho."""
    t = norm(text)
    games = find_games(t, settings)
    return {
        "urls": extract_urls(text),
        "hashtags": extract_hashtags(text),
        "mentions": extract_mentions(text),
        "codes": find_codes(text),
        "games": [g for g, _ in games],
        "games_specific": [g for g, gen in games if not gen],
        "platforms": find_platforms(t, settings),
        "cta": find_terms(t, lexicon(settings, "cta")),
        "payment": find_terms(t, lexicon(settings, "payment")),
        "bonus": find_terms(t, lexicon(settings, "bonus")),
        "group": find_terms(t, lexicon(settings, "group")),
        "expressions": find_terms(t, lexicon(settings, "expressions")),
        "bet_terms": find_terms(t, lexicon(settings, "bet_terms")),
        "gameplay": find_terms(t, lexicon(settings, "gameplay")),
        "publi": find_terms(t, lexicon(settings, "publi")),
        "influencer": find_terms(t, lexicon(settings, "influencer")),
        "official": find_terms(t, lexicon(settings, "official")),
    }


def bet_context(ex):
    """Há conteúdo de aposta (jogo, plataforma ou termo do domínio de apostas)?"""
    return bool(ex["games"] or ex["platforms"] or ex["bet_terms"])


def hashtag_bet_related(tags, settings):
    known = {h.lower().lstrip("#") for h in settings.get("hashtags", [])}
    gnames = []
    for _, names, _ in games_index(settings):
        gnames += [re.sub(r"\W", "", norm(n)) for n in names]
    out = []
    for h in tags:
        hn = norm(h)
        if hn in known or any(g and (g == hn or (len(g) >= 6 and g in hn)) for g in gnames) or \
           re.search(r"(bet|cassino|casino|tigrinho|slot|aposta|aviator)", hn):
            out.append(h)
    return out
