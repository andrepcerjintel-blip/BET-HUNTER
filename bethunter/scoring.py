"""Score explicável + classificador de falsos positivos.
O score é apoio à triagem: nunca conclui ilicitude. A confirmação final é humana."""
from . import config, extract, urltools
from .util import norm, find_terms, clip, NAO_IDENTIFICADO

VIDEO_KINDS = ("video", "snippet")

FP_CATS = [  # (chave léxico, peso reducer, rótulo de tipo, rótulo do motivo)
    ("fp_journalism", "r_journalism", "JORNALISMO / NOTÍCIA", "conteúdo jornalístico"),
    ("fp_critica", "r_critica", "CRÍTICA ÀS APOSTAS", "conteúdo crítico às apostas"),
    ("fp_institutional", "r_institutional", "INSTITUCIONAL", "conteúdo institucional/governamental"),
    ("fp_legal", "r_legal", "CONTEÚDO JURÍDICO", "conteúdo jurídico explicativo"),
    ("fp_educ", "r_educ", "PREVENÇÃO / EDUCATIVO", "conteúdo educativo ou de prevenção"),
    ("fp_legislation", "r_legislation", "LEGISLAÇÃO", "conteúdo sobre legislação"),
]
FP_THRESHOLD = 2  # soma de pesos de termos distintos necessária para acionar o redutor


def _fp_hits(text_norm, settings, key):
    total, terms = 0.0, []
    for term, w in extract.lexicon(settings, key):
        if find_terms(text_norm, [term]):
            total += w
            terms.append(term)
    return total, terms


