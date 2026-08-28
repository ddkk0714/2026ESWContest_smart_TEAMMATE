# Install Docker On Ubuntu

Use these commands on Ubuntu to install Docker Engine and Docker Compose plugin:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
newgrp docker
docker --version
docker compose version
```

# Container Guide

This guide explains how to build, start, and enter the ATLAS development container in this workspace.

## Requirements

- Docker installed
- Docker Compose available as `docker compose`
- Run commands from the workspace root

## Files Used

- `Dockerfile`
- `docker-compose.yml`

## Build The Image

```bash
docker compose build --no-cache
```

If you do not need a clean rebuild:

```bash
docker compose build
```

## Create And Start The Container

```bash
docker compose up -d
```

This will:
- build or reuse the `atlas-dev:latest` image
- create the `atlas-dev` container
- mount the current workspace into `/app`
- use host networking

## Enter The Container

```bash
docker compose exec atlas-dev bash
```

## Stop And Remove The Container

```bash
docker compose down
```

## Rebuild After Dockerfile Changes

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

## Direct Docker Run Alternative

Build the image:

```bash
docker build -t atlas-dev:latest .
```

Run the container:

```bash
docker run -it \
  --name atlas-dev \
  --network host \
  -v "$PWD:/app" \
  -w /app \
  atlas-dev:latest \
  bash
```

If you want the container to stay running in the background:

```bash
docker run -d \
  --name atlas-dev \
  --network host \
  -v "$PWD:/app" \
  -w /app \
  atlas-dev:latest \
  sleep infinity
```

Then enter it with:

```bash
docker exec -it atlas-dev bash
```

## Notes

- `network_mode: host` means published ports are not needed.
- Host networking works as expected on native Linux/WSL2.
- On Docker Desktop for Windows or macOS, host networking behavior is limited.
- The workspace is mounted into `/app`, so changes on the host are visible inside the container.
