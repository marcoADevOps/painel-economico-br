# CLAUDE.md — Painel Econômico BR

Projeto de portfólio: pipeline de dados da economia brasileira com Apache Airflow. Leia `REQUIREMENTS.md` para o escopo completo, as fases e os critérios de aceite.

## Contexto do projeto
- Dono: Marco (GitHub: marcoadevops). Perfil DevOps com dados.
- Desenvolvimento em um laptop Windows, com clone local do repositório. Execução em produção numa VM Ubuntu 24.04 no Proxmox (homelab), sem IP público.
- **Nada roda no laptop** (sem Docker/WSL): lint, testes e contêineres rodam na VM ou no CI. O laptop só edita código e usa o git.
- Repositório **público**. Nunca commitar segredos, IPs internos, tokens, arquivos `.env`, `*.tfstate` ou `*.tfvars`.

## Stack
- Apache Airflow 3.x, `LocalExecutor`, TaskFlow API (`@dag`, `@task`).
- Postgres 16 (camadas `raw`, `staging`, `marts`).
- Docker e Docker Compose.
- Metabase para dashboard.
- GitHub Actions com runner self-hosted na VM, publicando imagens no GHCR.
- VM criada manualmente a partir do template cloud-init do Proxmox (ver `docs/setup-vm.md`).
- Alertas por Telegram; monitoramento no Uptime Kuma.

## Convenções
- Identificadores, comentários e mensagens de commit em inglês. README e docs em português.
- Um arquivo por DAG em `dags/`. Nenhuma lógica pesada no nível do módulo do DAG (o scheduler importa esses arquivos o tempo todo).
- Funções de extração, transformação e carga ficam em `src/` (testáveis sem o Airflow). Os DAGs só orquestram.
- Toda task deve ser **idempotente**: usar upsert por chave natural, nunca inserir cegamente.
- Usar `logical_date`/intervalo de dados do Airflow para definir o período processado. Nada de `datetime.now()` para isso.
- Guardar a resposta bruta em `raw` antes de transformar.
- Não passar volumes grandes por XCom; passar referências (tabela, intervalo).
- Conexões e credenciais via Airflow Connections/variáveis de ambiente, nunca hardcoded.

## Estrutura esperada
```
dags/            DAGs (um por fonte)
src/             extração, transformação, carga e checagens
sql/             DDL e transformações SQL
tests/           testes unitários e teste de importação dos DAGs
docker/          Dockerfile e scripts de init do Postgres
compose.yaml     stack (Airflow, Postgres; Metabase na fase 3)
.github/workflows/   CI (lint e testes) e deploy
docs/            diagrama de arquitetura e notas
```

## Windows (laptop de desenvolvimento)
- Usar finais de linha **LF** nos arquivos (criar `.gitattributes` com `* text=auto eol=lf`), porque os contêineres rodam Linux.
- Caminhos dentro dos contêineres e dos workflows sempre no formato Linux.

## Como trabalhar
- Entregar por fases, na ordem do `REQUIREMENTS.md` (MVP primeiro). Não antecipar fases futuras.
- Antes de usar uma série do Banco Central, uma tabela do IBGE ou um arquivo da ANP, conferir o código, o formato e os limites na documentação oficial. Não assumir.
- Rodar lint e testes antes de propor commit.
- Ao tocar em infra ou deploy, explicar o impacto e como reverter.
- Em caso de dúvida sobre escopo, perguntar antes de implementar.

## Comandos (na VM, dentro de `~/painel-economico-br`)
- Subir ambiente: `docker compose up -d --build --wait`
- Imagem de teste: `docker build -f docker/Dockerfile --target test -t painel-airflow:test .`
- Testes: `docker run --rm painel-airflow:test python -m pytest`
- Lint: `docker run --rm painel-airflow:test python -m ruff check .`
