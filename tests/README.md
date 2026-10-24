# 安全与兼容回归

纯 Python 安全测试见主 README。在 Linux Docker 主机上，先构建发布镜像：

```bash
sudo docker build -t wechat-web:local deploy
sudo python3 scripts/test-container.py --image wechat-web:local
```

这会创建一次性 `--network none` 容器，仅使用临时密码和密钥，测试：
RS256/AUD/issuer/邮箱名单，Cookie/密码，Nginx/WebSocket 双向握手，
文件鉴权、符号链接、目录逃逸。不会读取或挂载真实微信配置。

手机 WebKit 回归需要单独的测试 SDK：

```bash
sudo docker build -f tests/Dockerfile.webkit --build-arg APP_IMAGE=wechat-web:local -t wechat-web:webkit-test .
sudo python3 scripts/test-container.py --image wechat-web:local --webkit-image wechat-web:webkit-test
```

SDK 中关闭 WebKit 内部 sandbox 仅用于受容器隔离、无网络的测试进程，
**不是生产应用设置**。不要部署 SDK。测试覆盖中文组合输入、拖动工具栏、
照片/文件分块传输、大小确认、手机视口/键盘/转屏，以及缺少 Safari 解码 API 时的真实 JPEG 绘制。
浏览器为模拟 iPhone 的 WebKit，不能替代每种物理设备的验证。

`test_real_stream.py` 是高级维护工具，仅允许带有
`/run/wechat-isolated-stream-fixture` 标记的隔离显示/编码夹具，
需要维护者另建带合成 X11 画面的测试环境，不属于默认测试。
不要为使测试通过而向生产容器添加标记；直接连接生产视频流可能接管主桌面。

test_terminal.py 使用真实伪终端验证输入、默认值、Token 隐藏和整个安装交互；系统操作由夹具代替。

完整安装需要独立 Ubuntu 虚拟机、真实域名和可用的 Cloudflare 配置。
在已经承载业务的主机上不应以运行安装器代替集成测试。
