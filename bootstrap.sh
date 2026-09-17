#!/usr/bin/env bash
set -Eeuo pipefail
GITHUB_REPO="https://github.com/skyhigh13gdhz-png/memory-mcp.git"
GITEE_REPO="https://gitee.com/skyhigh13/memory-mcp.git"
SOURCE_DIR="${MEMORY_MCP_SOURCE_DIR:-/opt/src/memory-mcp}"
INSTALL_DIR="${MEMORY_MCP_INSTALL_DIR:-/opt/memory-mcp}"
ENV_FILE="/etc/memory-mcp.env"
SERVICE="memory-mcp"
LOCAL_HTTP_PROXY="${MEMORY_MCP_HTTP_PROXY:-http://127.0.0.1:10809}"
# 与现有 memory-gateway 的 systemd 安装策略保持一致。bootstrap 经 sudo 执行时，
# /opt/memory-mcp 与 /etc/memory-mcp.env 都是 root 管理的运行时文件，服务也由 root 运行，
# 避免 SUDO_USER 无法穿过 /opt 或读取受保护配置导致 CHDIR/EnvironmentFile 失败。
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

log '检查源码网络'
SOURCE_PROXY=""
if curl -fsSIL --max-time 8 https://github.com >/dev/null 2>&1; then SOURCE="$GITHUB_REPO"; ok 'GitHub：直连可用，使用 GitHub Source of Truth'
elif curl -fsSIL --max-time 8 --proxy "$LOCAL_HTTP_PROXY" https://github.com >/dev/null 2>&1; then SOURCE="$GITHUB_REPO"; SOURCE_PROXY="$LOCAL_HTTP_PROXY"; ok 'GitHub：通过现有本机代理可访问，继续使用 GitHub Source of Truth'
elif curl -fsSIL --max-time 8 https://gitee.com >/dev/null 2>&1 && git ls-remote "$GITEE_REPO" HEAD >/dev/null 2>&1; then SOURCE="$GITEE_REPO"; warn 'GitHub 不可达，使用已存在的 Gitee 只读镜像'
else die '当前无法取得 Memory MCP 源码。'; fi

git_run(){ if [[ -n "$SOURCE_PROXY" ]]; then git -c "http.proxy=$SOURCE_PROXY" -c "https.proxy=$SOURCE_PROXY" "$@"; else git "$@"; fi; }
mkdir -p "$(dirname "$SOURCE_DIR")"
if [[ -d "$SOURCE_DIR/.git" ]]; then git_run -C "$SOURCE_DIR" fetch "$SOURCE" main; git -C "$SOURCE_DIR" checkout main >/dev/null 2>&1; git -C "$SOURCE_DIR" reset --hard FETCH_HEAD >/dev/null; else rm -rf "$SOURCE_DIR"; git_run clone "$SOURCE" "$SOURCE_DIR" >/dev/null; fi
git -C "$SOURCE_DIR" remote set-url origin "$GITHUB_REPO"
ok 'Memory MCP 源码已准备；origin 保持 GitHub'

log '准备安全配置'
GATEWAY_ENV="/opt/src/memory-gateway/.env"
[[ -r "$GATEWAY_ENV" ]] || die "找不到现有 Gateway 配置：$GATEWAY_ENV"
TOKEN="$(sed -n 's/^GATEWAY_API_TOKEN=//p' "$GATEWAY_ENV" | head -n1)"
[[ -n "$TOKEN" ]] || die '现有 Gateway 配置中没有 GATEWAY_API_TOKEN。'
umask 077
cat > "$ENV_FILE" <<EOF
MEMORY_GATEWAY_URL=http://127.0.0.1:8787
MEMORY_GATEWAY_TOKEN=$TOKEN
MEMORY_CLIENT_ID=mcp-client
MEMORY_GATEWAY_TIMEOUT=60
MEMORY_MCP_HOST=127.0.0.1
MEMORY_MCP_PORT=8000
EOF
chmod 600 "$ENV_FILE"
ok 'MCP 本地配置已与现有 Gateway 同步（Token 不显示、不进入 Git）'

log '安装并启动 Memory MCP'
rm -rf "$INSTALL_DIR"; mkdir -p "$INSTALL_DIR/scripts"
cp "$SOURCE_DIR/mcp_server.py" "$SOURCE_DIR/requirements.txt" "$INSTALL_DIR/"
cp "$SOURCE_DIR/scripts/smoke-test.py" "$INSTALL_DIR/scripts/"
python3 -m venv "$INSTALL_DIR/.venv"
PIP=("$INSTALL_DIR/.venv/bin/pip")
if [[ -n "$SOURCE_PROXY" ]]; then http_proxy="$SOURCE_PROXY" https_proxy="$SOURCE_PROXY" "${PIP[@]}" install -q --upgrade pip; http_proxy="$SOURCE_PROXY" https_proxy="$SOURCE_PROXY" "${PIP[@]}" install -q -r "$INSTALL_DIR/requirements.txt"; else "${PIP[@]}" install -q --upgrade pip; "${PIP[@]}" install -q -r "$INSTALL_DIR/requirements.txt"; fi
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
systemctl is-active --quiet "$SERVICE" || { journalctl -u "$SERVICE" -n 80 --no-pager; die 'Memory MCP 启动失败。'; }
ok "Memory MCP 已启动：127.0.0.1:8000/mcp（运行用户：${RUN_USER}），并设置开机自动恢复"

log '执行 MCP → Gateway → Hindsight 自动验收'
/usr/local/bin/memory-mcp test
printf '\n========== 安装完成 ==========\n日常管理：memory-mcp\n  memory-mcp status\n  memory-mcp health\n  memory-mcp test\n  memory-mcp logs\n==============================\n'
