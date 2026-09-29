"""Configuração padrão. Tudo aqui é sobrescrevível pelo usuário na aba CONFIG
(salvo no SQLite); nada exige alterar código."""
import copy

DEFAULT_SETTINGS = {
    "weights": {
        "link_bet": 40, "cta": 30, "gameplay": 25, "platform": 25, "payment": 20,
        "aff_link": 20, "bonus_code": 15, "group": 15, "shared_domain": 15,
        "hashtag": 10, "expressions": 10, "recurrence": 10, "padrao_recorrente": 15,
        "r_journalism": -40, "r_critica": -40, "r_institutional": -35, "r_legal": -30,
        "r_educ": -30, "r_legislation": -25, "r_comment": -20, "r_incidental": -20,
    },
    "thresholds": {"alta": 70, "revisar": 45},
    "goal": 200,
    "cache_days": 7,
    "auto_discard_low": False,     # padrão: BAIXA RELEVÂNCIA fica como BAIXA RELEVÂNCIA (oculta da revisão, nunca apaga)
    "request_delay": 1.2,          # segundos entre consultas externas (educação com as fontes)
    "http_timeout": 12,            # segundos por requisição externa
    "http_max_retries": 2,         # novas tentativas só em falha de conexão/timeout
    "http_min_interval": 0.7,      # intervalo mínimo entre requisições (1 por vez, sem paralelismo)
    "resolve_links": True,
    "max_links_per_candidate": 6,
    "aggregator_max_links": 10,
    "enrich_after_search": True,   # após buscas, coleta perfil dos melhores candidatos novos
    "enrich_max": 40,
    "expand_max_queries": 8,
    "recurrence_min": 2,
    "padrao_min_bet_items": 4,
    "cluster_min_score": None,     # None = usa limite de REVISAR
    "games": [
        "Fortune Tiger|tigrinho|jogo do tigrinho", "Fortune Ox", "Fortune Rabbit", "Fortune Dragon",
        "Fortune Mouse", "Fortune Gods", "Aviator", "Mines", "Spaceman", "Crash", "Plinko",
        "PG Soft", "slot", "slots", "roleta", "roleta online", "cassino online",
    ],
    # jogos "genéricos" não bastam para caracterizar demonstração de gameplay
    "generic_games": ["slot", "slots", "roleta", "roleta online", "cassino online", "crash", "mines"],
    "hashtags": [
        "fortunetiger", "tigrinho", "aviator", "mines", "cassino", "cassinoonline", "slot", "slots",
        "saque", "rendaextra", "plataforma", "plataformanova",
    ],
    # nomes (e domínios) de plataformas. Lista EDITÁVEL; formato "Nome|dominio1,dominio2"
    "platforms": [
        "Bet365|bet365.com", "Betano|betano.com", "Betfair|betfair.com", "Sportingbet|sportingbet.com",
        "Pixbet|pixbet.com", "Bet7k", "Betnacional", "Novibet", "Superbet", "Parimatch", "1xBet",
        "Betway", "Vaidebet", "EstrelaBet", "Esportes da Sorte", "Betmotion",
    ],
    "bet_domains": [],             # domínios que o analista sabe serem de apostas
    "aggregators": [
        "linktr.ee", "linktree.com", "beacons.ai", "bio.link", "lnk.bio", "campsite.bio", "allmylinks.com",
        "taplink.cc", "linkin.bio", "bio.site", "carrd.co", "solo.to", "hoo.be", "linkbio.co", "lnk.to",
        "msha.ke", "stan.store", "koji.to", "withkoji.com", "linke.to", "instabio.cc", "tap.bio",
    ],
    "shorteners": [
        "bit.ly", "tinyurl.com", "cutt.ly", "is.gd", "t.ly", "rebrand.ly", "ow.ly", "shorturl.at", "s.id",
        "tiny.cc", "goo.gl", "encurtador.com.br", "vvd.im", "rb.gy", "l.instagram.com", "lnkd.in",
    ],
    "messengers": ["t.me", "telegram.me", "telegram.org", "wa.me", "chat.whatsapp.com", "api.whatsapp.com",
                   "whatsapp.com", "discord.gg", "discord.com", "signal.group"],
    "behavior_terms": [
        "link na bio", "cadastre-se", "crie sua conta", "faça seu cadastro", "bônus", "bônus de cadastro",
        "bônus de boas-vindas", "ganhe bônus", "saque", "saque imediato", "saque rápido", "pix", "pagando",
        "plataforma pagando", "nova plataforma", "grupo vip", "grupo de sinais", "sinais", "horário pagante",
        "carta pagante", "bug", "estratégia", "robô", "método", "entrada", "banca", "multiplicador",
        "rodada grátis", "giros grátis", "cashback", "código", "cupom", "promo code",
    ],
    "behavior_combos": [
        ["link na bio", "saque"], ["cadastre-se", "bônus"], ["plataforma pagando", "pix"],
        ["grupo vip", "sinais"], ["crie sua conta", "bônus"], ["faça seu cadastro", "saque rápido"],
        ["nova plataforma", "bônus de cadastro"], ["horário pagante", "sinais"],
    ],
    "ignore_domains": [
        "instagram.com", "facebook.com", "youtube.com", "youtu.be", "twitter.com", "x.com", "tiktok.com",
        "google.com", "spotify.com", "kwai.com", "pinterest.com", "linkedin.com", "twitch.tv", "apple.com",
    ],
    "expand_sources": ["ddg", "bing", "tiktok"],
    # ---- matriz de consultas (grupos A-D) e missão
    "matrix": {
        "A": ["Fortune Tiger", "Tigrinho", "Aviator", "Mines", "Spaceman", "Crash", "Plinko", "slots", "roleta"],
        "B": ["link na bio", "cadastre-se", "crie sua conta", "acesse", "jogue", "entre agora", "faça seu cadastro"],
        "C": ["saque", "PIX", "pagando", "pagamento", "bônus", "cashback", "giros grátis"],
        "D": ["código", "cupom", "promocode", "indicação", "convite", "grupo VIP", "sinais"],
    },
    "matrix_pairs": ["AB", "AC", "BC", "AD", "BD"],
    "generic_alone": ["bet", "bets", "aposta", "apostas", "apostar", "cassino", "casino", "cassino online", "slot",
                      "slots", "roleta", "mines", "crash", "plinko"],
    "mission_depth": 2,            # 0..3
    "mission_mode": "rapido",      # rapido | completo
    "mission_sources": ["ddg", "bing", "tiktok"],
    "pool_factor": 2.5,            # a descoberta também para quando o pool qualificado chega a meta*fator
    "derived_per_candidate": 6,
    "derived_max": 400,
    "source_fail_limit": 3,        # falhas seguidas -> fonte pausada na missão (registrado no log)
    "visual_max_per_candidate": 3,
    "lexicons": {},  # sobrescreve listas de LEXICONS por chave (avançado)
}

