# Painel Econômico BR — Requisitos (v1)

## 1. Objetivo

Pipeline de dados da economia brasileira orquestrado pelo Apache Airflow, rodando 24/7 em uma VM dedicada no homelab (Proxmox), com deploy automatizado por GitHub Actions e dashboard com atualização automática.

Projeto de portfólio para o perfil DevOps/DataOps. O repositório é **público**, então nada sensível pode ir para o Git.

## 2. Escopo

### Dentro do escopo
- Ingestão agendada de indicadores públicos (Banco Central, IBGE, ANP).
- Armazenamento em Postgres em camadas (`raw`, `staging`, `marts`).
- Checagens de qualidade de dados.
- Dashboard em Metabase.
- Alertas de falha no Telegram.
- Deploy via GitHub Actions; ambiente descrito em Docker Compose.
- Documentação de portfólio (README, diagrama, prints).

### Fora do escopo (por enquanto)
- Exposição do dashboard para a internet (avaliar depois, com Cloudflare Tunnel e autenticação).
- Modelos de previsão/machine learning.
- Executors distribuídos (Celery/Kubernetes). Usar `LocalExecutor`.

## 3. Fontes de dados

| Fonte | Indicadores | Acesso | Frequência sugerida |
|---|---|---|---|
| Banco Central (SGS) | Selic, IPCA, dólar PTAX | API JSON | Diária (câmbio/Selic), mensal (IPCA) |
| IBGE (SIDRA) | IPCA por região, taxa de desocupação | API | Mensal / trimestral |
| ANP | Preço semanal de combustíveis | Provavelmente planilha (confirmar) | Semanal |

Pontos a confirmar na implementação (não assumir):
- Códigos das séries SGS (ex.: 432 Selic meta, 433 IPCA mensal, 1 dólar venda) e tabelas SIDRA (ex.: 7060 IPCA, 4099 desocupação) no catálogo oficial de cada órgão.
- Limites de consulta por janela de datas das séries diárias do SGS.
- Formato, URL estável e periodicidade real dos arquivos da ANP.

## 4. Requisitos funcionais

- **RF1 — Ingestão agendada:** um DAG por fonte, com frequência adequada ao indicador.
- **RF2 — Camadas no Postgres:**
  - `raw`: resposta bruta, com data/hora de carga.
  - `staging`: dados limpos e tipados.
  - `marts`: tabelas prontas para análise e dashboard.
- **RF3 — Idempotência:** rodar a mesma execução duas vezes não duplica dados (upsert por chave natural).
- **RF4 — Backfill:** carga do histórico e recuperação automática de execuções perdidas (`catchup`).
- **RF5 — Qualidade de dados:** checagens de nulos, duplicados, valores fora de faixa plausível e atraso de atualização. Falha de checagem falha a task.
- **RF6 — Alertas:** falha de DAG envia mensagem no Telegram com nome do DAG, task e link do log.
- **RF7 — Dashboard:** Metabase com séries históricas (Selic, IPCA, dólar, desocupação, combustíveis) lendo apenas a camada `marts`.

## 5. Requisitos não funcionais

- **Stack:** Airflow 3.x com `LocalExecutor`, Postgres 16, Docker Compose, Metabase.
- **Infra:** VM dedicada no Proxmox, **4 GB de RAM, 2 vCPUs e 30 GB de disco**, IP fixo, Ubuntu 24.04. Criada manualmente a partir do template cloud-init (`docs/setup-vm.md`); o `homelab-infra` foi desativado em 2026-09-30.
- **Deploy:** GitHub Actions com runner self-hosted. Deploy automático ao fazer merge na `main`.
- **Segredos:** nunca no repositório. Usar GitHub Secrets e arquivo `.env` fora do Git. Incluir `.env.example` sem valores reais.
- **Confiabilidade:** backup diário do Postgres, healthchecks nos contêineres, monitoramento no Uptime Kuma.
- **Observabilidade:** logs do Airflow acessíveis pela interface; retenção definida.
- **Código:** identificadores e comentários em inglês; documentação em português.
- **Testes:** testes unitários das funções de transformação e um teste que carrega todos os DAGs sem erro de importação.

## 6. Plano de entrega

### MVP
- Airflow no ar na VM, com deploy automático.
- Um DAG do Banco Central (Selic e PTAX) gravando em `raw` e `staging`.
- Alerta de falha no Telegram.

### Fase 2
- DAG do IBGE (IPCA por região, desocupação).
- Camada `marts`.
- Checagens de qualidade.
- Backup do Postgres e monitoramento no Uptime Kuma.

### Fase 3
- DAG da ANP.
- Dashboard no Metabase.
- Documentação final: README, diagrama de arquitetura, prints, lições aprendidas.

## 7. Critérios de aceite

- Um `git push` na `main` atualiza o ambiente sem intervenção manual.
- Desligar e religar a VM não perde dados nem deixa lacunas no histórico após o `catchup`.
- Falha proposital em um DAG gera alerta no Telegram em poucos minutos.
- Reexecutar qualquer DAG não gera linhas duplicadas.
- O dashboard mostra dados atualizados sem ação manual.
- O repositório público não contém segredos, IPs internos nem estado do Terraform.

## 8. Riscos

| Risco | Mitigação |
|---|---|
| RAM insuficiente no mini PC (16 GB compartilhados) | Conferir alocação atual antes de criar a VM; começar com 3 GB no MVP se necessário |
| Mudança de formato ou indisponibilidade de uma fonte | Guardar a resposta bruta em `raw`; alertar em falha; tratar cada fonte de forma isolada |
| Vazamento de segredo em repositório público | `.gitignore` desde o primeiro commit; `.env.example`; revisar histórico antes de publicar |
| Airflow 3 com material de estudo desatualizado (maioria é do Airflow 2) | Conferir a versão da documentação antes de copiar exemplos |
