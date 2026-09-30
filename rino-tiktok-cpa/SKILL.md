---
name: rino-tiktok-cpa
description: Investigação e triagem OSINT de perfis do TikTok que divulgam plataformas de apostas ("chinesas", "gringas"), CPA, links de afiliado, códigos de convite, bônus, cassino/slots e renda por indicação. Use para normalizar links do TikTok, coletar perfis com retomada, capturar evidências, resolver encurtadores, detectar referrals, classificar com justificativa, clusterizar, registrar revisão humana e exportar takedown. Nome de exibição - RINO TikTok CPA Intelligence.
---

# RINO TikTok CPA Intelligence

Fluxo: DESCOBERTA → NORMALIZAÇÃO → COLETA → EVIDÊNCIA → INDICADORES → LINKS/REDIRECTS → AFILIADOS/REFERRAL →
CLUSTERS → CLASSIFICAÇÃO → **REVISÃO HUMANA** → EXPORTAÇÃO.

## Execução

```bash
pip install -r requirements.txt                       # Chrome local é usado; não rode "playwright install"
python run_mission.py --input links.txt               # retoma sozinho se interrompido
python run_mission.py --input links.txt --review      # abre a revisão humana ao final
python scripts/classify_profiles.py decide @perfil CONFIRMAR "link leva a cassino com referral"
python scripts/export_takedown.py                     # só perfis aprovados por humano
python run_mission.py --input examples/links.txt --fixtures examples/fixtures.json   # demo offline
python -m pytest -q tests
```

`links.txt`: uma URL por linha (`/video/`, `/photo/`, perfil) ou `@usuario`. Tudo vira `https://www.tiktok.com/@usuario`,
sem duplicatas; a URL original é preservada.

## Regras inegociáveis

1. **Indicador ≠ prova.** Username, hashtag, "chinesa/gringa/CPA/casino/slots" ou menção em vídeo nunca classificam sozinhos.
   Procure promoção explícita, link para plataforma, referral/convite, instrução de cadastro.
2. **Na dúvida, REVISÃO HUMANA.** Jornalismo, denúncia e crítica reduzem o score e limitam a classe a REVISÃO HUMANA.
3. **Nada entra em `takedown.csv` sem decisão humana `CONFIRMADO`** em `datasets/feedback.csv`. A decisão humana sempre prevalece.
4. **Dado ausente é `null`/"NÃO IDENTIFICADO"**, nunca zero (seguidores "-" → `followers = null`; alcance mínimo soma só os conhecidos).
5. **Coleta passiva**: só conteúdo público. Sem burlar login/CAPTCHA/rate limit (bloqueio → pausa + retomada), sem cadastro, depósito,
   aposta, credenciais ou dados privados. Links externos só por GET.
6. **Precisão e rastreabilidade**: toda classificação cita suas evidências; score é apoio, não verdade.
7. Persistência após **cada** perfil (`output/index_perfis.csv`); rodar de novo continua só os pendentes.

## Mapa

| Etapa | Script |
|---|---|
| Normalização | `scripts/normalize_tiktok.py` |
| Coleta (1 sessão Chrome persistente, fecha modal de login, screenshot) | `scripts/collect_profiles.py` |
| Retomada / salvamento por perfil / backoff em bloqueios | `scripts/resume_collection.py` |
| Cadeia de redirects, hubs (Linktree, Beacons…) | `scripts/resolve_urls.py` |
| Termos, referrals (`inviter`, `ref`, `referral`, `affiliate`, `agent`, `invite_code`, `pid`…), evidências por perfil | `scripts/extract_indicators.py` |
| Classificação + feedback + golden set | `scripts/classify_profiles.py` |
| Clusters | `scripts/cluster_profiles.py` |
| Takedown | `scripts/export_takedown.py` |
| Relatório da missão | `scripts/build_report.py` |

Conhecimento editável: `knowledge/vocabulary.md` (cresce com `extract_indicators.py --expand`), `knowledge/platforms.json`
(só domínios verificados), `knowledge/indicators.md` (pesos e regras), `knowledge/regulation.md` (preencher com fonte oficial).

## Golden set

`datasets/positivos_confirmados.csv`, `negativos_confirmados.csv`, `duvidosos.csv`, `feedback.csv`. Cada decisão humana
(`CONFIRMAR`/`DESCARTAR`) é gravada em `feedback.csv` (`perfil,predicao,decisao_humana,motivo`) e promovida ao dataset
correspondente. O aprendizado usa **bio, links, conteúdo, hashtags, domínio final e códigos** — nunca o username.
O objetivo é separar: (a) mera menção, (b) jornalismo/crítica/denúncia, (c) promoção/facilitação de acesso.

## Saídas

`output/`: `candidates.csv`, `index_perfis.csv`, `screenshots/NNN_usuario_perfil.png`, `evidence/<usuario>/{profile.png,metadata.json,
external_links.json,redirects.json,analysis.json}`, `classificacao.csv`, `clusters.{json,csv}`, `takedown.csv`, `relatorio_missao.{txt,json}`.

## Limite conhecido

Os seletores do TikTok (`data-e2e`) e o fechamento do modal não foram validados contra o TikTok real no ambiente de
desenvolvimento; o parser é coberto por HTML de exemplo e há fallback por JSON embutido. Valide com `--limit 3` antes de lotes grandes.
