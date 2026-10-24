#!/usr/bin/env bash
# Shared by the source entry point and the generated Pages downloader.
bootstrap_authorize() {
    if [[ $EUID -eq 0 ]]; then return 0; fi
    if ! command -v sudo >/dev/null 2>&1; then
        echo '此安装需要管理员权限；当前账号没有 sudo，请改用 root 或具备 sudo 权限的账号。' >&2
        return 1
    fi
    # sudo -v may still demand a password with mixed PASSWD/NOPASSWD rules.
    # Probe the same root shell needed by the installer, using NOPASSWD first.
    if sudo -n bash -c '((EUID == 0))' >/dev/null 2>&1; then return 0; fi
    echo '正在确认 sudo 管理员权限；如有提示，请输入当前服务器账号的密码。'
    if ! sudo bash -c '((EUID == 0))'; then
        echo 'sudo 授权失败或已取消，安装已停止。请改用 root 或具备 sudo 权限的账号。' >&2
        return 1
    fi
}

bootstrap_root() {
    if [[ $EUID -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        echo '需要 root 或 sudo 权限才能自动安装依赖，请切换到管理员账号。' >&2
        return 1
    fi
}

bootstrap_dependencies() {
    local specification command_name package_name
    local -a missing=()
    for specification in python3:python3 curl:curl sha256sum:coreutils mktemp:coreutils chmod:coreutils ip:iproute2; do
        command_name=${specification%%:*}
        package_name=${specification#*:}
        command -v "$command_name" >/dev/null 2>&1 || missing+=("$package_name")
    done
    [[ -s /etc/ssl/certs/ca-certificates.crt ]] || missing+=(ca-certificates)
    if ((${#missing[@]})); then
        command -v apt-get >/dev/null 2>&1 || { echo '自动安装依赖需要 Ubuntu 的 apt-get。' >&2; return 1; }
        echo "自动安装引导依赖：${missing[*]}"
        bootstrap_root apt-get update -qq
        bootstrap_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
    fi
}
