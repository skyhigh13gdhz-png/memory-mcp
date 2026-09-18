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

Memory MCP **不直接访问 Hindsight、不保存记忆、不包含某个 AI 客户端专属业务逻辑**。Gateway 是统一记忆 API 边界。

## 2026-09-18 实际验收状态

腾讯云测试机已经真实验证：

- ChatGPT Plus 可创建并连接自定义 MCP；
- Cloudflare Tunnel → Memory MCP 公网链路通过；
- `memory_recall` 真实调用 Gateway/Hindsight 通过；
- `memory_retain` 真实写入通过；
- 新聊天跨会话 Recall 找回刚写入标记通过；
- MCP 本机 Retain → Recall → Reflect smoke test 通过。

当前重点已从“能否连接”转为**日常使用效率和端到端延迟**。已观察到 ChatGPT 页面整轮约 30–44 秒，而服务器内部 Recall 约亚秒级，因此必须用分段计时证据定位，不应直接归因于 Hindsight/VPS。

## 一键安装 / 更新

前提：同机 `memory-gateway` 已运行在 `127.0.0.1:8787`。

```bash
curl -fsSL https://raw.githubusercontent.com/skyhigh13gdhz-png/memory-mcp/main/bootstrap.sh | sudo bash
```

bootstrap 会：

- 检查 Gateway；
- 用 **git ls-remote + 硬超时**探测 GitHub，避免腾讯云上 `git fetch` 无限卡住；
- GitHub 直连不可用时尝试本机代理，再尝试已存在的 Gitee 只读镜像；
- 源码操作设置 90 秒硬超时；
- 保留现有 `MEMORY_MCP_PUBLIC_HOST`、`MEMORY_MCP_TOOL_MODE`、`MEMORY_MCP_LOG_LEVEL`，避免升级冲掉 ChatGPT 公网配置；
- 重建独立 venv、systemd 服务并执行真实 smoke test。

> GitHub 是唯一可写 Source of Truth；Gitee 只作为大陆部署镜像。镜像没有同步到目标 commit 时，不应把 Gitee 当成最新源码。

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

## Tool Mode（故障隔离）

```text
ping-only      无参数固定返回；验证 MCP / ChatGPT / Tunnel 基础兼容性
recall-schema  memory_recall(query: str) 固定返回；验证参数 Schema
recall-only    真实 Recall；验证 MCP → Gateway → Hindsight
full           Recall + Retain + Reflect 正常工具集
```

2026-09-18 的 A/B 结果：

```text
ping-only      ✅
recall-schema  ✅
recall-only    ✅（真实 Hindsight Recall）
full           ✅ Recall / Retain；Reflect 已通过服务器 smoke，ChatGPT 端继续验收
```

这组结果同时证明：此前连接器创建阶段的 Request timeout 不能简单归因为 Plus 套餐、Cloudflare、参数 Schema 或 Recall 本身。

## 性能日志

`mcp_server.py` 会为真实 Recall / Retain / Reflect 输出不包含记忆正文的结构化日志：

```text
event=tool_start request_id=... tool=memory_recall path=/v1/memories/recall
event=tool_end request_id=... tool=memory_recall status=200 client_ready_ms=... gateway_http_ms=... decode_ms=... mcp_total_ms=... gateway_timing=...
```

对比 ChatGPT UI 的整轮耗时与 `mcp_total_ms`，可以判断主要延迟在服务器业务链还是 ChatGPT 调工具前/后的模型流程。

## 默认安全策略

- MCP 仅监听 `127.0.0.1:8000`；
- Gateway 使用 `127.0.0.1:8787`；
- Gateway Token 只存在服务器 root 保护配置；
- 不提交 Token、个人记忆、服务器凭据；
- Cloudflare Tunnel 对外暴露稳定 HTTPS hostname，MCP 自身仍保持 localhost；
- DNS rebinding 防护保持开启，只 allowlist localhost 与配置的公网 hostname。

## 当前下一阶段

1. 完成 ChatGPT 端 Reflect 验收；
2. 用新计时日志测 Recall / Retain / Reflect，拆解 30–44 秒体感延迟；
3. 给公网 MCP 增加合适的客户端鉴权；
4. 固化 Cloudflare / ChatGPT / 迁移 SOP；
5. 测试机完整验收后，再 clean deploy 到腾讯云正式机；
6. Raw Store、自动 Retain、项目级自动触发在基础效率验证之后再做。
