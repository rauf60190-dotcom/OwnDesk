#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
task_action=${1:-install}
case "$task_action" in
  -h|--help) echo 'Usage: sudo bash Setup-OwnDesk.sh [install|start|stop|status|setup-link] [--protocol auto|http2|quic]'; exit 0 ;;
  install|start|stop|status|setup-link) ;;
  --protocol) task_action=install ;;
  *) echo 'Unknown action. Use: bash Setup-OwnDesk.sh --help'; exit 2 ;;
esac
task_args=("$@")
if [[ ${task_args[0]:-} == "$task_action" ]]; then task_args=("${task_args[@]:1}"); fi
while (( ${#task_args[@]} )); do
  case "${task_args[0]}" in
    --protocol)
      case "${task_args[1]:-}" in auto|http2|quic) ;; *) echo 'Protocol must be auto, http2 or quic.'; exit 2 ;; esac
      task_args=("${task_args[@]:2}") ;;
    -h|--help) echo 'Usage: sudo bash Setup-OwnDesk.sh [install|start|stop|status|setup-link] [--protocol auto|http2|quic]'; exit 0 ;;
    *) echo 'Unknown option. Use: bash Setup-OwnDesk.sh --help'; exit 2 ;;
  esac
done
if [[ ${EUID} -ne 0 ]]; then
  echo 'Запустите: sudo bash Setup-OwnDesk.sh'
  exit 1
fi
if [[ ! -f /etc/os-release ]]; then
  echo 'Не удалось определить дистрибутив Linux.'
  exit 1
fi
. /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 24.04 ]]; then
  echo 'Основная целевая система: Ubuntu Server 24.04 LTS x86-64. Другие версии ещё требуют приёмки.'
  exit 1
fi
if [[ $(uname -m) != x86_64 ]]; then
  echo 'Этот профиль рассчитан на x86-64; ARM ещё требует отдельной проверки.'
  exit 1
fi
if ! command -v python3 >/dev/null; then
  if [[ $task_action != install ]]; then echo 'Python is missing. Run install first.'; exit 1; fi
  apt-get update
  apt-get install -y python3
fi
if ! command -v docker >/dev/null; then
  if [[ $task_action != install ]]; then echo 'Docker is missing. Run install first.'; exit 1; fi
  echo 'Устанавливаем Docker Engine из официального репозитория Docker…'
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf '%s\n' 'Types: deb' 'URIs: https://download.docker.com/linux/ubuntu' 'Suites: noble' 'Components: stable' 'Architectures: amd64' 'Signed-By: /etc/apt/keyrings/docker.asc' > /etc/apt/sources.list.d/docker.sources
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
if ! docker compose version >/dev/null 2>&1; then
  echo 'Docker найден, но нужен Compose plugin. Установите его по https://docs.docker.com/engine/install/ubuntu/'
  exit 1
fi
if [[ $task_action == install ]]; then
  systemctl enable --now docker
elif [[ $task_action == start ]]; then
  systemctl start docker
fi
exec python3 infra/linux_setup.py "$@"
