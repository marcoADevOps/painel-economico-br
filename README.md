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
