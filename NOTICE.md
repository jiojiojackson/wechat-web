# 来源与许可证

本仓库提供独立的部署器、安全配置和浏览器兼容补丁，以
[nickrunning/wechat-selkies](https://github.com/nickrunning/wechat-selkies)
的官方上游镜像为运行基础。原项目 Git 版本：
`79aea193fec9788cde1111fe8cc91149275ac51e`。

固定的多架构镜像索引：
`ghcr.io/nickrunning/wechat-selkies@sha256:aefb35f900b3c821ae38830e88053d19a860e5be6960e25e2999de28d8e35d15`。
ARM64 子镜像：`sha256:6dfebe9b61dd0e9d506f9f73145cc5dfde3be574b2e06f8db0101c2db936b5ed`；
AMD64 子镜像：`sha256:81cada21028b9e57699fa2dfcb034b59722100f9d13e0c477affb006af855230`。

上游仓库的 MIT LICENSE 原文保留在 [docs/upstream-LICENSE](docs/upstream-LICENSE)。
本仓库自有部署代码采用 MIT。镜像中 LinuxServer/Selkies、系统库和其他组件
各自遵守其许可证；上游镜像也包含 GPL 标记，不能据此把全部镜像或其依赖视为 MIT。
微信及可能保留在上游镜像层内的腾讯软件为第三方专有软件，其商标、软件及服务条款
归原权利人。本仓库不重新许可这些二进制，也不代表腾讯、Cloudflare 或上游项目。

本仓库没有打包任何用户的聊天记录、微信登录态、SSH 密钥、Tunnel Token、
Cloudflare API 凭证、密码或生产部署配置。
