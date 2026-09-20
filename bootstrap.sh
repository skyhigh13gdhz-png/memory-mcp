#!/usr/bin/env bash
set -Eeuo pipefail
GITHUB_REPO="https://github.com/skyhigh13gdhz-png/memory-mcp.git"
GITEE_REPO="https://gitee.com/skyhigh13/memory-mcp.git"
SOURCE_DIR="${MEMORY_MCP_SOURCE_DIR:-/opt/src/memory-mcp}"
INSTALL_DIR="${MEMORY_MCP_INSTALL_DIR:-/opt/memory-mcp}"
ENV_FILE="/etc/memory-mcp.env"
SERVICE="memory-mcp"
LOCAL_HTTP_PROXY="${MEMORY_MCP_HTTP_PROXY:-http://127.0.0.1:10809}"
RUN_USER="${MEMORY_MCP_RUN_USER:-root}"
ok(){ printf '[✓] %s\n' "$*"; }; log(){ printf '\n[→] %s\n' "$*"; }; warn(){ printf '[!] %s\n' "$*"; }; die(){ printf '[✗] %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die '请使用 sudo 运行 bootstrap.sh。'
printf '========== Memory MCP 一键安装 ==========\n'

log '检查本机记忆链路'
command -v git >/dev/null || die '缺少 git。'
command -v python3 >/dev/null || die '缺少 python3。'
python3 -m venv --help >/dev/null 2>&1 || die '缺少 Python venv。'
curl -fsS --max-time 5 http://127.0.0.1:8787/health >/dev/null || die 'Memory Gateway 127.0.0.1:8787 不可访问。'
ok 'Memory Gateway：可以访问'

log '检查源码网络（所有探测均有硬超时，避免腾讯云卡死）'
SOURCE_PROXY=""
git_probe(){
  timeout 12s git "$@" >/dev/null 2>&1
}
git_probe_proxy(){
  timeout 12s git -c "http.proxy=$LOCAL_HTTP_PROXY" -c "https.proxy=$LOCAL_HTTP_PROXY" "$@" >/dev/null 2>&1
}
if git_probe_proxy ls-remote "$GITHUB_REPO" HEAD; then
  SOURCE="$GITHUB_REPO"; SOURCE_PROXY="$LOCAL_HTTP_PROXY"; ok 'GitHub：优先使用本机代理'
elif git_probe ls-remote "$GITHUB_REPO" HEAD; then
  SOURCE="$GITHUB_REPO"; ok 'GitHub：本机代理不可用，Git 直连可用'
elif git_probe ls-remote "$GITEE_REPO" HEAD; then
  SOURCE="$GITEE_REPO"; warn 'GitHub 不可达，使用 Gitee 只读镜像'
else
  die '12 秒内无法取得 Memory MCP 源码；已主动退出，不会无限卡在 git fetch。'
fi

git_run(){
  if [[ -n "$SOURCE_PROXY" ]]; then
    timeout 90s git -c "http.proxy=$SOURCE_PROXY" -c "https.proxy=$SOURCE_PROXY" "$@"
  else
    timeout 90s git "$@"
  fi
}
mkdir -p "$(dirname "$SOURCE_DIR")"
if [[ -d "$SOURCE_DIR/.git" ]]; then
  git_run -C "$SOURCE_DIR" fetch "$SOURCE" main || die '源码 fetch 失败或 90 秒超时。'
  git -C "$SOURCE_DIR" checkout main >/dev/null 2>&1
  git -C "$SOURCE_DIR" reset --hard FETCH_HEAD >/dev/null
else
  rm -rf "$SOURCE_DIR"
  git_run clone "$SOURCE" "$SOURCE_DIR" >/dev/null || die '源码 clone 失败或 90 秒超时。'
fi
git -C "$SOURCE_DIR" remote set-url origin "$GITHUB_REPO"
ok 'Memory MCP 源码已准备；origin 保持 GitHub'

# 旧版 bootstrap 在运行中拉到新版后，当前 shell 仍会继续执行旧逻辑。
# 仅重入一次已拉取的最新脚本，保证新增安装步骤在本轮就生效。
if [[ ${MEMORY_MCP_BOOTSTRAP_REFRESHED:-0} != 1 ]]; then
  log '切换到刚拉取的最新部署逻辑'
  exec env MEMORY_MCP_BOOTSTRAP_REFRESHED=1 bash "$SOURCE_DIR/bootstrap.sh"
fi

log '准备安全配置'
GATEWAY_ENV="/opt/src/memory-gateway/.env"
[[ -r "$GATEWAY_ENV" ]] || die "找不到现有 Gateway 配置：$GATEWAY_ENV"
TOKEN="$(sed -n 's/^GATEWAY_API_TOKEN=//p' "$GATEWAY_ENV" | head -n1)"
[[ -n "$TOKEN" ]] || die '现有 Gateway 配置中没有 GATEWAY_API_TOKEN。'

# 保留已经配置好的公网 Host、工具模式等非密钥运行参数，避免升级把 ChatGPT 接入配置冲掉。
PUBLIC_HOST="$(sed -n 's/^MEMORY_MCP_PUBLIC_HOST=//p' "$ENV_FILE" 2>/dev/null | tail -n1 || true)"
TOOL_MODE="$(sed -n 's/^MEMORY_MCP_TOOL_MODE=//p' "$ENV_FILE" 2>/dev/null | tail -n1 || true)"
TIMING_ENABLED="$(sed -n 's/^MEMORY_MCP_TIMING_ENABLED=//p' "$ENV_FILE" 2>/dev/null | tail -n1 || true)"
LOG_LEVEL="$(sed -n 's/^MEMORY_MCP_LOG_LEVEL=//p' "$ENV_FILE" 2>/dev/null | tail -n1 || true)"
PUBLIC_HOST="${PUBLIC_HOST:-memory.skyhighmonica.fyi}"
TOOL_MODE="${TOOL_MODE:-full}"
TIMING_ENABLED="${TIMING_ENABLED:-0}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

umask 077
cat > "$ENV_FILE" <<EOF
MEMORY_GATEWAY_URL=http://127.0.0.1:8787
MEMORY_GATEWAY_TOKEN=$TOKEN
MEMORY_CLIENT_ID=mcp-client
MEMORY_GATEWAY_TIMEOUT=60
MEMORY_RECALL_TIMEOUT=15
MEMORY_RETAIN_TIMEOUT=90
MEMORY_REFLECT_TIMEOUT=180
MEMORY_MCP_HOST=127.0.0.1
MEMORY_MCP_PORT=8000
MEMORY_MCP_PUBLIC_HOST=$PUBLIC_HOST
MEMORY_MCP_TOOL_MODE=$TOOL_MODE
MEMORY_MCP_TIMING_ENABLED=$TIMING_ENABLED
MEMORY_MCP_LOG_LEVEL=$LOG_LEVEL
EOF
chmod 600 "$ENV_FILE"
ok 'MCP 配置已同步，并保留公网 Host / Tool Mode / Log Level'

log '安装并启动 Memory MCP'
systemctl stop "$SERVICE" 2>/dev/null || true
systemctl reset-failed "$SERVICE" 2>/dev/null || true
rm -rf "$INSTALL_DIR"; mkdir -p "$INSTALL_DIR/scripts"
cp "$SOURCE_DIR/mcp_server.py" "$SOURCE_DIR/requirements.txt" "$INSTALL_DIR/"
cp "$SOURCE_DIR/scripts/smoke-test.py" "$SOURCE_DIR/scripts/document-smoke-test.py" "$INSTALL_DIR/scripts/"
python3 -m venv "$INSTALL_DIR/.venv"
PIP=("$INSTALL_DIR/.venv/bin/pip")
if [[ -n "$SOURCE_PROXY" ]]; then
  http_proxy="$SOURCE_PROXY" https_proxy="$SOURCE_PROXY" timeout 120s "${PIP[@]}" install -q --upgrade pip
  http_proxy="$SOURCE_PROXY" https_proxy="$SOURCE_PROXY" timeout 180s "${PIP[@]}" install -q -r "$INSTALL_DIR/requirements.txt"
else
  timeout 120s "${PIP[@]}" install -q --upgrade pip
  timeout 180s "${PIP[@]}" install -q -r "$INSTALL_DIR/requirements.txt"
fi
"$INSTALL_DIR/.venv/bin/python" -c 'import httpx, mcp' || die 'Python 依赖安装不完整。'
ok 'Python 依赖：完整'
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
systemctl daemon-reload; systemctl enable "$SERVICE" >/dev/null; systemctl restart "$SERVICE"; sleep 2
systemctl is-active --quiet "$SERVICE" || { journalctl -u "$SERVICE" -b -n 80 --no-pager; die 'Memory MCP 启动失败。'; }
ok "Memory MCP 已启动：127.0.0.1:8000/mcp（运行用户：${RUN_USER}），并设置开机自动恢复"

log '执行 MCP → Gateway → Hindsight 自动验收'
/usr/local/bin/memory-mcp test
printf '\n========== 安装完成 ==========\n日常管理：memory-mcp\n  memory-mcp status\n  memory-mcp health\n  memory-mcp test\n  memory-mcp logs\n==============================\n'
