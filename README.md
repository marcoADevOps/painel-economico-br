# Painel Econômico BR

Pipeline de dados da economia brasileira (Banco Central, IBGE e ANP) orquestrado
pelo Apache Airflow 3, rodando 24/7 numa VM do homelab.

> Em construção. Escopo, fases e critérios de aceite em [REQUIREMENTS.md](REQUIREMENTS.md).

## Stack

- Apache Airflow 3.3 (`LocalExecutor`, TaskFlow API)
- Postgres 16: metadados do Airflow e data warehouse (`raw`, `staging`, `marts`)
- Docker Compose numa VM Ubuntu 24.04 no Proxmox

## Estrutura

```
dags/       DAGs (um por fonte), só orquestração
src/        extração, transformação e carga (testáveis sem o Airflow)
sql/        DDL e transformações SQL
tests/      testes unitários e de importação dos DAGs
docker/     Dockerfile e scripts de inicialização do Postgres
docs/       arquitetura e notas (ver docs/setup-vm.md)
compose.yaml
```

## DAGs

### `bcb_sgs`: Banco Central (SGS)

| Série | Código SGS | Periodicidade |
|---|---|---|
| Meta Selic definida pelo Copom (% a.a.) | 432 | diária |
| Dólar americano (venda), PTAX de fechamento | 1 | diária (dias úteis) |

- **Agendamento:** dias úteis às 19h (horário de Brasília), com `catchup`.
- **Janela processada:** os 10 dias anteriores à data lógica da execução. Assim entram dados publicados com atraso ou revisados, e um período com a VM desligada é preenchido quando ela volta.
- **Fluxo de dados:**
  - `raw.bcb_sgs_response` guarda a resposta JSON original de cada janela consultada;
  - `staging.bcb_sgs_observation` guarda os dados tipados, com chave (`series_code`, `ref_date`).
- **Idempotência:** as duas tabelas usam upsert pela chave natural, então reexecutar não cria linhas duplicadas.
- **Carga de histórico:** disparar o DAG manualmente com `start` e, se quiser, `end` nos parâmetros (formato `YYYY-MM-DD`). O período é consultado em janelas de 1 ano.

Particularidades da API do SGS, conferidas na documentação oficial e em testes:
- **Janela máxima:** séries diárias aceitam no máximo 10 anos por consulta. Uma janela maior, ou uma consulta sem datas, retorna HTTP 406.
- **Período sem dados:** retorna HTTP 404 com `Value(s) not found`. O pipeline trata isso como "sem dados", não como erro.
- **Instabilidade:** às vezes a API responde HTTP 200 com uma página HTML de erro. Por isso o conteúdo da resposta é validado e a task é repetida.

### `ibge_sidra`: IBGE (SIDRA)

| Conjunto | Tabelas SIDRA | Variáveis | Localidades | Periodicidade |
|---|---|---|---|---|
| `ipca` | 1419 (2012–2019) e 7060 (desde 2020) | 63 (variação mensal), 2265 (acumulada em 12 meses), índice geral | Brasil, 10 regiões metropolitanas e 6 capitais | mensal |
| `desocupacao` | 4099 (PNAD Contínua) | 4099 (taxa de desocupação) | Brasil, Grandes Regiões e UFs | trimestral |

- **Agendamento:** dias úteis às 10h (o IBGE divulga às 9h), com `catchup`.
- **Janela processada:** os últimos ~13 meses em toda execução, uma requisição por tabela.
- **Fluxo de dados:** a resposta original vai para `raw.ibge_sidra_response` e os dados tipados para `staging.ibge_sidra_observation`.
- **Chave natural:** o nível territorial faz parte da chave, porque o ID da localidade se repete entre níveis (Brasil e Norte são os dois `1`).
- **Sinais especiais do SIDRA:** `-` é zero absoluto. `X`, `..` e `...` não têm valor; essas linhas são descartadas e contadas no log.
- **Carga de histórico:** disparar manualmente com `start` (os dados começam em 2012).

### `anp_precos`: ANP (preços de combustíveis)

Preço médio **semanal por município pesquisado e produto** (gasolina, gasolina aditivada, etanol, diesel, diesel S10, GNV e GLP), desde 2016. A pesquisa da ANP cobre uma amostra de ~450 a 520 municípios.

- **Fonte:** os CSVs de dados abertos da ANP, com uma linha por posto, produto e data de coleta. O DAG **lê a página oficial e descobre os links**, porque os nomes dos arquivos são irregulares (erros de digitação, com e sem ano, sem extensão, um semestre em `.zip`).
- **Sem dupla contagem:** a ANP publica os mesmos dados em arquivos mensais e em semestrais consolidados. Quando existe o semestral de um período, os mensais desse período são ignorados; os mensais só completam o semestre corrente.
- **`raw`:** os arquivos originais ficam em disco, compactados (`.csv.gz`, ~200 MB para 2016–2026), e a tabela `raw.anp_file` registra URL, SHA-256 e tamanho de cada um. Assim o backup diário do banco continua pequeno, e os arquivos originais são copiados de forma incremental.
- **`staging.anp_fuel_price_weekly`:** soma, mínimo, máximo e número de coletas por semana (domingo a sábado), município e produto, **por arquivo de origem**. Uma semana que cruza a virada do mês aparece em dois arquivos mensais, e os marts juntam as duas partes.
- **Agendamento:** toda segunda às 11h. Arquivos recentes (últimos 60 dias) são baixados de novo para detectar republicação pelo SHA-256; os antigos que já estão guardados são pulados.
- **Capacidade:** no máximo 2 arquivos são processados ao mesmo tempo (o pico medido foi de ~450 MB), e downloads cortados pelo servidor são retomados com HTTP Range.
- **Lacuna conhecida:** o 1º semestre de 2022 dos combustíveis automotivos (`ca-2022-01`) não está publicado pela ANP.