# Léxicos (sem acentos; '*' = prefixo). Editáveis via settings["lexicons"].
LEXICONS = {
    "cta": ["cadastre-se", "cadastre se", "se cadastre", "cadastra", "cadastro pelo link", "crie sua conta",
            "criar conta", "criar sua conta", "faca seu cadastro", "faca o cadastro", "registre-se", "link na bio",
            "link da bio", "link no perfil", "link no bio", "clica no link", "clique no link", "pelo link",
            "acesse", "acessa", "jogue", "joga agora", "aposte", "aposta agora", "entre agora", "entra agora",
            "baixe o app", "comece a jogar", "entre no link", "link abaixo", "link fixado", "link no comentario"],
    "payment": ["saque", "sacar", "saquei", "sacando", "saque imediato", "saque rapido", "pagando",
                "plataforma pagando", "paga de verdade", "pagou", "pix na hora", "caiu na conta", "cai na hora",
                "comprovante", "paguei", "recebi o pix", "pagamento na hora", "sacou"],
    "bonus": ["bonus", "bonus de cadastro", "bonus de boas-vindas", "ganhe bonus", "cupom", "codigo promocional",
              "promo code", "promocode", "rodada gratis", "rodadas gratis", "giros gratis", "cashback",
              "codigo de bonus", "use o codigo", "meu codigo", "bonus no primeiro deposito", "deposito minimo"],
    "group": ["telegram", "whatsapp", "grupo vip", "grupo de sinais", "canal vip", "grupo gratuito", "canal de sinais",
              "grupo do telegram", "grupo do whatsapp", "discord"],
    "expressions": ["horario pagante", "horarios pagantes", "carta pagante", "cartas pagantes", "sinal", "sinais",
                    "bug", "metodo", "estrategia", "robo", "multiplicador", "banca"],
    "bet_terms": ["bet", "bets", "aposta", "apostas", "apostar", "casa de apostas", "cassino", "casino",
                  "slot", "slots", "tigrinho", "roleta", "jackpot", "banca", "fortune tiger", "aviator",
                  "deposito", "depositar", "rodada gratis", "giros gratis", "cassino online"],
    "gameplay": ["girando", "girei", "rodada*", "ganhei", "ganhando", "jogando", "gameplay", "lucro", "lucrei",
                 "green", "multiplicador", "entrada", "x1", "rodei", "tela", "ao vivo", "live", "print"],
    "publi": ["publi", "publicidade", "parceria", "patrocinado", "patrocinio", "#publi", "#ad"],
    "influencer": ["influenciador", "influencer", "criador de conteudo", "digital influencer", "creator",
                   "criadora de conteudo", "embaixador"],
    "official": ["site oficial", "app oficial", "plataforma oficial", "canal oficial"],
    # --- classificador de falsos positivos: (termo, peso) ---
    "fp_journalism": [["reportagem", 2], ["noticia", 2], ["noticias", 2], ["jornal", 1], ["telejornal", 2],
                      ["segundo a policia", 2], ["de acordo com a policia", 2], ["plantao", 1], ["reporter", 1],
                      ["g1", 1], ["cnn", 1], ["urgente", 1], ["operacao", 1], ["policia federal", 1],
                      ["investigacao", 1], ["materia", 1], ["veiculo de imprensa", 2], ["mpf", 1], ["preso", 1]],
    "fp_critica": [["golpe", 1], ["viciado", 1], ["viciados", 1], ["vicio", 1], ["destroi", 1], ["arruina", 1],
                   ["nao caia", 2], ["cuidado com", 1], ["fim das bets", 2], ["proibir as bets", 2],
                   ["proibicao das bets", 2], ["contra as bets", 2], ["perdi tudo", 1], ["enganacao", 1],
                   ["estao roubando", 2], ["explora", 1], ["denuncia", 1], ["nao jogue", 2], ["proibir as apostas", 2]],
    "fp_legislation": [["projeto de lei", 2], ["lei das bets", 2], ["regulamentacao", 2], ["regulamentar", 1],
                       ["stf", 1], ["senado", 1], ["camara dos deputados", 2], ["portaria", 1], ["decreto", 1],
                       ["ministerio da fazenda", 2], ["secretaria de premios e apostas", 2], ["tributacao", 1],
                       ["imposto", 1], ["cpi", 1], ["proibicao", 1], ["lei 14.790", 2], ["legislacao", 2]],
    "fp_legal": [["advogado", 1], ["advogada", 1], ["juridico", 1], ["codigo penal", 2], ["contravencao", 2],
                 ["jurisprudencia", 2], ["sentenca", 1], ["stj", 1], ["responsabilidade civil", 2],
                 ["explico a lei", 2], ["direito do consumidor", 2], ["dr.", 1]],
    "fp_educ": [["ludopatia", 2], ["jogo responsavel", 0.5], ["jogue com responsabilidade", 0.5],
                ["vicio em jogo", 2], ["procure ajuda", 2], ["saude mental", 1], ["educacao financeira", 2],
                ["prevencao", 2], ["conscientizacao", 2], ["cvv", 1], ["entenda", 1], ["como funciona", 1],
                ["dependencia", 1], ["terapia", 1], ["psicologo", 1], ["voce sabia", 1], ["alerta", 1]],
    "fp_institutional": [["gov.br", 2], ["governo federal", 2], ["ministerio", 1], ["procon", 2], ["senacon", 2],
                         ["prefeitura", 1], ["comunicado oficial", 2], ["nota oficial", 2], ["assessoria", 1],
                         ["orgao publico", 2], ["camara municipal", 2]],
    "fp_political": [["presidente", 1], ["lula", 1], ["bolsonaro", 1], ["deputado", 1], ["senador", 1],
                     ["eleicao", 1], ["partido", 1], ["governo", 1], ["oposicao", 1]],
    "fp_comment": [["reagindo", 2], ["reacao", 2], ["react", 2], ["opiniao", 1], ["comentando", 2],
                   ["minha opiniao", 2], ["vamos conversar", 1], ["sera que", 1]],
    "fp_meme": [["meme", 2], ["zoeira", 1], ["piada", 1], ["humor", 1], ["kkkk", 0.5]],
}