def analyze(bundle, settings):
    """bundle: {username, display_name, bio, evidences:[{kind,caption,text,url_video,tags,hashtags}],
                links:[{origin,url_original,chain,url_final,domain_final,params,page_title,page_text,error}]}
    -> dict com score_base_raw, reasons, flags, campos extraídos, tipo de conteúdo."""
    W = settings["weights"]
    reasons = []

    def add(key, label, detail="", pts=None):
        p = W[key] if pts is None else pts
        if p:
            reasons.append({"key": key, "pts": p, "label": label, "detail": detail})

    ev = [e for e in bundle["evidences"] if e["kind"] != "relacao"]
    links = bundle["links"]
    bio = bundle.get("bio") or ""
    parts = [bundle.get("display_name") or "", bio]
    for e in ev:
        parts += [e.get("caption") or "", e.get("text") or "", " ".join("#" + h for h in e.get("hashtags", []))]
    all_text = "\n".join(p for p in parts if p)
    ex = extract.extract_all(all_text, settings)
    all_norm = norm(all_text)
    bet_ctx = extract.bet_context(ex)
    tags = {t for e in ev for t in e.get("tags", [])}
    vis = [v["signals"] for v in bundle.get("visuals", [])]
    vis_game = next((k for s in vis for k in ("slot_ui", "roulette", "aviator", "mines", "tigrinho") if s.get(k)), None)
    vis_logo = next((s["platform_logo"] for s in vis if s.get("platform_logo")), None)
    vis_pay = next((k for s in vis for k in ("withdraw", "pix") if s.get(k)), None)
    vis_code = next((s["promo_code"] for s in vis if s.get("promo_code")), None)

    # ---------- links ----------
    bet_terms_page = extract.lexicon(settings, "bet_terms")
    link_info = []
    for l in links:
        lvl = urltools.bet_link_level(l, settings, bet_terms_page)
        kind = urltools.domain_kind(l.get("domain_final") or "", settings)
        link_info.append((l, lvl, kind))
    aff_params, code_params = [], []
    for l in links:
        for p in l.get("params", []):
            if p["type"] in config.AFFILIATE_TYPES:
                aff_params.append((p, l))
            elif p["type"] == "promo":
                code_params.append((p, l))
    cta_present = bool(ex["cta"])
    strong_lvls = ("known", "page", "hint_strong")
    bet_links = [(l, lvl) for l, lvl, k in link_info
                 if k not in ("messenger", "tiktok") and (
                     lvl in strong_lvls or (lvl == "hint_weak" and (cta_present or aff_params or bet_ctx)))]
    link_bet = bool(bet_links)
    if link_bet:
        l0, lvl0 = bet_links[0]
        why = {"known": "plataforma conhecida", "page": "página de destino com conteúdo de apostas",
               "hint_strong": "domínio com indício forte", "hint_weak": "domínio com indício + contexto"}[lvl0]
        add("link_bet", "link externo para plataforma relacionada a aposta",
            f"{l0.get('domain_final')} ({why})")

    # ---------- CTA ----------
    cta = cta_present and (bet_ctx or link_bet)
    if cta:
        add("cta", "CTA explícito", f"\"{ex['cta'][0]}\"")

    # ---------- gameplay ----------
    gameplay = "gameplay" in tags or bool(vis_game)
    gp_detail = "marcado pelo analista" if "gameplay" in tags else (f"visual: {vis_game}" if vis_game else "")
    if not gameplay:
        for e in ev:
            if e["kind"] in VIDEO_KINDS or e.get("url_video"):
                x = extract.extract_all((e.get("caption") or "") + " " + (e.get("text") or ""), settings)
                if x["games_specific"] and (x["gameplay"] or x["cta"] or x["payment"]):
                    gameplay, gp_detail = True, f"vídeo cita {x['games_specific'][0]} + " + \
                        (x["gameplay"] or x["cta"] or x["payment"])[0]
                    break
    if gameplay:
        add("gameplay", "vídeo demonstrando jogo/aposta", gp_detail)

    # ---------- plataforma (nome/logo em conteúdo) ----------
    plat = list(ex["platforms"])
    if vis_logo and vis_logo.lower() not in [p.lower() for p in plat]:
        plat.append(vis_logo)
    if plat or "logomarca" in tags:
        add("platform", "nome/logomarca de plataforma",
            (", ".join(plat) + (" (logomarca visual)" if vis_logo else "")) if plat else "logomarca (analista)")

    # ---------- pagamento ----------
    pay = bool(ex["payment"]) and (bet_ctx or link_bet)
    if pay or "saque_demonstrado" in tags or vis_pay:
        add("payment", "promessa/demonstração de saque ou pagamento",
            f"\"{ex['payment'][0]}\"" if pay and ex["payment"] else (f"visual: {vis_pay}" if vis_pay else "marcado pelo analista"))

    # ---------- afiliado ----------
    aff = bool(aff_params) and (link_bet or bet_ctx)
    aff_ids = [f"{p['param']}={p['value']}" for p, _ in aff_params]
    if aff:
        p, l = aff_params[0]
        add("aff_link", "link com parâmetro de afiliado", f"{p['param']}={p['value']} em {p['dominio']}")

    # ---------- bônus / código ----------
    codes = list(ex["codes"])
    for p, _ in code_params:
        if p["value"] not in codes:
            codes.append(p["value"])
    if vis_code and vis_code not in codes:
        codes.append(vis_code)
    bonus = bool(ex["bonus"] or codes) and (bet_ctx or link_bet or bool(vis_game))
    if bonus:
        add("bonus_code", "bônus, cupom ou código promocional",
            f"código {codes[0]}" if codes else f"\"{ex['bonus'][0]}\"")

    # ---------- grupos ----------
    group_links = [l for l, lvl, k in link_info if k == "messenger"]
    group = (bool(group_links) or bool(ex["group"])) and (bet_ctx or link_bet)
    if group:
        add("group", "grupo Telegram/WhatsApp relacionado",
            group_links[0]["domain_final"] if group_links else f"\"{ex['group'][0]}\"")

    # ---------- hashtags / expressões ----------
    rel_tags = extract.hashtag_bet_related(ex["hashtags"], settings)
    if rel_tags:
        add("hashtag", "hashtag relacionada a jogo/aposta", "#" + rel_tags[0])
    strong_expr = [x for x in ex["expressions"] if x not in ("banca",)]
    if strong_expr and (bet_ctx or link_bet):
        add("expressions", "expressão típica de divulgação", f"\"{strong_expr[0]}\"")

    # ---------- recorrência ----------
    bet_items, cta_items, video_items = [], 0, 0
    for e in ev:
        if e["kind"] in ("bio",):
            continue
        x = extract.extract_all((e.get("caption") or "") + " " + (e.get("text") or ""), settings)
        if e["kind"] in VIDEO_KINDS or e.get("url_video"):
            video_items += 1
        if extract.bet_context(x):
            bet_items.append(x)
            if x["cta"]:
                cta_items += 1
    recurring = False
    if len(bet_items) >= settings["recurrence_min"]:
        add("recurrence", "conteúdo recorrente relacionado a apostas", f"{len(bet_items)} conteúdos")
    bio_bet_link = any(l.get("origin") == "bio" for l, _ in bet_links)
    if (len(bet_items) >= settings["padrao_min_bet_items"] and video_items and
            len(bet_items) / max(video_items, 1) >= 0.5 and bio_bet_link and cta_items >= 2):
        recurring = True
        add("padrao_recorrente", "PADRÃO PROMOCIONAL RECORRENTE",
            f"{len(bet_items)}/{video_items} conteúdos de apostas, link na bio, CTAs em {cta_items}")

    # ---------- falso positivo ----------
    commercial = link_bet or aff or (cta and bet_ctx) or bool(codes and bet_ctx) or gameplay
    strong_promo = link_bet or aff or (cta and bet_ctx)
    fp_hit = {}
    applied = []
    for lexkey, wkey, tipo, motivo in FP_CATS:
        tot, terms = _fp_hits(all_norm, settings, lexkey)
        fp_hit[lexkey] = (tot, terms)
    for lexkey, wkey, tipo, motivo in FP_CATS:
        tot, terms = fp_hit[lexkey]
        if tot >= FP_THRESHOLD:
            if lexkey == "fp_legislation" and any(a[0] in ("fp_legal", "fp_journalism") for a in applied):
                continue  # legislação só vale quando é o único enquadramento
            applied.append((lexkey, tipo))
            add(wkey, f"redutor: {motivo}", "termos: " + ", ".join(terms[:4]))
    c_tot, c_terms = _fp_hits(all_norm, settings, "fp_comment")
    if c_tot >= FP_THRESHOLD and not commercial:
        applied.append(("fp_comment", "COMENTÁRIO / REAÇÃO"))
        add("r_comment", "redutor: comentário/reação sem divulgação", "termos: " + ", ".join(c_terms[:3]))
    if bet_ctx and not commercial and not (cta or pay or bonus or group):
        add("r_incidental", "redutor: palavra-chave sem relação comercial",
            "termos: " + ", ".join((ex["bet_terms"] + ex["games"])[:3]))
    pol_tot, _ = _fp_hits(all_norm, settings, "fp_political")
    meme_tot, _ = _fp_hits(all_norm, settings, "fp_meme")

    # ---------- tipo de conteúdo ----------
    if applied and not strong_promo:
        ctype = applied[0][1]
    elif pol_tot >= FP_THRESHOLD and not strong_promo:
        ctype = "DEBATE POLÍTICO"
    elif meme_tot >= FP_THRESHOLD and not commercial:
        ctype = "MEME"
    elif strong_promo or (commercial and bet_ctx):
        if aff or (codes and link_bet):
            ctype = "AFILIADO"
        elif ex["publi"]:
            ctype = "PUBLICIDADE"
        elif ex["official"] and plat:
            ctype = "PLATAFORMA"
        elif ex["influencer"]:
            ctype = "INFLUENCIADOR"
        else:
            ctype = "DIVULGAÇÃO"
    else:
        ctype = "INDETERMINADO"

    # ---------- evidência principal ----------
    parts = []
    if cta:
        parts.append(f"CTA \"{ex['cta'][0]}\"")
    if link_bet:
        parts.append("link → " + bet_links[0][0]["domain_final"])
    if aff:
        parts.append(aff_ids[0])
    if pay:
        parts.append(f"saque/pagamento \"{ex['payment'][0]}\"")
    if codes and bonus:
        parts.append(f"código {codes[0]}")
    if ex["games"] and not parts:
        parts.append("cita " + ex["games"][0])
    excerpt = ""
    best = None
    for e in ev:
        t = (e.get("caption") or "") + " " + (e.get("text") or "")
        if t.strip():
            x = extract.extract_all(t, settings)
            n = len(x["cta"]) + len(x["payment"]) + len(x["bonus"]) + len(x["games"]) + len(x["platforms"])
            if best is None or n > best[0]:
                best = (n, t)
    if best:
        excerpt = clip(best[1], 140)
    main = " | ".join(parts)
    if excerpt:
        main = (main + " | " if main else "") + f"“{excerpt}”"
    if not main:
        main = NAO_IDENTIFICADO

    # ---------- prioridade operacional (1 = mais importante) ----------
    has_link = link_bet
    if has_link and cta and bet_ctx:
        priority = 1
    elif has_link and (aff or codes):
        priority = 2
    elif gameplay and cta:
        priority = 3
    else:
        priority = 4

    raw = sum(r["pts"] for r in reasons)
    manual_vis = bool(tags & {"gameplay", "logomarca", "saque_demonstrado"})
    visual_status = "disponível" if vis else ("manual" if manual_vis else "não disponível")
    return {"visual_analysis": visual_status,
        "raw": raw, "reasons": reasons, "content_type": ctype, "priority": priority, "recurring": recurring,
        "platforms": plat, "games": ex["games"], "hashtags": ex["hashtags"], "codes": codes,
        "affiliate_ids": aff_ids, "mentions": ex["mentions"], "main_evidence": main,
        "flags": {"link_bet": link_bet, "cta": cta, "bet_content": bet_ctx, "aff": aff, "gameplay": gameplay},
    }


def clamp(raw):
    return max(0, min(100, int(raw)))


def classify(score, settings):
    t = settings["thresholds"]
    if score >= t["alta"]:
        return "ALTA PROBABILIDADE DE DIVULGAÇÃO"
    if score >= t["revisar"]:
        return "REVISAR"
    return "BAIXA RELEVÂNCIA"
