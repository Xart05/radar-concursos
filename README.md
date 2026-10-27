# Radar de Concursos

Painel de concursos administrativos de nível médio do Ceará e nacionais.

**Site:** https://xart05.github.io/radar-concursos/

## Como funciona

Toda segunda às 8h (horário de Fortaleza) o GitHub roda `scripts/atualizar.py` de graça:

1. Lê os concursos abertos do Ceará e os nacionais no [PCI Concursos](https://www.pciconcursos.com.br/concursos/nordeste/#CE).
2. Abre a notícia de cada concurso que aceita nível médio e procura cargos administrativos
   (agente, assistente ou auxiliar administrativo, assistente em administração, secretário escolar etc.).
3. Grava a lista em `site/dados/concursos.json` e publica o site de novo.

Critérios:

- **Ceará:** qualquer salário, a partir do piso de R$ 1.621.
- **Nacionais:** salário a partir de R$ 2.500, com vagas no Ceará ou no país todo.
- Ficam de fora os concursos de banco e as seleções temporárias.

As marcações no painel (situação, matérias estudadas, anotações) ficam salvas no navegador de quem usa.
Use o botão **Copiar backup** de vez em quando.

## O que o script não faz

- **Concursos previstos** (UFC, INSS, Correios etc.) não aparecem nas listas do PCI. Eles são acrescentados à mão.
- **Salário do cargo:** quando a notícia não traz o valor de cada cargo, o painel mostra só a faixa do edital.
- **Matérias:** os concursos achados pelo script recebem uma lista padrão. Os do edital são corrigidos à mão.

## Rodar a atualização na hora

Aba **Actions** → **Atualizar concursos** → **Run workflow**.

## Acrescentar ou corrigir um concurso à mão

1. Abra `site/dados/concursos.json` no GitHub e clique no lápis (Edit).
2. Copie um bloco `{ ... }` parecido, cole e mude os campos. Use datas no formato `AAAA-MM-DD`.
3. Deixe `"origem": "manual"`: assim o script nunca apaga nem sobrescreve o concurso, só avisa se o prazo mudar no PCI.
4. Clique em **Commit changes**. O site é publicado de novo em cerca de 1 minuto.

Campos principais: `id` (único, sem espaços), `orgao`, `cargo`, `uf` (`CE` ou `BR`), `tipo` (`aberto` ou `previsto`),
`salario`, `vagas`, `banca`, `inscricoesInicio`, `inscricoesFim`, `prova`, `link`, `materias`, `alerta`, `arquivado`.

Para tirar um concurso do painel sem apagar, troque `"arquivado": false` por `"arquivado": true`.

## Se a atualização falhar

O GitHub manda um e-mail quando a execução de segunda falha. O motivo mais comum é o PCI Concursos ter mudado o layout da página.
O site continua no ar com a lista da semana anterior. Para consertar, cole a mensagem de erro (aba Actions) e o arquivo
`scripts/atualizar.py` numa conversa com o Claude e peça o ajuste.

## Testar no computador

```bash
python3 scripts/atualizar.py --simular
```
