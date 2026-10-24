#!/usr/bin/env python3
"""Interactive, fail-closed installer for a dedicated Ubuntu Docker host.

Uses only the standard library. Runtime configuration is never written into
the checkout. No Cloudflare account API key or GitHub credential is requested.
"""
import argparse
import fcntl
import getpass
import warnings
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path('/opt/wechat-web')
DATA = Path('/var/lib/wechat-web')
SOURCE = Path(__file__).resolve().parent
STATE = ROOT / 'install-state.json'
DOCKER_KEY = '9DC858229FC7DD38854AE2D88D81803C0EBFCD88'
CF_KEY_SHA256 = '1bd95f4082b320d541bee351560fc2765aa9f9cd8efa4c9e32135e63f252721d'
IMAGE_REPOSITORY = 'jiojiojackson/web-wechat'


def fail(message):
    raise RuntimeError(message)


def run(*args, capture=False, **kwargs):
    # Arguments must never contain passwords, signing keys, or tunnel tokens.
    result = subprocess.run([str(x) for x in args], check=True,
                            stdout=subprocess.PIPE if capture else None,
                            text=True, **kwargs)
    return result.stdout.strip() if capture else ''


def write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)
    temporary.chmod(mode)
    temporary.replace(path)


def ask(prompt, default=None):
    suffix = f' [{default}]' if default is not None else ''
    try:
        # Buffered r+ requires seeking on a character device on some Python
        # versions. Separate handles work with SSH, sudo and piped stdin.
        with open('/dev/tty', 'w', encoding='utf-8') as output:
            output.write(prompt + suffix + ': ')
            output.flush()
        with open('/dev/tty', 'r', encoding='utf-8') as incoming:
            line = incoming.readline()
    except OSError:
        fail('无法读取交互终端。请使用 SSH 交互会话（远程命令请加 ssh -t）后重试。')
    if not line:
        fail('未读取到终端输入。请在 SSH 交互终端中运行安装器。')
    return line.strip() or default or ''


def ask_secret(prompt):
    try:
        with open('/dev/tty', 'w', encoding='utf-8') as output, warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            return getpass.getpass(prompt + ': ', stream=output).strip()
    except (OSError, getpass.GetPassWarning):
        fail('无法安全隐藏输入。请在 SSH 交互终端中重试，Token 不会通过明文备用输入读取。')


def prompt_value(prompt, validator, default=None):
    while True:
        try:
            return validator(ask(prompt, default))
        except RuntimeError as error:
            print(f'输入无效：{error} 请重新填写。')


def ready(prompt):
    while True:
        answer = ask(prompt + '（完成后回车；输入 q 退出）')
        if answer.lower() == 'q':
            fail('用户取消安装。')
        if not answer or answer.lower() in ('y', 'yes', 'ok'):
            return
        print('请完成本步后按回车，或输入 q 退出。')


def storage_size(value):
    if not value.isdigit() or not 4 <= int(value) <= 256:
        fail('存储大小应为 4～256 GiB 的整数。')
    return int(value)


def yes_no(value):
    if value.lower() not in ('y', 'yes', 'n', 'no'):
        fail('请输入 y 或 n。')
    return value.lower() in ('y', 'yes')


