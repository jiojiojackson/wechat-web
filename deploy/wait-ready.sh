#!/usr/bin/env bash
set -Eeuo pipefail
for ((attempt=0; attempt<100; attempt++)); do
    status=$(docker inspect --format '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' wechat-web 2>/dev/null || true)
    # Compose may still be replacing the previous stopped container. Docker
    # retains that container's last health result; it is not a startup result.
    if [[ $status == 'true healthy' ]]; then exit 0; fi
    if [[ $status == 'true unhealthy' ]]; then echo '微信容器健康检查失败，请查看 docker logs wechat-web' >&2; exit 1; fi
    sleep 3
done
echo '等待微信容器就绪超时，请查看 docker logs wechat-web' >&2
exit 1
