# CIBERLAB — TIKTOK BET HUNTER

Sistema de **descoberta e correlação de publicidade de apostas** no TikTok: coleta centenas de candidatos,
pontua por *sinais combinados* (link + CTA + conteúdo + afiliado + recorrência), separa falsos positivos
(notícia, crítica, legislação, prevenção…), agrupa perfis por domínio/afiliado e leva o analista a uma lista
validada com evidência rastreável. **O score é apoio à triagem; a confirmação final é sempre humana.**

## Rodar no Windows

1. Instale o Python 3.10+ (python.org, marcando "Add Python to PATH").
2. Dê duplo clique em **`iniciar.bat`**: cria `.venv`, instala as dependências (e o `yt-dlp` opcional) só se faltarem e abre http://127.0.0.1:5000.

Manual (qualquer sistema): `python -m venv .venv` → ativar → `pip install -r requirements.txt` → `python run.py` (`--port`, `--db`, `--no-browser`).

| O quê | Onde |
|---|---|
| Banco SQLite (criado sozinho na 1ª execução; reiniciar/atualizar não apaga nada) | `data\bethunter.db` |
| Logs (fonte, consulta, erro; detalhes técnicos) | `logs\bethunter.log` + aba **LOG** da interface |
| Exportações (cópia salva a cada download) | `exportacoes\` |
| Dependências obrigatórias | `requirements.txt` (flask, requests, beautifulsoup4, openpyxl) |
| Opcionais | `requirements-opcional.txt` (yt-dlp) · Tesseract (instalador Windows) · `ANTHROPIC_API_KEY` |

**Opcionais** (o programa funciona sem qualquer um deles; a interface mostra INSTALADO/CONFIGURADO ou NÃO INSTALADO/NÃO CONFIGURADO):
copie `.env.example` para `.env` e preencha `ANTHROPIC_API_KEY=` para ativar a análise visual multimodal (a chave só é lida do ambiente, nunca vai para o banco/código); instale o Tesseract para OCR leve; o yt-dlp é instalado pelo `iniciar.bat`.
Rede: timeout, tentativas extras (padrão 2), intervalo mínimo entre requisições e uma requisição por vez são configuráveis em **CONFIG**. Bloqueios/captcha são registrados e a fonte seguinte continua; nada é contornado.

## TESTE LOCAL

1. Execute `iniciar.bat` e abra a aplicação.
2. Clique **TESTAR AMBIENTE**: veja INTERNET, DUCKDUCKGO, BING, TIKTOK, YT-DLP, ANTHROPIC, TESSERACT e BANCO.
3. Aba **CAÇAS** → execute uma caça (ex.: CAÇA 05) e acompanhe o painel; confira erros na aba **LOG**.
4. Verifique os candidatos em **RESULTADOS** (e **PENDENTES DE VALIDAÇÃO**).
5. **▶ REVISÃO ULTRARRÁPIDA**: `C` confirma, `D` descarta, `S` próximo, `X` expande, `Ctrl+Z` desfaz.
6. Em um confirmado, **EXPANDIR ESTE PERFIL**.
7. **EXPORTAR CONFIRMADOS** (CSV/XLSX); o arquivo também fica em `exportacoes\`.
8. Para volume: **▶ INICIAR MISSÃO**.

## Missão (fluxo principal)

**▶ INICIAR MISSÃO** (meta, profundidade 0–3, modo RÁPIDO/COMPLETO): executa caças + **matriz de consultas** (grupos A jogos × B CTA × C financeiro × D afiliados; termos genéricos nunca vão sozinhos) e transforma o que encontra em novas buscas (domínio, nome da plataforma, ID de afiliado, código, hashtags, jogo, frase da bio) até a profundidade escolhida. Segue até: meta de confirmados, pool qualificado (meta × 2,5), consultas esgotadas ou **INTERROMPER**. Fonte que falha 3 vezes seguidas é pausada (registrado no LOG) e as demais continuam.
RÁPIDO = texto/bio/URL/domínio/score. COMPLETO = também resolve redirecionamentos, coleta perfis e análise visual, **só dos candidatos promissores**. A meta mede o fluxo de análise: nada é confirmado automaticamente.
**REVISÃO ULTRARRÁPIDA**: `C` confirma · `D` descarta · `S` próximo · `E` evidências · `X` expandir · `Ctrl+Z` desfaz. **EXPORTAR CONFIRMADOS** gera CSV/XLSX da missão (NUMERO, USERNAME, URL_PERFIL, URL_VIDEO, DESCRICAO_EVIDENCIA, TEXTO_EVIDENCIA, PLATAFORMA, DOMINIO, URL_EXTERNA, CODIGO_AFILIADO, DATA_COLETA, OBSERVACAO_ANALISTA) e o arquivo completo. BAIXA RELEVÂNCIA fica registrada e oculta da revisão principal.

## Fluxo de trabalho recomendado

1. **CAÇAS** → *EXECUTAR HABILITADAS* (12 caças editáveis) e/ou **NOVA BUSCA** (livre, combinação de termos, lista de jogos, hashtags).
2. **INVESTIGAR PERFIL** (@user / URL) para perfis-semente → **ENCONTRAR PERFIS RELACIONADOS** (menções, domínio, código, hashtag, plataforma, jogo).
3. **IMPORTAR LISTA** (TXT/CSV/colar: usernames, URLs, domínios, #hashtags) — fallback para qualquer fonte que não puder ser consultada.
4. **▶ MODO REVISÃO RÁPIDA**: fila ordenada por prioridade; `C` confirma, `D` descarta, `S` pula, `O/V/L` abrem perfil/vídeo/link.
5. **CLUSTERS** para ver quantos perfis levam ao mesmo domínio/afiliado/código; **MODO MISSÃO** acompanha a meta (200) sem parar a coleta nela.
6. **EXPORTAR** CSV simplificado (encaminhamento), CSV/XLSX/JSON completo; botões de copiar perfis/usernames/vídeos.

## Regras de qualidade embutidas

* **Evidência mínima**: todo candidato tem indicador de descoberta + evidência registrada. Sem evidência → **PENDENTE DE VALIDAÇÃO** (aba própria, **não pode ser confirmado**, não entra em exportação de confirmados). Menção/relação sozinha não conta como evidência.
* **Nunca inventa dados**: campo indisponível = `NÃO IDENTIFICADO`. Perfil/URL/código só existem se foram coletados ou digitados.
* **Nada é apagado**: falso positivo vira `DESCARTADO` (e evita reanálise). Decisão manual do analista nunca é sobrescrita por reanálise.
* **Palavra isolada nunca basta** (`bet`, `cassino`, `#tigrinho` → BAIXA RELEVÂNCIA); CTA/afiliado/código só pontuam com contexto de apostas.
* **Cache**: item analisado nos últimos N dias mostra “ESTE ITEM JÁ FOI ANALISADO” (data, status, score, evidências) e permite reanalisar.
* **Dedupe** por username, ID de perfil, URL de vídeo (evidência) e URL final; perfis diferentes no mesmo domínio **não** são fundidos — formam **cluster**.
* **Cadeia de links** registrada: `URL_ORIGINAL → intermediárias (agregador/encurtador/redirects) → URL_FINAL / DOMINIO_FINAL / PARAMETROS_URL`; parâmetros (`aff`, `ref`, `code`, `promo`, `utm_*`, …) preservados com o valor original.

