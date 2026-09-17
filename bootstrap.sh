#!/usr/bin/env bash
set -euo pipefail

REPO_GITHUB="https://github.com/skyhigh13gdhz-png/memory-mcp.git"
SOURCE_DIR="/opt/src/memory-mcp"
INSTALL_DIR="/opt/memory-mcp"
ENV_FILE="/etc/memory-mcp.env"
SERVICE="memory-mcp"
RUN_USER="${SUDO_USER:-ubuntu}"

[[ $EUID -eq 0 ]] || { echo "[✗] 请使用 sudo 运行 bootstrap.sh"; exit 1; }

echo "========== Memory MCP 一键安装 =========="
echo

echo "[→] 检查前置条件"
command -v git >/dev/null || { echo "[✗] 缺少 git"; exit 1; }
command -v python3 >/dev/null || { echo "[✗] 缺少 python3"; exit 1; }
python3 -m venv --help >/dev/null 2>&1 || { echo "[✗] 缺少 Python venv"; exit 1; }
curl -fsS --max-time 5 http://127.0.0.1:8787/health >/dev/null || { echo "[✗] Memory Gateway 127.0.0.1:8787 不可访问，请先确保 Gateway 正常"; exit 1; }
echo "[✓] Memory Gateway：可以访问"

mkdir -p /opt/src
if [[ -d "$SOURCE_DIR/.git" ]]; then
  echo "[→] 更新 Memory MCP 源码"
  git -C "$SOURCE_DIR" remote set-url origin "$REPO_GITHUB"
  git -C "$SOURCE_DIR" fetch --quiet origin main
  git -C "$SOURCE_DIR" reset --hard origin/main >/dev/null
else
  echo "[→] 获取 Memory MCP 源码"
  rm -rf "$SOURCE_DIR"
  git clone --quiet "$REPO_GITHUB" "$SOURCE_DIR"
fi
echo "[✓] 源码已准备：$SOURCE_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[→] 创建安全配置"
  GATEWAY_ENV="/opt/src/memory-gateway/.env"
  [[ -f /opt/memory-gateway/.env ]] && GATEWAY_ENV="/opt/memory-gateway/.env"
  TOKEN=""
  if [[ -r "$GATEWAY_ENV" ]]; then
    TOKEN="$(sed -n 's/^GATEWAY_API_TOKEN=//p' "$GATEWAY_ENV" | head -n1)"
  fi
  if [[ -z "$TOKEN" && -r /etc/memory-gateway.env ]]; then
    TOKEN="$(sed -n 's/^GATEWAY_API_TOKEN=//p' /etc/memory-gateway.env | head -n1)"
  fi
  if [[ -z "$TOKEN" ]]; then
    echo "[✗] 无法自动读取 Gateway Token。为避免让你手工复制密钥，安装停止。"
    echo "    请先确认现有 memory-gateway 的实际配置文件位置。"
    exit 1
  fi
  cat > "$ENV_FILE" <<EOF
MEMORY_GATEWAY_URL=http://127.0.0.1:8787
MEMORY_GATEWAY_TOKEN=$TOKEN
MEMORY_CLIENT_ID=mcp-client
MEMORY_GATEWAY_TIMEOUT=60
MEMORY_MCP_HOST=127.0.0.1
MEMORY_MCP_PORT=8000
EOF
  chmod 600 "$ENV_FILE"
fi

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/scripts"
cp "$SOURCE_DIR/mcp_server.py" "$SOURCE_DIR/requirements.txt" "$INSTALL_DIR/"
cp "$SOURCE_DIR/scripts/smoke-test.py" "$INSTALL_DIR/scripts/"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
install -m 0755 "$SOURCE_DIR/bin/memory-mcp" /usr/local/bin/memory-mcp

cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=Memory MCP Adapter
After=network-online.target memory-gateway.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/mcp_server.py
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE" >/dev/null
sleep 2
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "[✗] Memory MCP 启动失败"
  journalctl -u "$SERVICE" -n 80 --no-pager
  exit 1
fi
echo "[✓] Memory MCP 已启动：127.0.0.1:8000/mcp，并设置开机自动恢复"

echo
echo "[→] 执行 MCP → Gateway → Hindsight 自动验收"
/usr/local/bin/memory-mcp test

echo
echo "========== 安装完成 =========="
echo "统一管理命令：memory-mcp"
echo "  memory-mcp status"
echo "  memory-mcp health"
echo "  memory-mcp test"
echo "  memory-mcp logs"
echo "================================"