def wizard(info, memory):
    print('\n[1/6] 服务器检查通过')
    print(f'Ubuntu {info.get("VERSION_ID", "")} · {memory/1024**3:.1f} GiB；将使用 Docker Hub 成品镜像。')
    configured = prompt_value('Cloudflare 的域名、Tunnel 和 Access 是否已配置？y 已配置，n 需要引导', yes_no, 'y')
    print('\n[2/6] 域名与访问身份')
    print('请准备已接入 Cloudflare DNS 的专用子域名。无需开放公网应用端口。')
    domain = prompt_value('微信完整域名，例如 wechat.example.com', hostname)
    emails = prompt_value('Access 允许登录的本人邮箱（多个用逗号分隔）', email_list)
    print('\n[3/6] Cloudflare Tunnel')
    setup_tunnel()
    if not configured:
        print(f'请在该 Tunnel 添加 Published application route：\n  域名：{domain}\n  类型：HTTP\n  服务：localhost:13000')
        print('已有 SSH 路由保留不变。')
        ready('该微信路由已保存')
    else:
        print('复用已有微信路由，接下来自动验证；源站应为 HTTP localhost:13000。')
    print('\n[4/6] Cloudflare Access')
    if not configured:
        print(f'Access → Applications → Self-hosted：域名 {domain}，覆盖全部路径。')
        print(f'Allow → Emails 仅允许：{emails}；不要添加 Bypass / Everyone。')
        print('在 Tunnel 的该路由启用 Protect with Access，并关联同一个应用。')
    print('从该 Access 应用 Additional settings 复制 Application Audience (AUD) Tag。')
    aud = prompt_value('粘贴微信应用的 AUD Tag', audience)
    config = dict(domain=domain, emails=emails, aud=aud)
    verify_connection(config)
    print('\n[5/6] 数据空间与确认')
    config['storage_gib'] = prompt_value('微信数据卷大小（GiB）', storage_size, '16')
    print(f'访问 https://{domain}；数据 /var/lib/wechat-web；最多占用 {config["storage_gib"]} GiB。')
    print('软件依赖缺失时自动安装；只拉取镜像，不在目标服务器编译。')
    if ask('确认部署？y 继续，n 退出', 'y').lower() not in ('y', 'yes'):
        fail('用户取消安装。')
    return config


def verify_connection(config):
    while True:
        try:
            if 'team' not in config:
                config['team'] = resolve_access_team(config['domain'])
            verified = validate_config(config)
            verify_edge(verified)
            config.update(verified)
            print('Access 拦截与团队公钥检查通过。')
            return
        except (RuntimeError, OSError, ValueError):
            print('Access 验证未通过，请核对域名、Tunnel 路由和 Access 应用。')
            answer = ask('回车重新检查；输入 edit 修改域名/AUD；输入 q 退出')
            if answer.lower() == 'q':
                fail('用户取消安装。')
            if answer.lower() == 'edit':
                config['domain'] = prompt_value('微信完整域名', hostname)
                config['aud'] = prompt_value('微信应用 AUD Tag', audience)
                config.pop('team', None)


def hostname(value):
    value = value.strip().lower().rstrip('.')
    if len(value) > 253 or '.' not in value:
        fail('请输入完整域名，例如 wechat.example.com，不带 https:// 或路径。')
    labels = value.split('.')
    if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in labels):
        fail('域名格式无效；国际化域名请使用 Punycode。')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        fail('需要域名，不能使用 IP 地址。')
    return value


def team_name(value):
    value = value.strip().lower().removeprefix('https://').removesuffix('/')
    value = value.removesuffix('.cloudflareaccess.com')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', value):
        fail('团队名称无效，例如 my-team 或 https://my-team.cloudflareaccess.com。')
    return value


def audience(value):
    if not re.fullmatch(r'[a-fA-F0-9]{64}', value.strip()):
        fail('Application Audience (AUD) 应为 64 位十六进制应用标识；不要输入 Token。')
    return value.strip().lower()


def email_list(value):
    values = sorted(set(x.strip().lower() for x in value.split(',') if x.strip()))
    if not values or not all(re.fullmatch(r'[a-z0-9.!%+_=-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+', x) for x in values):
        fail('请填写明确的邮箱，多个用英文逗号分隔，不能使用通配符。')
    return ','.join(values)


def validate_config(config):
    return dict(config, domain=hostname(config['domain']), team=team_name(config['team']),
                aud=audience(config['aud']), emails=email_list(config['emails']))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def edge_response(opener, domain, path='/'):
    request = urllib.request.Request('https://' + domain + path,
                                     headers={'User-Agent': 'wechat-web-preflight/1.0'})
    try:
        return opener.open(request, timeout=20)
    except urllib.error.HTTPError as error:
        return error


def access_login_team(response):
    """Extract a team from a standard Access login redirect without following it."""
    location = response.headers.get('Location', '')
    if len(location) > 16384 or re.search(r'[\x00-\x20\x7f]', location):
        fail('Access 登录跳转格式无效。')
    try:
        target = urllib.parse.urlsplit(location)
        match = re.fullmatch(r'([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.cloudflareaccess\.com',
                             target.hostname or '')
        valid = (response.status in (302, 303) and target.scheme == 'https' and match
                 and target.username is None and target.password is None
                 and target.port in (None, 443)
                 and (target.path == '/cdn-cgi/access/login'
                      or target.path.startswith('/cdn-cgi/access/login/')))
    except ValueError:
        valid = False
    if not valid:
        fail('未收到标准 Cloudflare Access HTTPS 登录跳转。')
    return match[1]


