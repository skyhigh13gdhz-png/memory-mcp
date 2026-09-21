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

## 正式工具

- `memory_retain`：异步受理原始记录；可选传入稳定 `document_id`、ISO 8601 `timestamp` 和本次自然记录的 `idempotency_key`；客户端重试必须复用同一幂等键，`replace|append` 只用于更新已知 `document_id`，新建时不传；
- `memory_operation_get`：按 `operation_id` 查询异步写入的真实状态；`completed` 才表示处理完成；
- `memory_recall`：检索提取后的事实记忆；默认返回 20 条，MCP 会把客户端传入的 1–9 自动提升到 10，日期范围完整分析必须改用 `memory_document_range`；
- `memory_reflect`：基于多条记忆综合分析；
- `memory_document_list`：按 speaker 列出或搜索原始文档；
- `memory_document_range`：按事件日期范围确定性读取全部原始文档和正文，供日报、周报及范围统计使用；
- `memory_document_get`：读取一份原始文档；
- `memory_document_patch`：对原文做单次、确定、可审计的 compare-and-swap 替换。

修正工作流固定为 `List/Get → Patch`。`expected_text` 零次命中会返回冲突，多次命中会返回歧义；两种情况都不会静默覆盖原文。当前不对 AI 客户端暴露 Delete/Reprocess，避免异步 reprocess 与 delete 竞态导致已删文档复活。

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
- 首次拉取后自动重入一次最新 bootstrap，确保部署器自身新增步骤在当次安装就生效；
- 保留现有 `MEMORY_MCP_PUBLIC_HOST`、`MEMORY_MCP_TOOL_MODE`、`MEMORY_MCP_LOG_LEVEL`，避免升级冲掉 ChatGPT 公网配置；
- 重建独立 venv、systemd 服务并执行真实 smoke test。

smoke test 固定使用 `speaker=audit-mcp-core` 和 `document_id=mcp-core-smoke`，重复部署只替换同一测试 Document，不再向 `liangzai` / `monica` 的人类记录写入部署测试数据。

> GitHub 是唯一可写 Source of Truth；Gitee 只作为大陆部署镜像。镜像没有同步到目标 commit 时，不应把 Gitee 当成最新源码。

## 管理

```bash
memory-mcp status
memory-mcp health
memory-mcp test
memory-mcp test-documents
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
full           Recall + Retain + Reflect + Document List/Range/Get/Patch 正常工具集
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

详细性能打点**生产默认关闭**（`MEMORY_MCP_TIMING_ENABLED=0`），避免长期为正常请求生成 UUID、多个分段计时和结构化日志。需要定位延迟时临时开启：

```bash
memory-mcp timing on
# 完成一轮 Recall / Retain / Reflect 测试
memory-mcp logs 200
memory-mcp timing off
```

关闭时只保留原本就需要返回的 `mcp_adapter_ms` 总耗时计数，不做详细分段日志。开启后，`mcp_server.py` 会为真实 Recall / Retain / Reflect 输出不包含记忆正文的结构化日志：

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


## Speaker 隔离（共享 ChatGPT 账号）

正式工具 `memory_retain`、`memory_recall`、`memory_reflect` 都要求传稳定 `speaker` ID。

当前约定：

- `liangzai`：靓仔 / 良仔
- `monica`：Monica / 灼暄 / 猫呢咔
- 默认：`monica`
- 客户端或 Project 明确指定身份时，明确身份覆盖默认值。

Gateway 会把 Speaker 作为 Hindsight tag（`speaker:<id>`）写入；Recall / Reflect 使用 strict tag filter，避免共享 memory bank 中不同讲述者的记忆串线。

测试模式切换不再直接编辑 `/etc/memory-mcp.env`：

```bash
memory-mcp mode speaker-probe
memory-mcp mode full
memory-mcp mode status
```

注意：Speaker 功能上线前写入的旧记忆没有 `speaker:<id>` tag，在 strict 模式下不会自动混入任何人的结果。应通过一次性迁移明确归属后再加入 Speaker 范围。
