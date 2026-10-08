# 云端部署：关机也能对话 + 每日速递

目标：把 QQ 机器人「木木」的对话监听放到一台 **7×24 运行的云服务器**上，你本机关机后：

- **对话**：私聊 / 群 @ 照常工作（WebSocket 监听在服务器上）
- **每日速递**：两条路都可用 —— 服务器上的 `brief --push`（可选加 crontab），或 GitHub Actions（见下文）

## 1. 购买服务器（约 30 分钟）

| 项 | 建议 |
| --- | --- |
| 厂商 | 腾讯云 / 阿里云「轻量应用服务器」，学生认证一般 20~50 元/月 |
| 配置 | 1 核 2G 即可（监听+偶尔跑调研，很轻） |
| 系统 | Ubuntu 22.04 或 24.04 |
| 安全组 | **不需要开放任何入站端口**（QQ 机器人是 WebSocket 出站连接），默认即可 |

## 2. SSH 登录服务器

```bash
ssh root@你的服务器IP
# 或按厂商控制台「网页终端」直接登录
```

## 3. 一键部署

```bash
git clone https://github.com/Mustar034/hotnews-agent.git /tmp/hn
cd /tmp/hn
bash scripts/deploy_server.sh
```

脚本会自动：装依赖 → 拉代码到 `/opt/hotnews-agent` → 建 Python 虚拟环境 → 生成 `.env` 模板 → 退出让你填凭据。

## 4. 填写凭据

```bash
nano /opt/hotnews-agent/.env
```

填入（与本地 `.env` 相同的值）：

```
LLM_API_KEY=你的大模型 API Key
QQ_APP_ID=1905729931
QQ_APP_SECRET=你的 QQ 机器人 Secret
QQ_TARGET_OPENIDS=接收速递的 openid（可留空，用户发消息自动绑定）
```

保存后再次执行部署脚本完成安装：

```bash
cd /tmp/hn
bash scripts/deploy_server.sh
```

## 5. 验证

```bash
journalctl -u hotnews-qqbot -f
```

看到 `已连接 QQ 开放平台，等待私聊/群@消息` 即成功。手机给「木木」发条消息测试。

服务已配置**开机自启 + 崩溃自动重启**（systemd `Restart=always`），服务器重启后无需任何操作。

常用命令：

```bash
sudo systemctl status hotnews-qqbot   # 状态
journalctl -u hotnews-qqbot -f        # 实时日志
sudo systemctl restart hotnews-qqbot  # 重启
```

## 可选：服务器上做每日速递（不依赖 GitHub）

在服务器加 crontab（北京时间每天 08:00 推送昨日 AI 速递）：

```bash
crontab -e
```

```cron
0 0 * * * cd /opt/hotnews-agent && .venv/bin/python -m hotnews.brief --date yesterday --theme ai --push
```

## 可选：GitHub Actions 定时速递（云端、与服务器无关）

1. 把本地代码推到 GitHub（本地执行）：`git push -u origin main`
2. GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret，添加 4 个：

| Secret 名 | 值 |
| --- | --- |
| `LLM_API_KEY` | 你的大模型 API Key |
| `QQ_APP_ID` | 1905729931 |
| `QQ_APP_SECRET` | 你的 QQ 机器人 Secret |
| `QQ_TARGET_OPENIDS` | 接收速递的 openid（可留空，自动绑定） |

3. 手动验证：Actions → Daily AI Brief → Run workflow → 跑一次，成功后每天北京时间 08:00 自动推送昨日 AI 速递。

> 注意：GitHub Actions 只做「定时速递」，**不能**常驻对话监听（Job 有时限）。对话常驻靠上面的服务器部署。
