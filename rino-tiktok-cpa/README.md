# RINO TikTok CPA Intelligence

Skill operacional para triagem OSINT de perfis do TikTok que divulgam apostas/CPA. Veja `SKILL.md` para as regras e o mapa dos scripts.

## Início rápido (Windows, Python 3.11+)

```bat
cd rino-tiktok-cpa
python -m venv .venv && .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m pytest -q tests
python run_mission.py --input links.txt --limit 3      # teste curto com o Chrome instalado
python run_mission.py --input links.txt --review       # rodada completa + revisão humana
```

Se o TikTok pedir verificação, rode sem `--headless` e use `--manual-wait 120` para resolver **você mesmo** no navegador;
nada é automatizado ou contornado. Bloqueios consecutivos encerram a rodada com o progresso salvo; execute de novo para retomar.

O login do TikTok (opcional) fica no perfil persistente `output/.chrome_profile`; credenciais nunca são lidas nem gravadas pela skill.

## Fluxo de revisão humana

1. `output/classificacao.csv` lista a classe automática, evidências e score técnico.
2. `python run_mission.py --input links.txt --review` (ou `classify_profiles.py decide @perfil CONFIRMAR|DESCARTAR|MANTER "motivo"`).
3. As decisões vão para `datasets/feedback.csv` e alimentam os datasets positivos/negativos.
4. `python scripts/export_takedown.py` exporta só CONFIRMADO + aprovado por humano.

## Demonstração offline

`python run_mission.py --input examples/links.txt --fixtures examples/fixtures.json --no-resolve` (dados fictícios).
