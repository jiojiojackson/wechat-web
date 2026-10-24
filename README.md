# WeChat Web · 安全加固部署

通过浏览器使用 Linux 微信，支持手机 Safari、中文输入和手机照片/文件上传。
在 [nickrunning/wechat-selkies](https://github.com/nickrunning/wechat-selkies) 基础上，
补充 Cloudflare Access 源站验签、明确邮箱白名单、随机密码与容器隔离。
仅启动微信，关闭 QQ 自动启动、终端、命令执行、Docker-in-Docker 和公开分享入口。

**安装入口：[jiojiojackson.github.io/wechat-web](https://jiojiojackson.github.io/wechat-web/)**

## Docker Hub 镜像与一键部署

使用一台独立的 **Ubuntu Server 22.04 / 24.04 / 26.04 LTS** 云主机，
支持 **ARM64 / x86_64**。至少约 4 GiB 内存，推荐 2 核 / 8 GiB，
默认 16 GiB 数据卷，加上构建、镜像和系统建议准备至少 40 GiB 可用磁盘。
需要 root/sudo、systemd、正常公网出站和一个接入 Cloudflare 的域名。

成品镜像：[jiojiojackson/web-wechat](https://hub.docker.com/r/jiojiojackson/web-wechat)。
支持 ARM64 / AMD64，公开拉取无需 Docker Hub 登录。Compose 模板见 [deploy/compose.yaml](deploy/compose.yaml)。

在服务器的 SSH 交互终端执行（先自动补齐下载工具，再启动向导）：

```bash
bash -c 'set -e; wechat_sudo=(); if ((EUID)); then wechat_sudo=(sudo); fi; if ! command -v curl >/dev/null || ! test -s /etc/ssl/certs/ca-certificates.crt; then "${wechat_sudo[@]}" apt-get update -qq; "${wechat_sudo[@]}" apt-get install -y curl ca-certificates; fi; wechat_tmp=$(mktemp); wechat_cleanup(){ rm -f -- "$wechat_tmp"; }; trap wechat_cleanup EXIT; curl --proto "=https" --tlsv1.2 -fsSL https://jiojiojackson.github.io/wechat-web/install.sh -o "$wechat_tmp"; bash "$wechat_tmp"'
```

这是下载并运行具有管理员权限的安装代码；可以先阅读 [install.sh](install.sh)
和 [deploy/setup.py](deploy/setup.py)。缺少 Python、curl、证书、系统工具、Docker 或
Compose 时自动安装。既有 Docker 的不兼容配置仍会停止，避免影响其他业务。

安装器会依次引导你完成外部配置、填写域名/AUD/邮箱、自动识别团队并验证 Access、
拉取已构建的镜像、创建数据卷并启动 Compose。目标服务器不进行镜像构建。
向导逐步询问域名、邮箱、Tunnel 路由、Access/AUD 和数据卷大小；输入错误可原地重试。
完成后显示访问网址、用户名 **`wechat`** 和独立生成的 **随机强密码**。
在网页上先通过 Access，再输入本地账号密码，最后用手机微信扫码登录。

也可以从源码安装：

```bash
git clone https://github.com/jiojiojackson/wechat-web.git
cd wechat-web
sudo bash deploy/install.sh
```

## 外部配置：域名、Tunnel、Access

安装脚本会逐项引导并等待输入，你可以边配置边安装。不需要 Cloudflare API Token。

1. **域名**：在 Cloudflare DNS 托管自己的域名，选择专用子域名，例如
   `wechat.example.com`。通过 Tunnel 发布，无需添加指向服务器公网 IP 的 A 记录。
2. **Tunnel**：在 Cloudflare One / Zero Trust → Networks → Connectors →
   Cloudflare Tunnels 创建或复用一个 `cloudflared` Tunnel。
   已有本机 SSH Tunnel 时，复用它，只添加路由，保留原 SSH 路由和连接器。
   新服务器没有连接器时，脚本可以安装官方 cloudflared，隐藏读取 Tunnel Token，
   通过 `--token-file` 启动。Token 不进入命令行、Git 或安装输出。
3. **发布路由**：Published application routes（旧版 Public Hostnames）：
   Hostname 填 `wechat.example.com`，Service Type 选 **HTTP**，URL 填
   **`localhost:13000`**。连接器必须运行于这台主机的网络命名空间；
   本安装器不自动处理位于其他容器网络内的连接器。
4. **Access 应用**：Access → Applications → Add an application → Self-hosted。
   使用相同域名，保护整个域名全部路径。创建 Allow 策略：
   Include → Emails → 填写明确的本人邮箱。不要使用 Everyone、整个邮箱后缀或 Bypass。
   根据需要设定 Access 会话时长，例如 24 小时，并配置适用的 MFA。
5. **复制 AUD**：微信 Access 应用 Additional settings 中的
   **Application Audience (AUD) Tag**，不要填写 API Token/JWT。
   团队地址由安装器从该域名的 Access HTTPS 登录跳转自动识别，通常无需查找或输入。
   如果域名尚未就绪、请求被拦截或跳转格式不受支持，才会提示手动填写团队名称或
   团队地址，例如 `https://my-team.cloudflareaccess.com`。
6. **路由源站保护**：返回 Tunnel 的该路由，在 Additional application settings →
   Access 开启 **Protect with Access**，关联相同的团队/应用。
   即使此项漏设，源站也会拒绝缺失/错误 JWT；仍建议同时启用。
7. **验证**：无痕窗口打开域名，首先应看到 Cloudflare Access 登录。
   安装前登录后出现 502 可以是源站尚未启动；匿名请求必须先被拦截。
   安装器会检查 `/`、`/files/`、`/websockets` 的拦截和团队公钥。

脚本按步骤询问完整域名、允许登录的邮箱、AUD 和数据卷大小；Tunnel/Access 配置完成后
自动识别团队，并显示识别出的地址。未通过检查时可重试或修改域名/AUD。只接受标准的 `https://<团队>.cloudflareaccess.com`
Access 登录跳转，不跟随任意重定向。识别后仍需通过全部路径拦截和团队公钥检查；
手动备用输入也不能跳过这些验证。团队地址保存到本机配置后，续装和日常检查不会自动更换它。
**源站邮箱白名单与 Access 策略同时生效**：意外放宽云端策略也不能让不在名单内的邮箱进入应用。
匿名探测只能验证登录拦截，无法枚举云端全部策略或证明填写的 AUD 正确；
最终需要第一次实际登录确认。

官方操作说明：[Cloudflare 应用](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)、
[源站 JWT 校验](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)。
控制台栏目名称可能调整。

## 云平台需要开放什么端口？

**微信应用不需要新增任何公网入站端口**，无需开放 80、443、13000、3000、8082 或 UDP 视频端口。
连接器通过出站 Tunnel 承载网页和 WebSocket，微信入口只绑定 `127.0.0.1:13000`。
云防火墙需要允许正常 DNS、HTTPS 和 Cloudflare Tunnel 出站通信（UDP/TCP 7844）；
微信自身还需要正常公网出站，安装器仅禁止访问私网、宿主机及云元数据。

安装器不会擅自关闭 SSH、重写整机 INPUT 策略或改变其他应用的端口。
使用 Oracle 时，请自行核对 Security Lists / NSG 的 IPv4、IPv6 入站规则。
若通过 SSH Tunnel 管理，先验证备用登录方式，再在云控制台关闭公网 TCP 22。
参见 [Cloudflare Tunnel 防火墙说明](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-with-firewall/)。

## 增加了哪些安全加固？

| 项目 | 本仓库实现 |
| --- | --- |
| 网络入口 | Cloudflare Tunnel；仅发布本机回环端口；所有页面、文件与 WebSocket 受保护 |
| 独立身份验证 | 源站固定团队公钥，校验 RS256、issuer、AUD、时间、应用类型、身份和明确邮箱名单 |
| 本地第二道验证 | 每次安装生成随机强密码，bcrypt 校验；首次密码登录后保留身份绑定会话 |
| Cookie | `__Host-`、Secure、HttpOnly、SameSite=Lax、30 天上限；绑定 Access subject、域名与密码版本；不能代替 Access |
| 请求边界 | 拒绝错误 Origin，关闭 devmode；限制验证并发与请求大小；公钥不可用时拒绝验证；鉴权后不向桌面流服务转发 JWT、Cookie 或 Basic 密码头 |
| 容器隔离 | Docker userns-remap、AppArmor、seccomp、no-new-privileges、移除默认 capabilities；微信进程使用 UID 1000 |
| 宿主机隔离 | 没有特权、host 网络/PID、设备或 Docker socket；专用 bridge 阻止元数据、私网和宿主机管理访问 |
| 启动顺序 | systemd 先检查隔离规则，再启动容器；由 systemd 重启应用，避免 Docker 自动启动早于隔离规则 |
| 缩减入口 | 关闭终端/sudo/命令执行、额外应用安装器、分享/协作、QQ 自动启动；仅保留微信启动菜单 |
| 资源与文件 | 内存、CPU、进程、日志和临时目录有上限；私有数据卷固定容量并以 nosuid/nodev 挂载 |
| 凭证保管 | 密码/签名密钥本机随机生成；root 私有目录，容器微信用户无法读取验证器秘密 |
| 文件下载 | 文件目录鉴权、禁止符号链接逃逸、附件下载、nosniff 和禁止缓存 |
| 更新与备份 | 启用 Ubuntu 自动安全更新但不自动重启；每日私有备份，7 天保留并限制累计 16 GiB |
| 发布流程 | Actions 测试后构建并推送双架构 Docker Hub 镜像；Pages 安装包绑定镜像摘要；安装包 SHA256 校验、安全解包 |

Docker 已存在时，脚本会检查版本和安全选项。**存在其他容器且未启用用户映射时会停止**，
不会为安装微信而改变已有容器的 UID 映射或重启它们。推荐独立主机。
脚本不自动修改 SSH 登录方式，不全面审计 Oracle/Cloudflare IAM，也不能替代宿主机安全维护。

## 手机体验

- 手机自动使用较合适的远程桌面尺寸；转屏时调整，键盘弹出时保持桌面比例。
- 修复中文输入法组合输入的重复前缀问题。
- 可拖动、展开/收起的「工具」按钮：键盘、照片、文件。
- 照片和文件上传到容器 **`/config/Desktop`**，方便微信插入；使用原有受认证的传输通道。
  默认单文件 100 MiB、每批 20 个/500 MiB 为浏览器限制，服务端以整个数据卷容量为边界。
- 补充缺少 ImageDecoder API 时的 JPEG 解码路径，改善 Safari 黑屏兼容性。

这是 Linux 微信的远程桌面，布局不会变成原生手机微信；物理 iPhone 的版本和网络仍可能影响表现。

## 日常管理

```bash
sudo wechat-web doctor           # Access / 源站拒绝匿名 / 健康 / 端口 / 防火墙检查
sudo wechat-web credentials      # 查看用户名与随机密码
sudo wechat-web reset-password   # 断开连接，轮换密码和会话密钥，再启动
sudo wechat-web backup           # 手动执行一次备份
sudo wechat-web stop
sudo wechat-web start
sudo journalctl -u wechat-web -n 100 --no-pager
sudo docker logs --tail 100 wechat-web
```

| 本机位置 | 内容 |
| --- | --- |
| `/opt/wechat-web/credentials.txt` | 用户名与密码，root 0600 |
| `/opt/wechat-web/secrets/` | 密码与会话签名密钥，只读挂载给容器 |
| `/opt/wechat-web/.env` | 本机域名、团队、AUD、邮箱、镜像 ID 等，root 0600 |
| `/opt/wechat-web/source/` | 安装时使用的源码模板，不存放运行秘密 |
| `/var/lib/wechat-web/data.ext4` | 固定容量私有数据卷 |
| `/var/lib/wechat-web/config/Desktop` | 对应容器 `/config/Desktop` |
| `/var/backups/wechat-web/` | 可能包含聊天记录和登录态的备份，root 私有 |
| `/etc/cloudflared-wechat-web/token` | 仅新建连接器时保存，root 0600 |

安装中断后，可重新运行同一命令继续；已有数据卷不会再次格式化，密码不会自动变更。
已经完成的安装会检查状态并保留配置，**重新运行安装器不是源码升级命令**。
数据卷格式化中断、磁盘不足或目录归属不明确时会停止，要求管理员核对。

更新运行实例时，先做好备份，在维护窗口拉取经过 CI 测试的新镜像：

```bash
sudo wechat-web backup
sudo docker pull jiojiojackson/web-wechat:latest
sudo docker image inspect jiojiojackson/web-wechat:latest --format '{{json .RepoDigests}}'
sudo wechat-web stop
# 将 /opt/wechat-web/.env 中 WECHAT_IMAGE 改为上述新仓库摘要（repo@sha256:...）后：
sudo wechat-web start
sudo wechat-web doctor
```

Pages 安装包自带本次发布的 `deploy/release.json`，新安装会拉取其中固定的摘要。
直接从 Git 安装则拉取已测试的 latest，再解析并固定仓库摘要；不会每次开机跟随 latest。

主要 Python 依赖和上游摘要的更新需要审查源码、测试和重新扫描，
不会未经审查自动跟随 `latest`。内核安全更新通常需要安排主机重启才生效。
备份保存在同一台服务器，建议另做加密异地备份；恢复需先停服务，再按原 UID/GID 映射还原数据。

## 常见问题与安全边界

- **403**：检查团队、AUD、源站邮箱名单和 Access 应用是否一致，确认没有只保护部分路径。
- **502 / 容器不健康**：检查 Tunnel 路由和上述日志。不要通过临时开放源站端口来排查。
- **黑屏 / Waiting for stream**：先检查容器健康，关闭重复页面后重新连接，尝试清理此站点旧设置。
  服务端健康不代表物理 Safari 已成功解码；回报问题时提供浏览器版本和经过脱敏的日志。
- **授权撤销**：已建立的 WebSocket 不持续重新验签。撤销 Access 时如需立即断开，先
  `sudo wechat-web stop`，再撤销云端会话并轮换本地密码。仅退出 Access 不保证既有连接立即结束。
- **主机风险**：容器共享主机内核，主机 root/sudo 管理员可以读取微信数据。
  未修复内核、微信专有二进制、账号/设备被盗和未知漏洞无法用部署脚本消除。
  没有公网应用端口也不等于零风险。
- **镜像内容**：加固镜像仍基于上游镜像层；关闭某项功能不等于从历史镜像层彻底移除其二进制。
- **隐私**：本项目不会回传安装配置或密码。GitHub Pages 只分发公开静态文件；
  云端 Access/DNS/Tunnel 配置和用户信息仅留在自己的 Cloudflare 账号和服务器。

## 开发、验证与 Pages 发布

```bash
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt
.venv/bin/python -m unittest discover -s tests -p test_access_guard.py -v
.venv/bin/python -m unittest discover -s tests -p test_installer.py -v
.venv/bin/python -m unittest discover -s tests -p test_bootstrap.py -v
.venv/bin/python -m unittest discover -s tests -p test_terminal.py -v
bash -n install.sh deploy/*.sh
python3 scripts/check-public.py
```

认证、Nginx/WebSocket、文件下载与手机 WebKit 测试可在一次性、无网络测试容器运行，
参考 [tests/README.md](tests/README.md)。测试使用临时签名密钥与合成画面，
不应连接已登录微信的生产视频流。安装器安全测试不会调用真实 apt/systemd 或格式化磁盘。

仓库 Settings → Pages → Source 设为 **GitHub Actions**。
在仓库 Actions Secrets 配置 `DOCKERHUB_USERNAME` 和 `DOCKERHUB_TOKEN`（仅 CI 使用）。
推送 main 后，工作流运行安全/真实终端测试，分别在 AMD64、ARM64 原生 runner 构建并测试，
推送到 Docker Hub，再合并发布 `latest` 和 `sha-<完整提交 SHA>` 双架构标签。
最后才生成绑定该镜像摘要的 Pages 安装包并发布。PR 测试不会登录或推送 Docker Hub。
流程不需要 Cloudflare 凭证或 GitHub PAT，目标服务器也不需要 Docker Hub Token。Fork 时请修改 `install.sh` 中的 BASE_URL
以及 README/页面链接，并在自己的仓库启用 Pages。

安装包只收录受 Git 跟踪的部署源码和指定文档；不打包工作目录、`.env`、secrets、
聊天数据、备份或审计记录。SHA256 检查防止包损坏/版本不一致，但不构成独立签名；
仍需信任本仓库及其 GitHub Pages 发布权限。

原项目和第三方许可证见 [NOTICE.md](NOTICE.md)，安全说明见 [SECURITY.md](SECURITY.md)。
