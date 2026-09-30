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

## 3. Subir o ambiente

```bash
cd ~/painel-economico-br
cp .env.example .env   # preencher; chmod 600 .env
docker compose up -d --build --wait
```

A interface do Airflow fica em `http://<VM_IP>:8080`.
