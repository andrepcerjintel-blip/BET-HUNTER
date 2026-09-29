"""Gera um banco de DEMONSTRAÇÃO com dados 100% FICTÍCIOS (sem rede).
Uso: python demo/seed_demo.py [--n 400] [--db data/demo.db]  e depois: python run.py --db data/demo.db
Nenhum username/domínio/código abaixo existe de propósito; servem só para testar a interface e o fluxo."""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=400)
ap.add_argument("--db", default="data/demo.db")
a = ap.parse_args()
if os.path.exists(a.db):
    os.remove(a.db)
os.environ["BETHUNTER_DB"] = a.db

from bethunter import db, pipeline, urltools  # noqa: E402
from bethunter.util import base_domain  # noqa: E402

db.init_db()
random.seed(7)
DOMS = ["xyzbet.bet.br", "tigrefake.bet.br", "cassinodemo.com", "fakeslots.vip", "pagafake.bet"]
PROMO = [
    "Cadastre-se pelo link na bio e ganhe bônus de cadastro! Fortune Tiger pagando, saque na hora #tigrinho",
    "Plataforma pagando! Use o código {code} — link na bio. Aviator estratégia e horário pagante",
    "Grupo VIP de sinais no Telegram, tigrinho ao vivo, crie sua conta pelo link e comece a jogar",
    "Ganhei no Mines hoje, saque via pix caiu na conta. Cadastre-se no link na bio",
]
NEWS = ["Reportagem: projeto de lei prevê regulamentação das bets, segundo a polícia há investigação",
        "Entenda a ludopatia: vício em jogo destrói famílias. Procure ajuda. Jogo responsável",
        "Senado debate proibição das bets; ministério da fazenda comenta a lei das bets"]
WEAK = ["Vi um vídeo sobre bet hoje kkkk", "#tigrinho", "Sera que vale a pena apostar? minha opinião", "cassino online é golpe? reagindo"]


def rec(dom, aff=None, chain=()):
    url = f"https://{dom}/" + (f"?aff={aff}&utm_source=tiktok" if aff else "")
    return {"url_original": f"https://linktr.ee/x", "chain": list(chain), "url_final": url, "domain_final": dom,
            "params": urltools.parse_params(url), "params_raw": urltools.raw_query(url), "page_title": "", "page_text": "",
            "aggregator": "linktr.ee", "error": ""}


with db.connect() as c:
    s = db.get_settings(c)
    n = a.n
    for i in range(n):
        u = f"demo_perfil_{i:04d}"
        r = random.random()
        src = random.choice(["TikTok Search", "DuckDuckGo", "Bing", "hashtag", "lista importada", "expansão automática"])
        cid, _ = pipeline.upsert_candidate(c, u, source=src, query=random.choice(["tigrinho pagando", "\"link na bio\" saque", "#fortunetiger", "\"grupo vip\" sinais"]))
        if r < 0.55:  # promotores
            dom = random.choice(DOMS); aff = str(random.randint(100, 999))
            code = f"DEMO{random.randint(10, 99)}"
            for k in range(random.randint(1, 5)):
                pipeline.add_evidence(c, cid, "video", source=src, url_video=f"https://www.tiktok.com/@{u}/video/{i}{k:02d}",
                                      caption=random.choice(PROMO).format(code=code))
            c.execute("UPDATE candidates SET bio=?, display_name=? WHERE id=?", (f"Link na bio 👇 código {code}", f"Demo {i}", cid))
            if random.random() < 0.85:
                pipeline.save_links(c, cid, {"https://linktr.ee/x": [rec(dom, aff, ["https://bit.ly/demo"])]}, "bio", s)
        elif r < 0.75:  # jornalismo / educativo
            pipeline.add_evidence(c, cid, "video", source=src, url_video=f"https://www.tiktok.com/@{u}/video/{i}00", caption=random.choice(NEWS))
        elif r < 0.92:  # fracos
            pipeline.add_evidence(c, cid, "snippet", source=src, text=random.choice(WEAK))
        # else: sem evidência -> pendente
    pipeline.refresh_all(c, s)
    db.save_settings(c, {"request_delay": 0})
print(f"Banco de demonstração criado: {a.db} ({n} candidatos fictícios)")
