#!/usr/bin/env bash
# Docker Engine в WSL2 без Docker Desktop — для Windows, где Docker Desktop ставить не хочется или некуда.
#
# В Windows (PowerShell; компонент «Платформа виртуальной машины» должен быть включён; в командном репозитории
# скрипт лежит в ml-service/tools/):
#   wsl --install Ubuntu-24.04 --name inspector-docker --location D:\wsl\inspector-docker --web-download --no-launch
#   wsl -d inspector-docker -u root -- bash /mnt/d/<путь к репозиторию>/tools/wsl_docker.sh
# Диск дистрибутива (и все образы Docker) — в папке --location, на C: место не расходуется.
# Потом: wsl -d inspector-docker -u root, внутри — docker compose up --build в папке репозитория.
# Порты контейнеров видны в Windows как localhost:<порт>.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if ! command -v docker >/dev/null; then
    apt-get update -q
    # docker-buildx — BuildKit (docker compose build без него уходит на устаревший сборщик)
    apt-get install -y -q --no-install-recommends docker.io docker-compose-v2 docker-buildx git unzip curl ca-certificates
fi

if [ "$(ps -p 1 -o comm=)" = "systemd" ]; then
    systemctl enable --now docker
elif ! docker info >/dev/null 2>&1; then
    service docker start || (nohup dockerd >/var/log/dockerd.log 2>&1 &)
fi
for _ in $(seq 60); do docker info >/dev/null 2>&1 && break; sleep 1; done

# Если Docker Hub из этой сети недоступен (соединение с registry-1.docker.io зависает), образы docker.io берутся
# через зеркало Google — mirror.gcr.io
if ! timeout 10 curl -4 -s -o /dev/null https://registry-1.docker.io/v2/ && [ ! -f /etc/docker/daemon.json ]; then
    echo "Docker Hub недоступен — включаю зеркало mirror.gcr.io"
    printf '{\n  "registry-mirrors": ["https://mirror.gcr.io"]\n}\n' > /etc/docker/daemon.json
    systemctl restart docker 2>/dev/null || service docker restart
    for _ in $(seq 60); do docker info >/dev/null 2>&1 && break; sleep 1; done
fi

docker version --format 'Docker Engine {{.Server.Version}}'
docker compose version
docker buildx version