## Score (editável em CONFIG)

| Sinal | Pts | | Redutor | Pts |
|---|---|---|---|---|
| link externo p/ plataforma de aposta | +40 | | jornalismo/notícia | −40 |
| CTA explícito | +30 | | crítica às apostas | −40 |
| vídeo de jogo/aposta (gameplay) | +25 | | institucional/governamental | −35 |
| nome/logo de plataforma | +25 | | jurídico explicativo | −30 |
| saque/pagamento | +20 | | educativo/prevenção | −30 |
| parâmetro de afiliado | +20 | | só legislação | −25 |
| bônus/cupom/código | +15 | | comentário/reação sem divulgação | −20 |
| grupo Telegram/WhatsApp | +15 | | palavra-chave incidental | −20 |
| mesmo domínio em vários promotores | +15 | | | |
| hashtag / expressões / recorrência | +10 cada | | | |
| padrão promocional recorrente | +15 | | | |

`≥70` ALTA PROBABILIDADE DE DIVULGAÇÃO · `45–69` REVISAR · `<45` BAIXA RELEVÂNCIA (limites editáveis). Cada resultado mostra `MOTIVOS` linha a linha e o `TOTAL`.

## Fontes e limitações (leia)

| Fonte | Uso |
|---|---|
| Busca via DuckDuckGo / Bing (`site:tiktok.com …`) | @user, URL de vídeo e trecho como evidência |
| Busca pública do TikTok / página de hashtag | JSON público da página |
| Perfil/vídeo do TikTok (bio, link, vídeos, legenda) | página pública, oEmbed e `yt-dlp` quando instalado |
| Importação (TXT/CSV/colar), evidência manual, links de busca manuais | sempre disponíveis |

Fontes podem ser bloqueadas, mudar de layout ou exigir captcha: o erro vai para o LOG e as demais seguem. Use dentro dos termos das plataformas e da lei aplicável.

**Análise visual (opcional)**: quando há thumbnail, `VISUAL_ANALYSIS` usa a API multimodal (defina `ANTHROPIC_API_KEY`; modelo em `BETHUNTER_VISION_MODEL`) ou, se houver `tesseract` instalado, OCR leve. Sem nenhum dos dois o resultado é `não disponível` e o candidato segue normalmente; tags manuais (gameplay/logomarca/saque) aparecem como `manual`.

## Estrutura

```
run.py                     # servidor local
bethunter/config.py        # padrões: pesos, limites, léxicos, jogos, caças
bethunter/db.py            # SQLite (esquema, configurações)
bethunter/envcheck.py      # STATUS DO AMBIENTE / TESTAR AMBIENTE
bethunter/urltools.py      # parâmetros de afiliado, cadeia de redirecionamento, agregadores
bethunter/extract.py       # extração de sinais do texto
bethunter/scoring.py       # score explicável + classificador de falso positivo
bethunter/sources.py       # DuckDuckGo, Bing, TikTok, yt-dlp (opcional)
bethunter/mission.py       # matriz de consultas, derivação iterativa, missão
bethunter/visual.py        # VISUAL_ANALYSIS opcional (multimodal/OCR)
bethunter/pipeline.py      # candidatos, evidências, dedupe, investigar, expandir, caças, importação, jobs
bethunter/queries.py       # filtros, painel, clusters, métricas, missão
bethunter/exporter.py      # CSV/XLSX/JSON e listas para copiar
bethunter/web.py           # API Flask;  templates/ + static/ = interface
demo/seed_demo.py          # dados fictícios     tests/  # testes
```
