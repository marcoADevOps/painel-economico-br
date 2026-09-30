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