CODE_STOPWORDS = {
    "de", "da", "do", "na", "no", "bio", "link", "promocional", "promo", "code", "codigo", "cupom", "aqui", "abaixo",
    "para", "com", "que", "meu", "minha", "use", "usar", "ganhe", "gratis", "bonus", "cadastro", "o", "a", "e",
    "em", "um", "uma", "pelo", "pela", "agora", "hoje", "novo", "nova", "vip", "perfil", "http", "https", "www",
}

# tipos de parâmetros de URL
AFF_PARAMS = {
    "aff": "affiliate", "affiliate": "affiliate", "affiliate_id": "affiliate", "affiliateid": "affiliate",
    "partner": "affiliate", "partnerid": "affiliate", "partner_id": "affiliate",
    "ref": "referral", "referral": "referral", "refid": "referral", "ref_id": "referral",
    "invite": "referral", "invitation": "referral",
    "code": "promo", "promo": "promo", "promocode": "promo", "promo_code": "promo",
    "campaign": "campaign", "campaignid": "campaign", "campaign_id": "campaign", "utm_campaign": "campaign",
    "source": "tracking", "subid": "tracking", "sub_id": "tracking", "clickid": "tracking",
    "click_id": "tracking", "utm_source": "tracking", "utm_medium": "tracking", "utm_content": "tracking",
}
AFFILIATE_TYPES = {"affiliate", "referral"}  # contam para "+20 link com parâmetro de afiliado"

