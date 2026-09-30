# Provisionamento da VM

A VM `airflow-01` é criada manualmente no Proxmox, clonando um template
cloud-init do Ubuntu 24.04. Os comandos abaixo usam placeholders: IPs e IDs
reais ficam fora deste repositório público.

| Recurso | Valor |
|---|---|
| vCPU | 2 |
| RAM | 4 GB |
| Disco | 30 GB (`local-lvm`) |
| SO | Ubuntu 24.04 LTS (cloud-init, usuário `deploy`) |
| Início automático | sim (`onboot=1`) |

## 1. Criar a VM (no host Proxmox)

```bash
qm clone <TEMPLATE_ID> <VM_ID> --name airflow-01 --full 1 --storage local-lvm
qm set <VM_ID> --cores 2 --memory 4096 --onboot 1 --agent enabled=0 \
  --ipconfig0 ip=<VM_IP>/24,gw=<GATEWAY> --nameserver "1.1.1.1 8.8.8.8"
qm resize <VM_ID> scsi0 30G
qm start <VM_ID>
```

Reverter: `qm stop <VM_ID> && qm destroy <VM_ID> --purge`.

## 2. Instalar o Docker (na VM)

Repositório oficial do Docker, conforme a documentação para Ubuntu:

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker deploy
```

Rotação de logs dos contêineres, para não encher o disco (`/etc/docker/daemon.json`):

```json
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
```

## 3. Runner self-hosted do GitHub Actions

O runner roda com um usuário dedicado (`gh-runner`, no grupo `docker`) e é
registrado só neste repositório. Como o repositório é público, os workflows
usam o runner apenas em push na `main` e em execuções manuais; PRs rodam em
runners hospedados pelo GitHub. Em **Settings → Actions → General**, exigir
aprovação para workflows de PRs de forks.

```bash
sudo useradd -m -s /bin/bash -G docker gh-runner
# como gh-runner, em ~/actions-runner: baixar a versão atual do runner
# (github.com/actions/runner/releases), conferir o SHA-256 e extrair
sudo -u gh-runner bash -c 'cd ~/actions-runner && ./config.sh --unattended \
  --url https://github.com/<OWNER>/painel-economico-br --name airflow-01 \
  --labels painel,airflow-01 --token <TOKEN>'
# o home do gh-runner é 750: rodar o svc.sh como root, de dentro da pasta
sudo bash -c 'cd /home/gh-runner/actions-runner && ./svc.sh install gh-runner && ./svc.sh start'
```

## 4. Diretório de deploy

O deploy (`.github/workflows/ci-cd.yml`) roda em `/opt/painel`. O `.env` com
os segredos é criado uma única vez ali e nunca é sobrescrito pelo workflow:

```bash
sudo install -d -o gh-runner -g gh-runner -m 750 /opt/painel
sudo install -o gh-runner -g gh-runner -m 600 .env /opt/painel/.env
```

A cada push na `main`, o workflow roda lint e testes, publica a imagem no
GHCR, copia o `compose.yaml` para `/opt/painel` e sobe a stack. A interface
do Airflow fica em `http://<VM_IP>:8080`.

Rollback: em `/opt/painel/.env`, trocar `AIRFLOW_IMAGE` por uma tag anterior
(`ghcr.io/<owner>/painel-economico-br/airflow:<sha>`) e rodar
`docker compose up -d`.

## 5. Backup diário do Postgres

O backup roda **fora do Airflow**, por timers do systemd, para funcionar mesmo com o Airflow fora do ar. Os arquivos ficam em `ops/backup/`.

| Onde | O quê | Quando | Retenção |
|---|---|---|---|
| VM | `painel-backup.timer`: `pg_dump` (formato custom) dos bancos `airflow` e `painel`, mais as roles; confere se cada dump pode ser lido e grava `SHA256SUMS` em `/var/backups/painel/<data>/` | 03:30 | 7 dias |
| Host Proxmox | `painel-backup-pull.timer`: puxa as cópias por `rsync`, confere as somas e guarda em `/var/backups/painel-vm/` | 04:30 | 14 dias |

Como o acesso do host à VM é restrito:
- o host usa uma chave dedicada, e na VM o usuário `painelbak` só aceita `rrsync -ro` na pasta de backup (sem shell e sem escrita);
- a VM não guarda nenhuma credencial do host;
- a cópia não usa `--delete`, então apagar os backups na VM não apaga as cópias do host.

```bash
# VM
sudo groupadd --system painelbak
sudo useradd --system --gid painelbak --home-dir /var/lib/painelbak --create-home --shell /bin/sh painelbak
sudo install -m 750 ops/backup/backup-postgres.sh /usr/local/sbin/painel-backup-postgres
sudo install -m 644 ops/backup/painel-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now painel-backup.timer
# authorized_keys do painelbak (chave pública gerada no host):
#   restrict,command="/usr/bin/rrsync -ro /var/backups/painel" ssh-ed25519 AAAA... proxmox-painel-backup-pull

# Host Proxmox
ssh-keygen -t ed25519 -N "" -f /root/.ssh/painel_backup_pull
install -m 750 ops/backup/proxmox/painel-backup-pull.sh /usr/local/sbin/painel-backup-pull
install -m 644 ops/backup/proxmox/painel-backup-pull.{service,timer} /etc/systemd/system/
echo "VM_HOST=<VM_IP>" > /etc/painel-backup-pull.env && chmod 600 /etc/painel-backup-pull.env
systemctl daemon-reload && systemctl enable --now painel-backup-pull.timer
```

Restauração (testada em 2026-09-30 num banco temporário, com as mesmas contagens do original):

```bash
docker exec painel-postgres-1 sh -c 'createdb -U "$POSTGRES_USER" restore_test'
sudo cat /var/backups/painel/<data>/painel.dump \
  | docker exec -i painel-postgres-1 sh -c 'pg_restore -U "$POSTGRES_USER" -d restore_test --no-owner'
```

Reverter: `systemctl disable --now painel-backup.timer` na VM e `painel-backup-pull.timer` no host.

## 6. Monitoramento (Uptime Kuma)

O Uptime Kuma roda num LXC separado (`uptime-01`), fora da VM do Airflow. Assim ele continua avisando quando a própria VM cai.

```bash
# Host Proxmox: LXC Debian 12 sem privilégios (nesting para Docker)
pct create <CT_ID> local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst \
  --hostname uptime-01 --cores 1 --memory 512 --swap 256 --rootfs local-lvm:8 \
  --unprivileged 1 --features nesting=1,keyctl=1 --onboot 1 \
  --net0 name=eth0,bridge=vmbr0,ip=<KUMA_IP>/24,gw=<GATEWAY>
# No LXC: Docker (repositório oficial para Debian) e depois
cd /opt/uptime-kuma && docker compose up -d   # ops/uptime-kuma/compose.yaml
```

Monitores configurados na interface (`http://<KUMA_IP>:3001`):

| Monitor | Tipo | Alvo | Regra |
|---|---|---|---|
| Airflow scheduler | HTTP(s) - Json Query | `http://<VM_IP>:8080/api/v2/monitor/health` | `scheduler.status` == `healthy` |
| VM airflow-01 | Ping | `<VM_IP>` | responde ao ping |
| Backup Postgres | Push | URL gerada pelo Kuma, gravada em `/etc/painel-backup.env` na VM | um push a cada 25 h, no máximo |

As notificações vão para o mesmo bot do Telegram usado nos alertas do Airflow.

Reverter: `pct destroy <CT_ID>` no host.