def resolve_access_team(domain):
    """Bootstrap from the user's HTTPS hostname; runtime issuer stays pinned."""
    domain = hostname(domain)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with edge_response(opener, domain) as response:
            team = access_login_team(response)
    except (RuntimeError, OSError, ValueError):
        print('无法从域名的 Access 登录跳转自动识别团队。请检查域名和 Access 配置，或手动填写。')
        # Do not print Location, response bodies, or network exception details.
        # Manual input still goes through the same Access/JWKS checks below.
        return team_name(ask('Zero Trust 团队名称 / 团队地址（自动识别失败时备用）'))
    print(f'已自动识别团队地址：https://{team}.cloudflareaccess.com；接下来验证 Access 与公钥。')
    return team


def verify_edge(config):
    """Unauthenticated probes must hit this team's Access gate before deploy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for path in ('/', '/files/', '/websockets'):
        try:
            with edge_response(opener, config['domain'], path) as response:
                team = access_login_team(response)
        except RuntimeError:
            fail(f'Access 拦截验证失败：{path}。检查域名、团队、应用覆盖路径和 Bypass 策略。')
        if team != config['team']:
            fail(f'Access 团队不一致：{path}。不会自动替换已记录的团队。')
    url = f"https://{config['team']}.cloudflareaccess.com/cdn-cgi/access/certs"
    with opener.open(url, timeout=20) as response:
        raw = response.read(262145)
    if len(raw) > 262144:
        fail('Access 公钥响应异常。')
    keys = json.loads(raw).get('keys', [])
    if not any(x.get('kty') == 'RSA' and x.get('alg') == 'RS256' for x in keys):
        fail('没有取得有效的 Access RS256 公钥。')


def platform_check():
    info = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            info[key] = value.strip('"')
    if info.get('ID') != 'ubuntu' or info.get('VERSION_ID') not in ('22.04', '24.04', '26.04'):
        fail('目前支持 Ubuntu Server 22.04 / 24.04 / 26.04 LTS，使用 systemd。')
    if os.uname().machine not in ('aarch64', 'x86_64') or not Path('/run/systemd/system').is_dir():
        fail('需要 ARM64 或 x86_64 的 systemd 云主机；不支持容器内安装。')
    memory = int(re.search(r'MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text())[1]) * 1024
    if memory < 3800 * 1024**2:
        fail('至少需要约 4 GiB 内存，推荐 8 GiB 和 2 核以上。')
    return info, memory


def preflight():
    # Check all conflicts before creating runtime directories or changing Docker.
    if ROOT.exists() and not STATE.exists():
        fail('/opt/wechat-web 已存在且无安装状态，拒绝覆盖。')
    if DATA.exists() and not STATE.exists():
        fail('/var/lib/wechat-web 已存在且无安装状态，拒绝覆盖。')
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 13000))
        except OSError:
            fail('本机 13000 端口已被占用，未更改现有应用。请使用一台独立服务器。')
    daemon = Path('/etc/docker/daemon.json')
    config = json.loads(daemon.read_text()) if daemon.exists() else {}
    if config.get('userns-remap') not in (None, 'default'):
        fail('已有自定义 Docker 用户映射；请使用独立服务器，安装器不会覆盖。')
    if config.get('firewall-backend', 'iptables') != 'iptables' or config.get('iptables') is False:
        fail('需要 Docker iptables 防火墙后端；不会覆盖现有网络配置。')
    if shutil.which('docker'):
        if subprocess.run(['docker', 'info'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            fail('已有 Docker 无法使用，请先检查 daemon；安装器不会重置。')
        version = run('docker', 'version', '--format', '{{.Server.Version}}', capture=True)
        if int(version.split('.')[0]) < 28:
            fail('需要 Docker 28+ 修复本机端口隔离问题，请先维护升级。')
        containers = run('docker', 'ps', '-aq', capture=True)
        if containers and config.get('userns-remap') != 'default':
            fail('已有容器但未启用 userns-remap；拒绝自动重启或改变其他容器。请换独立服务器。')
        if run('docker', 'network', 'ls', '--filter', 'name=^wechat-web-net$', '-q', capture=True):
            fail('同名 Docker 网络已存在，请先核对，安装器不会删除。')
    for service in ('docker.io', 'podman-docker', 'containerd', 'runc'):
        status = subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status}', service],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if status.stdout == 'installed':
            fail(f'检测到冲突包 {service}，请先维护或使用干净的 Ubuntu 主机。')
    if shutil.which('ip'):
        for route in json.loads(run('ip', '-j', 'route', capture=True)):
            net = route.get('dst', 'default')
            if net != 'default' and ipaddress.ip_network(net).overlaps(ipaddress.ip_network('172.30.81.0/24')):
                fail('172.30.81.0/24 与已有路由重叠，请使用独立服务器或手动调整安装器配置。')


def ensure_packages(packages):
    missing = []
    for package in packages:
        result = subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status}', package],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if result.returncode or result.stdout.strip() != 'installed':
            missing.append(package)
    if missing:
        print('自动安装缺失依赖：' + ', '.join(missing))
        run('apt-get', 'update', '-qq')
        apt_install(*missing)


def apt_install(*packages):
    run('apt-get', 'install', '-y', '--no-install-recommends', *packages,
        env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))


def docker_repository(info):
    directory = Path('/etc/apt/keyrings')
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    # main() uses umask 0077 for private runtime files. APT's unprivileged
    # _apt downloader still needs to traverse this public signing-key directory.
    directory.chmod(0o755)
    key = directory / 'wechat-web-docker.asc'
    run('curl', '--proto', '=https', '--tlsv1.2', '-fsSL', '--retry', '3', '-o', key,
        'https://download.docker.com/linux/ubuntu/gpg')
    os.chmod(key, 0o644)
    details = run('gpg', '--batch', '--with-colons', '--show-keys', key, capture=True)
    if DOCKER_KEY not in [line.split(':')[9] for line in details.splitlines() if line.startswith('fpr:')]:
        fail('Docker 官方签名密钥指纹不匹配。')
    arch = run('dpkg', '--print-architecture', capture=True)
    write('/etc/apt/sources.list.d/wechat-web-docker.list',
          f'deb [arch={arch} signed-by={key}] https://download.docker.com/linux/ubuntu {info["VERSION_CODENAME"]} stable\n', 0o644)


def dependencies(info):
    ensure_packages(['ca-certificates', 'curl', 'gnupg', 'iproute2', 'iptables',
                     'e2fsprogs', 'util-linux', 'apparmor', 'unattended-upgrades'])
    run('systemctl', 'enable', '--now', 'apparmor')
    if not Path('/sys/module/apparmor/parameters/enabled').exists() or Path('/sys/module/apparmor/parameters/enabled').read_text().strip() != 'Y':
        fail('内核未启用 AppArmor，停止部署。请先启用并重启主机。')
    if not shutil.which('docker'):
        docker_repository(info)
        run('apt-get', 'update', '-qq')
        apt_install('docker-ce', 'docker-ce-cli', 'containerd.io',
                    'docker-buildx-plugin', 'docker-compose-plugin')
    elif subprocess.run(['docker', 'compose', 'version'], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL).returncode:
        docker_repository(info)
        ensure_packages(['docker-compose-plugin'])
    daemon = Path('/etc/docker/daemon.json')
    config = json.loads(daemon.read_text()) if daemon.exists() else {}
    changed = config.get('userns-remap') != 'default'
    if changed:
        config.update({'userns-remap': 'default', 'live-restore': True, 'log-driver': 'local',
                       'log-opts': {'max-size': '10m', 'max-file': '3'}})
        write(daemon, json.dumps(config, indent=2) + '\n')
        run('systemctl', 'restart', 'docker')
    run('systemctl', 'enable', '--now', 'docker')
    run('docker', 'compose', 'version')
    security = run('docker', 'info', '--format', '{{json .SecurityOptions}}', capture=True)
    if not all(x in security for x in ('userns', 'seccomp', 'apparmor')):
        fail('Docker 必须具备 userns、seccomp 和 AppArmor 支持。')
    run('systemctl', 'enable', '--now', 'apt-daily.timer', 'apt-daily-upgrade.timer')
    write('/etc/apt/apt.conf.d/52wechat-web-security',
          'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n'
          'Unattended-Upgrade::Automatic-Reboot "false";\n', 0o644)


def setup_tunnel():
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'cloudflared']).returncode == 0
    active = active or subprocess.run(['systemctl', 'is-active', '--quiet', 'cloudflared-wechat-web']).returncode == 0
    if active:
        print('检测到现有 Cloudflare Tunnel 服务，将复用，不更改其配置。')
        return
    print('本机没有已运行的 Tunnel。请在 Cloudflare → Networks → Connectors 创建或选择一个 Tunnel。')
    if ask('由脚本安装 cloudflared 连接器？y 安装，n 退出', 'y').lower() not in ('y', 'yes'):
        fail('请先手动启动本机 Tunnel，再重新运行安装器。')
    ensure_packages(['ca-certificates', 'curl', 'gnupg'])
    while True:
        token = ask_secret('粘贴该 Tunnel 的连接器 Token（隐藏输入，只粘贴 Token 部分）')
        if re.fullmatch(r'[A-Za-z0-9_+/=-]{80,4096}', token):
            break
        print('Token 格式无效，请重新粘贴；不要粘贴整条安装命令。')
    # Never put the connector token in argv, environment, journald, or Git.
    if not shutil.which('cloudflared'):
        key = Path('/usr/share/keyrings/wechat-web-cloudflare.gpg')
        run('curl', '--proto', '=https', '--tlsv1.2', '-fsSL', '--retry', '3', '-o', key,
            'https://pkg.cloudflare.com/cloudflare-main.gpg')
        if hashlib.sha256(key.read_bytes()).hexdigest() != CF_KEY_SHA256:
            fail('Cloudflare 包签名密钥发生变化，请先由维护者核验并更新。')
        key.chmod(0o644)
        write('/etc/apt/sources.list.d/wechat-web-cloudflare.list',
              f'deb [signed-by={key}] https://pkg.cloudflare.com/cloudflared any main\n', 0o644)
        run('apt-get', 'update', '-qq')
        apt_install('cloudflared')
    directory = Path('/etc/cloudflared-wechat-web')
    directory.mkdir(mode=0o700, exist_ok=True)
    write(directory / 'token', token + '\n')
    del token
    binary = shutil.which('cloudflared')
    write('/etc/systemd/system/cloudflared-wechat-web.service',
          '[Unit]\nDescription=Cloudflare Tunnel for WeChat Web\nAfter=network-online.target\nWants=network-online.target\n'
          '[Service]\nType=simple\n'
          f'ExecStart={binary} --no-autoupdate tunnel run --token-file {directory}/token\n'
          'Restart=on-failure\nRestartSec=5s\nUMask=0077\nNoNewPrivileges=true\nPrivateTmp=true\n'
          'ProtectSystem=strict\nProtectHome=true\nProtectKernelTunables=true\nProtectControlGroups=true\n'
          '[Install]\nWantedBy=multi-user.target\n', 0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'cloudflared-wechat-web')
    print('连接器已启动。请确认 Cloudflare 中 Tunnel 显示 Healthy。')


def pull_image():
    metadata = SOURCE / 'release.json'
    reference = json.loads(metadata.read_text())['image'] if metadata.exists() else IMAGE_REPOSITORY + ':latest'
    pattern = r'(?:docker\.io/)?jiojiojackson/web-wechat(?:@sha256:[0-9a-f]{64}|:latest|:sha-[0-9a-f]{40})'
    if not re.fullmatch(pattern, reference):
        fail('发布镜像地址无效，只允许该项目的 Docker Hub 镜像。')
    print('正在从 Docker Hub 拉取已构建的镜像，无需本机编译。')
    run('docker', 'pull', reference)
    digests = json.loads(run('docker', 'image', 'inspect', reference, '--format', '{{json .RepoDigests}}', capture=True))
    for digest in digests or []:
        if re.fullmatch(r'(?:docker\.io/)?jiojiojackson/web-wechat@sha256:[0-9a-f]{64}', digest):
            if '@sha256:' in reference and digest.split('@', 1)[1] != reference.split('@', 1)[1]:
                continue
            return digest
    fail('未能确认拉取镜像的仓库摘要，拒绝使用未固定版本的镜像。')


def subid(path):
    entries = [line.split(':') for line in Path(path).read_text().splitlines() if line.startswith('dockremap:')]
    if len(entries) != 1 or int(entries[0][2]) < 65536:
        fail('无法确认 dockremap 的连续 65536 UID/GID 映射，停止创建数据卷。')
    return int(entries[0][1])


def create_volume(config):
    image = DATA / 'data.ext4'
    target = DATA / 'config'
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.mkdir(mode=0o700, exist_ok=True)
    if not image.exists():
        needed = config['storage_gib'] * 1024**3
        if shutil.disk_usage(DATA).free < needed + 10 * 1024**3:
            fail('数据卷之外需要至少 10 GiB 剩余空间用于镜像与系统缓存；请释放磁盘后重试。')
        run('fallocate', '-l', str(needed), image)
        image.chmod(0o600)
        run('mkfs.ext4', '-q', '-F', image)
        config['volume_formatted'] = True
        write(STATE, json.dumps(config, indent=2) + '\n')
    elif not config.get('volume_formatted'):
        fail('数据镜像存在但无格式化完成标记；请人工核对，安装器不会重新格式化。')
    unit = run('systemd-escape', '--path', '--suffix=mount', target, capture=True)
    write(Path('/etc/systemd/system') / unit,
          '[Unit]\nDescription=Bounded private WeChat Web data volume\nBefore=wechat-web.service\n'
          f'[Mount]\nWhat={image}\nWhere={target}\nType=ext4\nOptions=loop,nosuid,nodev,noatime\n'
          '[Install]\nWantedBy=multi-user.target\n', 0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', unit)
    uid, gid = subid('/etc/subuid'), subid('/etc/subgid')
    os.chown(DATA, uid + 1000, gid + 1000)
    os.chown(target, uid + 1000, gid + 1000)
    config.update(uid_base=uid, gid_base=gid)
    return uid, gid


def create_secrets(uid, gid):
    directory = ROOT / 'secrets'
    directory.mkdir(mode=0o700, exist_ok=True)
    password = directory / 'web-password'
    session = directory / 'session-key'
    if not password.exists():
        write(password, secrets.token_urlsafe(24) + '\n', 0o400)
    if not session.exists():
        write(session, secrets.token_hex(32) + '\n', 0o400)
    for path in (password, session):
        os.chown(path, uid, gid)
        path.chmod(0o400)
    write(ROOT / 'credentials.txt', '用户名: wechat\n密码: ' + password.read_text().strip() + '\n')


def install_services():
    for name in ('wechat-web.service', 'wechat-web-firewall.service', 'wechat-web-backup.service', 'wechat-web-backup.timer'):
        write(Path('/etc/systemd/system') / name, (SOURCE / name).read_text(), 0o644)
    for filename, target in (('harden-firewall.sh', '/usr/local/sbin/wechat-web-firewall'),
                             ('backup-wechat.sh', '/usr/local/sbin/wechat-web-backup')):
        write(target, (SOURCE / filename).read_text(), 0o700)
    write('/usr/local/sbin/wechat-web', '#!/bin/sh\nexec /usr/bin/python3 /opt/wechat-web/source/deploy/setup.py "$@"\n', 0o700)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', 'wechat-web-firewall.service', 'wechat-web.service', 'wechat-web-backup.timer')
    # A previous interrupted install may already have active units. enable --now
    # leaves those running with the old firewall or Compose configuration.
    run('systemctl', 'restart', 'wechat-web-firewall.service')
    run('systemctl', 'restart', 'wechat-web.service')
    run('systemctl', 'start', 'wechat-web-backup.timer')


def install():
    info, memory = platform_check()
    if STATE.exists():
        config = validate_config(json.loads(STATE.read_text()))
        if config.get('complete'):
            print('检测到已完成的安装；保留数据、密码和运行配置。')
            doctor(config)
            print('查看用户名和密码：sudo wechat-web credentials')
            return
        print('继续尚未完成的安装；保留原域名、密码和数据。')
    else:
        preflight()
        config = wizard(info, memory)
        config.update(memory=f'{min(6, max(2, int(memory/1024**3)-2))}g',
                      cpus=min(3, max(1, (os.cpu_count() or 2)-1)), complete=False)
    if STATE.exists():
        verify_connection(config)
    print('\n[6/6] 安装依赖、拉取镜像并启动 Docker Compose')
    if not STATE.exists():
        ROOT.mkdir(mode=0o700)
        write(STATE, json.dumps(config, indent=2) + '\n')
    ROOT.chmod(0o700)
    # The source copy contains templates, never credentials or host state.
    installed = ROOT / 'source' / 'deploy'
    if SOURCE != installed:
        for directory in ('deploy', 'docs'):
            shutil.copytree(SOURCE.parent / directory, ROOT / 'source' / directory,
                            dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    dependencies(info)
    image_reference = pull_image()
    uid, gid = create_volume(config)
    create_secrets(uid, gid)
    config['image'] = image_reference
    env = dict(WECHAT_IMAGE=image_reference, WECHAT_DOMAIN=config['domain'], WECHAT_ACCESS_TEAM=config['team'],
               WECHAT_ACCESS_AUD=config['aud'], WECHAT_ALLOWED_EMAILS=config['emails'],
               WECHAT_MEMORY=config['memory'], WECHAT_CPUS=str(config['cpus']))
    write(ROOT / '.env', ''.join(f'{k}={v}\n' for k, v in env.items()))
    write(ROOT / 'compose.yaml', (SOURCE / 'compose.yaml').read_text())
    write(STATE, json.dumps(config, indent=2) + '\n')
    install_services()
    doctor(config)
    config['complete'] = True
    write(STATE, json.dumps(config, indent=2) + '\n')
    print(f'\n部署完成：https://{config["domain"]}\n先完成 Access 登录，再输入以下本地账号：')
    print((ROOT / 'credentials.txt').read_text())
    print('凭证仅保存在本机 /opt/wechat-web/credentials.txt（root 0600）；请勿提交至 Git。')


def doctor(config):
    verify_edge(config)
    # Docker translates host loopback connections to the bridge gateway.
    # Keep healthz private to the container, rather than relaxing its ACL.
    run('docker', 'exec', 'wechat-web', 'curl', '-fsS', '--max-time', '5',
        'http://127.0.0.1:3000/healthz', capture=True)
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(
            'http://127.0.0.1:13000/', timeout=10)
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise
    else:
        fail('源站未登录访问未被拒绝。')
    details = json.loads(run('docker', 'inspect', 'wechat-web', capture=True))[0]
    if details['State'].get('Health', {}).get('Status') != 'healthy':
        fail('容器尚未 healthy，请检查 sudo journalctl -u wechat-web。')
    ports = details['HostConfig']['PortBindings']
    if ports != {'3000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '13000'}]}:
        fail('容器端口绑定不符合仅本机访问要求。')
    run('/usr/local/sbin/wechat-web-firewall', '--check')
    print('验证通过：Access 拦截、源站拒绝匿名访问、容器健康、回环端口及隔离规则。')
    print('邮箱策略/AUD 的最终确认需要首次实际登录；安装器不会索取用户的 Access JWT。')


def main():
    parser = argparse.ArgumentParser(description='微信 Web 安装与管理（只在本机保存配置/凭证）')
    parser.add_argument('command', nargs='?', default='install',
                        choices=['install', 'doctor', 'credentials', 'reset-password', 'backup', 'start', 'stop'])
    args = parser.parse_args()
    if os.geteuid() != 0:
        fail('请使用 sudo bash deploy/install.sh，或 sudo wechat-web <命令>。')
    os.umask(0o077)
    Path('/run/lock').mkdir(exist_ok=True)
    with open('/run/lock/wechat-web-install.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail('另一个安装或管理进程正在运行，请等待其完成后重试。')
        if args.command == 'install':
            install()
        else:
            config = validate_config(json.loads(STATE.read_text()))
            if args.command == 'doctor':
                doctor(config)
            elif args.command == 'credentials':
                print((ROOT / 'credentials.txt').read_text())
            elif args.command == 'reset-password':
                # Cutting established connections is part of revocation.
                run('systemctl', 'stop', 'wechat-web')
                write(ROOT / 'secrets/web-password', secrets.token_urlsafe(24) + '\n', 0o400)
                write(ROOT / 'secrets/session-key', secrets.token_hex(32) + '\n', 0o400)
                create_secrets(config['uid_base'], config['gid_base'])
                run('systemctl', 'start', 'wechat-web')
                print((ROOT / 'credentials.txt').read_text())
            elif args.command == 'backup':
                run('systemctl', 'start', 'wechat-web-backup')
            else:
                run('systemctl', args.command, 'wechat-web')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'\n安装/检查已停止：{error}', file=sys.stderr)
        print('未完成的安装可重新运行；不会自动删除数据、重新格式化已有卷或生成新密码。', file=sys.stderr)
        sys.exit(1)
