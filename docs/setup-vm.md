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