### `marts`: tabelas para análise

O DAG é disparado por Assets do Airflow sempre que `bcb_sgs`, `ibge_sidra` **ou** `anp_precos` terminam de atualizar a staging. Com a condição OU, uma fonte com falha não segura a atualização da outra, e a reconstrução leva segundos. Cada tabela é refeita numa única transação (`TRUNCATE` + `INSERT`), então quem consulta nunca vê uma tabela pela metade e reexecutar é sempre seguro.

| Tabela | Grão | Conteúdo |
|---|---|---|
| `marts.monthly_indicators` | mês | Selic meta no fim do mês, PTAX média e de fechamento, IPCA Brasil (mensal e em 12 meses) e juro real ex-post: (1 + Selic) / (1 + IPCA 12m) − 1 |
| `marts.ipca_by_region` | mês × localidade | IPCA mensal e em 12 meses do Brasil, regiões metropolitanas e capitais |
| `marts.unemployment_by_region` | trimestre × localidade | Taxa de desocupação do Brasil, Grandes Regiões e UFs |
| `marts.fuel_prices_weekly` | semana × nível × produto | Preço médio, mínimo, máximo e número de coletas para Brasil, UF e município, com média ponderada pelas coletas |

O IPCA das tabelas do SIDRA usadas aqui começa em 2012. Por isso, antes de 2012, `monthly_indicators` tem Selic e PTAX, mas não tem IPCA nem juro real.

## Dashboards (Metabase)

Os dashboards são definidos **como código** em `ops/metabase/provision.py` e lêem só a camada `marts`, com um usuário somente leitura.

| Dashboard | Conteúdo |
|---|---|
| Indicadores macroeconômicos | Selic meta, IPCA 12 meses, juro real ex-post e PTAX (valores atuais e séries históricas) |
| Inflação e desemprego por região | IPCA 12 meses por região metropolitana e capital; desocupação por UF e por Grande Região |
| Combustíveis | preço médio semanal de gasolina, etanol, diesel S10 e GLP; gasolina por UF; os 10 municípios mais caros e os 10 mais baratos |

## Qualidade de dados

Os dois DAGs de origem rodam uma task `quality_checks` depois de carregar a staging e **antes** de publicar o Asset que dispara os marts. Se alguma checagem falhar, a task falha na hora, sem novas tentativas (repetir não conserta dado ruim), o alerta vai para o Telegram e **os marts não são atualizados com o dado suspeito**.

| Checagem | BCB | IBGE | ANP |
|---|---|---|---|
| Duplicados | uma linha por série e data | uma linha por conjunto, variável, categoria, nível, localidade e período | uma linha por semana, município, produto e arquivo |
| Nulos | colunas obrigatórias preenchidas | idem | idem, e pelo menos uma coleta |
| Faixa plausível | Selic entre 0 e 50% a.a.; PTAX entre R$ 0,50 e R$ 20 | IPCA mensal entre −5% e 10%; IPCA 12 meses entre −10% e 100%; desocupação entre 0 e 40% | média semanal entre R$ 0,50 e R$ 20 (combustíveis líquidos e GNV) ou entre R$ 20 e R$ 300 (GLP 13 kg) |
| Atraso | Selic e PTAX com no máximo 7 dias | IPCA Brasil com no máximo 80 dias; desocupação com no máximo 240 dias (a data de referência é o início do período) | semana mais recente com no máximo 100 dias |

O atraso é medido em relação à data da execução (`as_of`), nunca em relação a "agora". Cada checagem é uma consulta SQL em `src/painel/quality.py` que retorna as linhas problemáticas: zero linhas significa que passou.

## Alertas

Quando uma task falha de vez, sem novas tentativas restantes, o `on_failure_callback`
(`src/painel/alerts.py`) envia uma mensagem no Telegram. Ela traz o DAG, a task, a
execução, a tentativa, o erro resumido e um link para o log.

- O token do bot e o chat ID ficam apenas no `.env` da VM (`TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`). Sem eles, os alertas são ignorados e o DAG não é afetado.
- Um erro no envio nunca derruba a task e nunca registra o token nos logs.
- Parâmetros inválidos na carga manual falham na hora, sem novas tentativas, e o
  alerta chega em segundos. Para testar os alertas, dispare `bcb_sgs` com `start`
  depois de `end`.

## Como rodar

Tudo roda na VM, dentro de contêineres (ver [docs/setup-vm.md](docs/setup-vm.md)):

```bash
cp .env.example .env    # preencher os segredos
docker compose up -d --build --wait
```

Lint e testes, na imagem de teste:

```bash
docker build -f docker/Dockerfile --target test -t painel-airflow:test .
docker run --rm painel-airflow:test python -m ruff check .
docker run --rm painel-airflow:test python -m pytest
```