DEFAULT_HUNTS = [
    ("CAÇA 01 — Fortune Tiger / Tigrinho", "queries",
     ["Fortune Tiger", "tigrinho", "jogo do tigrinho", "fortune tiger link na bio", "tigrinho pagando"]),
    ("CAÇA 02 — Aviator", "queries", ["Aviator cadastre-se", "aviator sinais", "aviator horário pagante", "aviator link na bio"]),
    ("CAÇA 03 — Mines", "queries", ["Mines bônus", "mines estratégia", "mines sinais", "mines link na bio"]),
    ("CAÇA 04 — \"plataforma pagando\"", "queries",
     ["\"plataforma pagando\"", "plataforma pagando pix", "nova plataforma pagando"]),
    ("CAÇA 05 — \"link na bio\" + saque", "queries",
     ["\"link na bio\" saque", "\"link na bio\" \"saque rápido\"", "\"link na bio\" pix pagando"]),
    ("CAÇA 06 — \"grupo vip\" + sinais", "queries",
     ["\"grupo vip\" sinais", "\"grupo de sinais\" cassino", "\"grupo vip\" tigrinho"]),
    ("CAÇA 07 — \"bônus\" + cadastro", "queries",
     ["bônus cadastro", "\"cadastre-se\" bônus", "\"bônus de cadastro\" plataforma", "\"crie sua conta\" bônus"]),
    ("CAÇA 08 — \"horário pagante\"", "queries",
     ["\"horário pagante\"", "\"carta pagante\"", "horário pagante tigrinho"]),
    ("CAÇA 09 — promocode / cupom / código", "queries",
     ["\"promo code\" cassino", "cupom bet", "\"código promocional\" bônus slot", "\"use o código\" plataforma"]),
    ("CAÇA 10 — expansão de perfis confirmados", "expand_confirmed", []),
    ("CAÇA 11 — links de afiliados", "affiliate_links",
     ["\"aff=\" plataforma", "\"ref=\" bônus cadastro", "affiliate cadastro bônus"]),
    ("CAÇA 12 — domínios encontrados anteriormente", "known_domains", []),
    ("CAÇA 13 — matriz de consultas (jogos × CTA × financeiro × afiliados)", "matrix", []),
]
DEFAULT_HUNT_SOURCES = ["ddg", "bing", "tiktok"]


def default_settings():
    return copy.deepcopy(DEFAULT_SETTINGS)


def deep_merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base
