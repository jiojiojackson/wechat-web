#!/usr/bin/env python3
"""Remove this installation without touching Tunnel or shared host services."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path('/opt/wechat-web')
DATA = Path('/var/lib/wechat-web')
BACKUPS = Path('/var/backups/wechat-web')
UNITS = Path('/etc/systemd/system')
BIN = Path('/usr/local/sbin')
LOCKS = Path('/run/lock')
TIMERS = Path('/var/lib/systemd/timers')
APT_CONFIG = Path('/etc/apt/apt.conf.d/52wechat-web-security')
SERVICE_NAMES = ('wechat-web-backup.timer', 'wechat-web-backup.service',
                 'wechat-web.service', 'wechat-web-firewall.service')
CHAINS = (('iptables', 'DOCKER-USER', 'WXWEB-ISOLATE'),
          ('iptables', 'INPUT', 'WXWEB-HOST4'), ('ip6tables', 'INPUT', 'WXWEB-HOST6'))


def command(*args, allowed=(0,)):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    if result.returncode not in allowed:
        raise RuntimeError(f'卸载命令失败（{result.returncode}）：{" ".join(str(x) for x in args[:3])}。未执行全局 prune 或强制卸载。')
    return result


def mounts():
    result = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        path = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
        result.append((Path(path), fields[2], fields[fields.index('-')+1]))
    return result


def check_directories():
    for root in (ROOT, DATA, BACKUPS):
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise RuntimeError(f'{root} 或其父目录是符号链接，拒绝递归删除。')
        for target, device, filesystem in mounts():
            if target != root and root not in target.parents:
                continue
            if root != DATA or target != DATA/'config' or filesystem != 'ext4':
                raise RuntimeError(f'发现额外挂载 {target}，请先核对并卸载，未删除应用数据。')
            backing = Path('/sys/dev/block')/device/'loop/backing_file'
            if not backing.is_file() or backing.read_text().strip().lstrip('/') != str(DATA/'data.ext4').lstrip('/'):
                raise RuntimeError('微信数据挂载不是本应用的 loop 镜像，拒绝卸载或删除。')


def docker_json(*args):
    return json.loads(command('docker', *args).stdout)


def docker_resources():
    if not shutil.which('docker'):
        if (DATA/'data.ext4').exists() or Path('/var/lib/docker/containers').exists():
            raise RuntimeError('缺少 Docker CLI，无法确认容器已清除。请先恢复 Docker 后重试。')
        return None, None, False
    command('docker', 'info', '--format', '{{.ServerVersion}}')
    ids = command('docker', 'container', 'ls', '-aq', '--filter', 'name=^/wechat-web$').stdout.split()
    container = docker_json('container', 'inspect', ids[0])[0] if ids else None
    if container:
        labels = container['Config'].get('Labels') or {}
        own_mount = any(m.get('Destination') == '/config' and m.get('Source') == str(DATA/'config')
                        for m in container.get('Mounts', []))
        if labels.get('com.docker.compose.project') != 'wechat-web' or not own_mount:
            raise RuntimeError('同名 wechat-web 容器不属于本安装，拒绝删除。')
    network_ids = command('docker', 'network', 'ls', '-q', '--filter', 'name=^wechat-web-net$').stdout.split()
    network = docker_json('network', 'inspect', network_ids[0])[0] if network_ids else None
    if network:
        if network.get('Driver') != 'bridge' or network.get('Options', {}).get('com.docker.network.bridge.name') != 'br-wxweb':
            raise RuntimeError('同名 Docker 网络不属于本安装，拒绝删除。')
        others = set(network.get('Containers', {})) - ({container['Id']} if container else set())
        if others:
            raise RuntimeError('专用网络连接了其他容器，未停止或删除任何应用。请先核对网络。')
    return container, network, True


def stop_unit(name):
    state = command('systemctl', 'show', name, '-p', 'LoadState', '--value', allowed=(0, 1, 4)).stdout.strip()
    if state and state != 'not-found':
        command('systemctl', 'stop', name)
        command('systemctl', 'disable', name)


def remove_firewall():
    for binary, parent, chain in CHAINS:
        if not shutil.which(binary):
            continue
        while command(binary, '-w', '-C', parent, '-j', chain, allowed=(0, 1)).returncode == 0:
            command(binary, '-w', '-D', parent, '-j', chain)
        if command(binary, '-w', '-S', chain, allowed=(0, 1)).returncode == 0:
            command(binary, '-w', '-F', chain)
            command(binary, '-w', '-X', chain)


def project_reference(value):
    return bool(re.fullmatch(r'(?:docker\.io/)?jiojiojackson/web-wechat(?::[A-Za-z0-9_.-]+|@sha256:[0-9a-f]{64})', value))


def remove_images():
    retained = set()
    ids = command('docker', 'image', 'ls', '--all', '--no-trunc', '--format', '{{.ID}}').stdout.split()
    images = docker_json('image', 'inspect', *sorted(set(ids))) if ids else []
    other_ids = command('docker', 'container', 'ls', '-aq').stdout.split()
    used = {item['Image'] for item in docker_json('container', 'inspect', *other_ids)} if other_ids else set()
    for image in images:
        references = [x for x in (image.get('RepoTags') or []) + (image.get('RepoDigests') or []) if project_reference(x)]
        if image['Id'] in used:
            retained.update(references)
            continue
        for reference in references:
            if command('docker', 'image', 'inspect', reference, allowed=(0, 1)).returncode == 0:
                command('docker', 'image', 'rm', '--no-prune', reference)
    return retained


def uninstall(assume_yes=False, ask=None):
    if os.geteuid() != 0:
        raise RuntimeError('请使用 sudo wechat-web uninstall。')
    print('将永久删除微信容器、聊天与上传数据、备份、登录凭证和应用配置，不会自动备份。')
    print('保留 Cloudflare Tunnel、域名、Access、Docker、系统依赖及其软件源。')
    if not assume_yes and (ask is None or ask('输入 uninstall 确认卸载') != 'uninstall'):
        raise RuntimeError('已取消卸载，未更改应用。')
    check_directories()
    container, network, docker = docker_resources()
    if docker and any(not shutil.which(binary) for binary in ('iptables', 'ip6tables')):
        raise RuntimeError('缺少 iptables/ip6tables，无法核验专用防火墙链已清除。请先恢复工具后重试。')
    mount_unit = command('systemd-escape', '--path', '--suffix=mount', DATA/'config').stdout.strip()
    print('[1/4] 停止应用和定时任务')
    for name in SERVICE_NAMES:
        stop_unit(name)
    if container:
        command('docker', 'container', 'rm', '-f', container['Id'])
    stop_unit(mount_unit)
    if any(p == DATA/'config' for p, _, _ in mounts()):
        raise RuntimeError('数据卷仍在挂载，已停止卸载，未删除数据。')
    check_directories()
    print('[2/4] 清理专用网络、防火墙规则和镜像')
    if network:
        command('docker', 'network', 'rm', network['Id'])
    remove_firewall()
    retained = remove_images() if docker else set()
    print('[3/4] 删除数据、备份及应用专用配置')
    unit_names = (*SERVICE_NAMES, mount_unit)
    for name in unit_names:
        for directory in (UNITS, UNITS/'multi-user.target.wants', UNITS/'docker.service.wants', UNITS/'timers.target.wants'):
            (directory/name).unlink(missing_ok=True)
    APT_CONFIG.unlink(missing_ok=True)
    (TIMERS/'stamp-wechat-web-backup.timer').unlink(missing_ok=True)
    for name in ('wechat-web-firewall', 'wechat-web-backup'):
        (BIN/name).unlink(missing_ok=True)
    for root in (DATA, BACKUPS):
        if root.exists():
            shutil.rmtree(root)
    command('systemctl', 'daemon-reload')
    # Clear only this application's failed-unit records; preserve other units.
    for name in unit_names:
        command('systemctl', 'reset-failed', name, allowed=(0, 1))
    print('[4/4] 核验并移除管理命令')
    if docker:
        if command('docker', 'container', 'ls', '-aq', '--filter', 'name=^/wechat-web$').stdout.strip():
            raise RuntimeError('容器未完全删除。')
        if command('docker', 'network', 'ls', '-q', '--filter', 'name=^wechat-web-net$').stdout.strip():
            raise RuntimeError('专用网络未完全删除。')
    if any(root.exists() for root in (DATA, BACKUPS)) or any(p == DATA/'config' for p, _, _ in mounts()):
        raise RuntimeError('应用数据或挂载仍存在。')
    for binary, _, chain in CHAINS:
        if shutil.which(binary) and command(binary, '-w', '-S', chain, allowed=(0, 1)).returncode == 0:
            raise RuntimeError('应用防火墙链未完全删除。')
    if root := next((UNITS/name for name in unit_names if (UNITS/name).exists()), None):
        raise RuntimeError(f'应用服务文件仍存在：{root}')
    if ROOT.exists():
        shutil.rmtree(ROOT)
    (BIN/'wechat-web').unlink(missing_ok=True)
    for name in ('wechat-web-backup.lock', 'wechat-web-firewall.lock'):
        (LOCKS/name).unlink(missing_ok=True)
    # Last operation: no destructive work remains when a new installer can
    # create a fresh lock inode. main() still holds the old inode until return.
    (LOCKS/'wechat-web-install.lock').unlink(missing_ok=True)
    print('卸载完成：应用、数据、备份及专用配置已删除；Tunnel 和外部配置保持不变。')
    if retained:
        print('其他容器仍在使用以下共享镜像，已保留：' + ', '.join(sorted(retained)))
