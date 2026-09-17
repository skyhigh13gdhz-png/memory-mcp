# Memory MCP

面向 AI 客户端的通用 MCP → Memory Gateway 协议适配层。

## 架构边界

```text
ChatGPT / Claude / Gemini / Qwen / 其他 MCP Client
                         ↓ MCP
                     Memory MCP
                         ↓ HTTP
                   Memory Gateway
                         ↓
                     Hindsight
```

Memory MCP **不直接访问 Hindsight、不保存记忆、不包含某个 AI 客户端专属业务逻辑**。Gateway 仍然是统一记忆 API 边界。

## 当前阶段目标

先在旧 VPS 上验证本机链路：

```text
MCP Client → Memory MCP → Gateway → Hindsight
```

必须真实通过 `memory_retain`、`memory_recall`、`memory_reflect`，并记录端到端耗时。通过后才进入公网 HTTPS / ChatGPT 接入；在此之前不增加 Raw Store、自动触发等复杂度。

## 一键安装

前提：同机 `memory-gateway` 已运行在 `127.0.0.1:8787`。

```bash
curl -fsSL https://raw.githubusercontent.com/skyhigh13gdhz-png/memory-mcp/main/bootstrap.sh | sudo bash
```

安装脚本会自动检查 Gateway、拉取/更新源码、从现有 Gateway 本地配置读取 Token、建立独立 venv、安装 systemd 服务，并执行 Retain → Recall → Reflect 真实 smoke test及耗时统计。

## 管理

```bash
memory-mcp status
memory-mcp health
memory-mcp test
memory-mcp logs
memory-mcp logs --history 500
memory-mcp restart
memory-mcp config
```

## 默认安全策略

- MCP 默认仅监听 `127.0.0.1:8000`；
- Gateway 默认使用 `127.0.0.1:8787`；
- Gateway Token 只存在 VPS 本地 root 保护配置中；
- 不把 Token、个人记忆、服务器凭据提交到 Git；
- 公网访问后续通过独立 HTTPS/TLS/Auth 接入层实现，不直接裸露内部端口。

## 当前状态

- 代码：已建立；
- 旧 VPS runtime：**尚未验证**；
- ChatGPT 公网接入：**尚未进行**。

只有旧 VPS smoke test 实际通过后，才能把 MCP → Gateway → Hindsight 标记为已验证。
