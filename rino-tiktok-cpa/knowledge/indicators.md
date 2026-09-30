# Indicadores e pesos

O score técnico é uma **ajuda de triagem**, não prova. Toda classificação lista as evidências que a sustentam.

| Indicador | Peso | Observação |
|---|---|---|
| Link final leva a plataforma de apostas | +30 | domínio em `platforms.json`, palavra-chave de domínio, domínio aprendido do golden set ou landing page com ≥2 termos de tema |
| Código de afiliado/referral | +25 | parâmetro na URL (`inviter`, `ref`, `referral`, `affiliate`, `agent`, `invite`, `invite_code`, `pid`, `promo`, `partner`) ou "código: XXXX" no texto |
| Bio promove ganhos | +20 | termos da categoria *Ganho* na bio |
| Vídeo promove plataforma | +20 | termos de *Ganho*, *Cadastro* ou *Plataforma* no conteúdo dos vídeos/OCR/transcrição |
| Instrução de cadastro | +15 | termos da categoria *Cadastro* |
| "CPA chinês" | +10 | |
| "plataforma chinesa" | +10 | |
| Username relacionado | +5 | apenas indicador |
| Texto similar a perfil confirmado | +10 | similaridade de conteúdo (nunca username) |
| Texto similar a perfil descartado | −10 | |
| Conteúdo jornalístico | −30 | |
| Denúncia | −30 | "não é golpe" não conta como denúncia |
| Conteúdo crítico | −30 | |
| Mera menção sem promoção | −20 | tema presente, sem CTA, sem link, sem código |
| Sem link e sem CTA | −20 | |

## Regras de decisão

- **CONFIRMADO (automático, técnico)**: link final para plataforma **e** (código de afiliado **ou** promoção+instrução de cadastro), score ≥ 60 e nenhum sinal negativo de conteúdo.
- **PROVÁVEL**: score ≥ 40 com ao menos um pilar forte (link, código ou promoção+cadastro).
- **REVISÃO HUMANA**: há indicadores mas faltam pilares; ou há sinal negativo de conteúdo junto com sinais positivos (limite máximo nesse caso).
- **DESCARTADO**: sem indicadores relevantes, conta indisponível ou score < 15.
- Nada vai para `takedown.csv` sem decisão humana `CONFIRMADO` em `datasets/feedback.csv`. A decisão humana sempre prevalece sobre o automático.
- Username **nunca** decide sozinho.
