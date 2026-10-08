#!/usr/bin/env bash
# HotNews QQ 监听 云端一键部署（Ubuntu 22.04 / 24.04）
#
# 用法：
#   第一次：  bash scripts/deploy_server.sh
#            脚本会在 /opt/hotnews-agent 拉代码、装依赖、生成 .env，然后退出让你填凭据
#   填好后：  nano /opt/hotnews-agent/.env
#   再跑一次：bash scripts/deploy_server.sh  → 安装并启动 systemd 服务（开机自启+崩溃自动重启）
#
# 验证：journalctl -u hotnews-qqbot -f  看到「已连接 QQ 开放平台」即成功；手机给机器人发消息测试。
set -euo pipefail

REPO_URL="${1:-https://github.com/Mustar034/hotnews-agent.git}"
APP_DIR=/opt/hotnews-agent

echo "==> [1/5] 安装基础依赖"
sudo apt-get update -y
sudo apt-get install -y git python3-venv python3-pip curl

echo "==> [2/5] 拉取代码到 $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown "$USER:$USER" "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull
else
  git clone "$REPO_URL" "$APP_DIR"
fi

echo "==> [3/5] 安装 Python 依赖（独立 venv，不污染系统）"
cd "$APP_DIR"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

echo "==> [4/5] 检查 .env 凭据"
if [ ! -f .env ]; then
  cp .env.example .env
  cat <<'EOF'

首次部署：请编辑 /opt/hotnews-agent/.env 填入以下凭据（密钥只保存在服务器上，不进仓库）：

  LLM_API_KEY=你的大模型 API Key
  QQ_APP_ID=1905729931
  QQ_APP_SECRET=你的 QQ 机器人 Secret
  QQ_TARGET_OPENIDS=接收速递的 openid（可留空：用户给机器人发消息后自动绑定）

编辑命令：nano /opt/hotnews-agent/.env
填完保存后，重新执行：bash scripts/deploy_server.sh 继续安装服务。
EOF
  exit 0
fi

echo "==> [5/5] 安装并启动 systemd 服务（开机自启 + 崩溃自动重启）"
sudo cp "$APP_DIR/scripts/hotnews-qqbot.service" /etc/systemd/system/hotnews-qqbot.service
sudo systemctl daemon-reload
sudo systemctl enable hotnews-qqbot
sudo systemctl restart hotnews-qqbot
sleep 3
sudo systemctl --no-pager status hotnews-qqbot || true
echo ""
echo "完成！查看实时日志：journalctl -u hotnews-qqbot -f"
echo "确认出现「已连接 QQ 开放平台，等待私聊/群@消息」即部署成功。"
