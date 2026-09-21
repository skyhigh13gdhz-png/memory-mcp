"""Memory MCP：把 MCP 工具调用翻译成 Memory Gateway REST API。

职责边界：不直接访问 Hindsight、不保存个人记忆、不包含客户端专属逻辑。
"""
from __future__ import annotations
import json
import logging
import os
import time
import uuid
from datetime import date
from typing import Any, Optional
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

logging.basicConfig(
    level=os.environ.get("MEMORY_MCP_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("memory-mcp")

GATEWAY_BASE_URL = os.environ.get("MEMORY_GATEWAY_URL", "http://127.0.0.1:8787").rstrip("/")
GATEWAY_TOKEN = os.environ.get("MEMORY_GATEWAY_TOKEN", "")
DEFAULT_CLIENT_ID = os.environ.get("MEMORY_CLIENT_ID", "mcp-client")
REQUEST_TIMEOUT = float(os.environ.get("MEMORY_GATEWAY_TIMEOUT", "60"))
RECALL_TIMEOUT = float(os.environ.get("MEMORY_RECALL_TIMEOUT", "15"))
RECALL_DEFAULT_RESULTS = int(os.environ.get("MEMORY_RECALL_DEFAULT_RESULTS", "20"))
RECALL_MIN_RESULTS = int(os.environ.get("MEMORY_RECALL_MIN_RESULTS", "10"))
RETAIN_TIMEOUT = float(os.environ.get("MEMORY_RETAIN_TIMEOUT", "90"))
REFLECT_TIMEOUT = float(os.environ.get("MEMORY_REFLECT_TIMEOUT", "180"))
MCP_HOST = os.environ.get("MEMORY_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MEMORY_MCP_PORT", "8000"))
PUBLIC_HOST = os.environ.get("MEMORY_MCP_PUBLIC_HOST", "memory.skyhighmonica.fyi").strip()
TOOL_MODE = os.environ.get("MEMORY_MCP_TOOL_MODE", "full").strip().lower()
TIMING_ENABLED = os.environ.get("MEMORY_MCP_TIMING_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
VALID_TOOL_MODES = {"full", "recall-only", "recall-schema", "ping-only", "speaker-probe"}
if TOOL_MODE not in VALID_TOOL_MODES:
    raise RuntimeError(
        f"Invalid MEMORY_MCP_TOOL_MODE={TOOL_MODE!r}; expected one of {sorted(VALID_TOOL_MODES)}"
    )

allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
if PUBLIC_HOST:
    allowed_hosts.extend([PUBLIC_HOST, f"{PUBLIC_HOST}:*"])
    allowed_origins.extend([f"https://{PUBLIC_HOST}", f"https://{PUBLIC_HOST}:*"])

transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=allowed_hosts,
    allowed_origins=allowed_origins,
)

mcp = FastMCP(
    "Personal Memory",
    host=MCP_HOST,
    port=MCP_PORT,
    transport_security=transport_security,
    instructions=(
        "访问共享账号的长期外置记忆。所有正式记忆工具都必须传稳定 speaker ID。"
        "默认 speaker=monica。只有当前 Project/客户端指令明确固定为 liangzai，或用户在当前聊天中亲自明确声明“我是靓仔/良仔”时，才使用 liangzai。"
        "禁止根据其他聊天、跨聊天记忆、历史习惯、话题内容或模型推断把默认 speaker 改成 liangzai。"
        "别名统一：靓仔/良仔→liangzai；Monica/灼暄/猫呢咔→monica。"
        "普通事实查找使用 memory_recall；明确要求保存时使用 memory_retain；"
        "只有需要综合多条长期记忆时才使用 memory_reflect。"
        "凡是日报、周报、月报、时间范围统计或要求完整覆盖每一天的分析，必须先用 memory_document_range 按日期读取完整原文；"
        "不能用 memory_recall/reflect 的语义结果判断某天没有记录。"
        "修正原始记录时先用 memory_document_list/get 核对，再用 memory_document_patch 做精确替换；"
        "Patch 冲突或歧义时停止并向用户确认。"
    ),
)

def _headers() -> dict[str, str]:
    if not GATEWAY_TOKEN:
        raise RuntimeError("MEMORY_GATEWAY_TOKEN is not configured")
    return {"Authorization": f"Bearer {GATEWAY_TOKEN}"}

def _timing_from_gateway(data: dict[str, Any]) -> dict[str, Any]:
    """只提取 Gateway 已返回的计时字段，避免日志打印记忆正文。"""
    return {
        key: value
        for key, value in data.items()
        if key.endswith("_ms") or key in {"timing", "timings"}
    }

async def _gateway(
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    tool: str,
    timeout: float | None = None,
    method: str = "POST",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 生产默认关闭详细打点。关闭时不生成 UUID、不序列化 timing 日志，
    # 只保留原有请求路径和一个 perf_counter 用于 mcp_adapter_ms。
    request_id = uuid.uuid4().hex[:12] if TIMING_ENABLED else ""
    total_started = time.perf_counter()
    if TIMING_ENABLED:
        logger.info(
            "event=tool_start request_id=%s tool=%s path=%s",
            request_id,
            tool,
            path,
        )
    try:
        if TIMING_ENABLED:
            connect_started = time.perf_counter()
        async with httpx.AsyncClient(timeout=timeout or REQUEST_TIMEOUT) as client:
            if TIMING_ENABLED:
                client_ready_ms = round((time.perf_counter() - connect_started) * 1000, 1)
                upstream_started = time.perf_counter()
            response = await client.request(
                method,
                f"{GATEWAY_BASE_URL}{path}",
                headers=_headers(),
                json=payload,
                params=params,
            )
            if TIMING_ENABLED:
                gateway_http_ms = round((time.perf_counter() - upstream_started) * 1000, 1)
            response.raise_for_status()
            if TIMING_ENABLED:
                decode_started = time.perf_counter()
            data = response.json()
            if TIMING_ENABLED:
                decode_ms = round((time.perf_counter() - decode_started) * 1000, 1)

        total_ms = round((time.perf_counter() - total_started) * 1000, 1)
        data["mcp_adapter_ms"] = total_ms
        if TIMING_ENABLED:
            logger.info(
                "event=tool_end request_id=%s tool=%s status=%s client_ready_ms=%.1f "
                "gateway_http_ms=%.1f decode_ms=%.1f mcp_total_ms=%.1f gateway_timing=%s",
                request_id,
                tool,
                response.status_code,
                client_ready_ms,
                gateway_http_ms,
                decode_ms,
                total_ms,
                json.dumps(_timing_from_gateway(data), ensure_ascii=False, separators=(",", ":")),
            )
        return data
    except Exception as exc:
        total_ms = round((time.perf_counter() - total_started) * 1000, 1)
        if TIMING_ENABLED:
            logger.exception(
                "event=tool_error request_id=%s tool=%s mcp_total_ms=%.1f error_type=%s",
                request_id,
                tool,
                total_ms,
                type(exc).__name__,
            )
        else:
            logger.error("tool=%s failed error_type=%s", tool, type(exc).__name__)
        raise

# 测试模式按复杂度逐级增加：
# ping-only      : 无参数、固定字符串，不访问后端。
# speaker-probe  : 验证 ChatGPT 是否会按 Project instructions 自动传 speaker；不访问后端。
# recall-schema  : 仅一个字符串参数、固定字符串，不访问后端；隔离 Tool input schema。
# recall-only    : 真实 Recall（query + max_results），访问 Gateway/Hindsight。
# full           : 正常三工具。
if TOOL_MODE == "speaker-probe":
    @mcp.tool()
    async def speaker_probe(message: str, speaker: str) -> dict[str, str]:
        """测试讲述者参数能否由客户端上下文稳定传入；不访问 Gateway/Hindsight。"""
        return {"ok": "true", "speaker": speaker, "message": message}

elif TOOL_MODE == "ping-only":
    @mcp.tool()
    async def ping() -> str:
        """只读连通性测试；返回固定文本，不访问任何外部服务。"""
        return "pong"

elif TOOL_MODE == "recall-schema":
    @mcp.tool()
    async def memory_recall(query: str) -> str:
        """最小只读记忆查询兼容性测试。"""
        return f"recall schema ok: {query}"

else:
    @mcp.tool()
    async def memory_recall(
        query: str,
        speaker: str = "monica",
        max_results: int = RECALL_DEFAULT_RESULTS,
    ) -> dict[str, Any]:
        """按当前讲述者查询过去的事实、决定、经历、项目进度和历史讨论。默认返回 20 条，普通查询不要低于 10 条；跨日期完整分析不得用本工具代替 memory_document_range。speaker 默认 monica；只有当前 Project/当前聊天明确身份时才覆盖。"""
        effective_max_results = max(RECALL_MIN_RESULTS, min(max_results, 100))
        return await _gateway(
            "/v1/memories/recall",
            {
                "query": query,
                "speaker": speaker,
                "max_results": effective_max_results,
                "client_id": DEFAULT_CLIENT_ID,
            },
            tool="memory_recall",
            timeout=RECALL_TIMEOUT,
        )

    if TOOL_MODE == "full":
        @mcp.tool()
        async def memory_retain(
            content: str,
            speaker: str = "monica",
            document_id: Optional[str] = None,
            timestamp: Optional[str] = None,
            update_mode: Optional[str] = None,
            idempotency_key: Optional[str] = None,
        ) -> dict[str, Any]:
            """快速受理一条明确要求长期记住的原始记录，返回 operation_id 表示已进入后台处理。一次自然记录动作对应一个 document；新建记录时不要传 update_mode。只有明确更新已知 document_id 时，才同时传 document_id 和 replace/append。已知事件时间时传 ISO 8601 timestamp。客户端重试同一次自然记录时必须复用同一个 idempotency_key（至少 8 字符）；新记录使用新键。未传键时 Gateway 仍提供短窗口自动去重。不要因未立即可检索而改写后重投。"""
            if update_mode not in {None, "replace", "append"}:
                raise ValueError("update_mode must be replace or append")
            if document_id is None:
                update_mode = None
            payload: dict[str, Any] = {
                "content": content,
                "speaker": speaker,
                "client_id": DEFAULT_CLIENT_ID,
                "async_processing": True,
            }
            if document_id is not None:
                payload["document_id"] = document_id
            if timestamp is not None:
                payload["timestamp"] = timestamp
            if update_mode is not None:
                payload["update_mode"] = update_mode
            if idempotency_key is not None:
                payload["idempotency_key"] = idempotency_key
            return await _gateway(
                "/v1/memories/retain",
                payload,
                tool="memory_retain",
                timeout=RETAIN_TIMEOUT,
            )

        @mcp.tool()
        async def memory_operation_get(
            operation_id: str,
            speaker: str = "monica",
        ) -> dict[str, Any]:
            """查询一次异步记忆写入的真实状态。retain 返回 operation_id 只表示已受理；需要确认时用本工具区分 pending、processing、completed、failed 或 cancelled。completed 才表示处理完成；last_error 可能只是成功重试前的历史错误，不能覆盖当前 status。"""
            return await _gateway(
                f"/v1/operations/{quote(operation_id, safe='')}",
                tool="memory_operation_get",
                method="GET",
                params={"speaker": speaker},
            )

        @mcp.tool()
        async def memory_reflect(query: str, speaker: str = "monica") -> dict[str, Any]:
            """只基于当前讲述者的长期记忆进行归纳或反思。speaker 只传稳定 ID：liangzai 或 monica。普通事实查找不要使用本工具。"""
            return await _gateway(
                "/v1/memories/reflect",
                {"query": query, "speaker": speaker, "client_id": DEFAULT_CLIENT_ID},
                tool="memory_reflect",
                timeout=REFLECT_TIMEOUT,
            )

        @mcp.tool()
        async def memory_document_list(
            speaker: str = "monica",
            query: Optional[str] = None,
            limit: int = 100,
            offset: int = 0,
        ) -> dict[str, Any]:
            """列出当前讲述者的原始记忆文档。需要定位待核对或待修正记录时使用；query 可按文档内容或 ID 搜索。"""
            params: dict[str, Any] = {
                "speaker": speaker,
                "limit": max(1, min(limit, 100)),
                "offset": max(0, offset),
            }
            if query:
                params["q"] = query
            return await _gateway(
                "/v1/documents",
                tool="memory_document_list",
                method="GET",
                params=params,
            )

        @mcp.tool()
        async def memory_document_range(
            start_date: str,
            end_date: str,
            speaker: str = "monica",
            limit: int = 100,
        ) -> dict[str, Any]:
            """按事件日期确定性读取一段时间内的全部原始记录及完整正文。日报、周报、月报、饮食/睡眠/交易等范围统计必须先调用本工具，不能用语义 Recall/Reflect 代替完整性查询。日期格式 YYYY-MM-DD，起止日期均包含。"""
            try:
                start = date.fromisoformat(start_date)
                end = date.fromisoformat(end_date)
            except ValueError as exc:
                raise ValueError("start_date and end_date must use YYYY-MM-DD") from exc
            if start > end:
                raise ValueError("start_date must be <= end_date")
            if (end - start).days > 366:
                raise ValueError("date range must not exceed 366 days")
            return await _gateway(
                "/v1/documents",
                tool="memory_document_range",
                method="GET",
                params={
                    "speaker": speaker,
                    "date_from": start.isoformat(),
                    "date_to": end.isoformat(),
                    "include_text": "true",
                    "limit": max(1, min(limit, 1000)),
                    "offset": 0,
                },
            )

        @mcp.tool()
        async def memory_document_get(document_id: str, speaker: str = "monica") -> dict[str, Any]:
            """读取当前讲述者的一份原始记忆文档，用于确认原文后再执行精确修正。"""
            return await _gateway(
                f"/v1/documents/{quote(document_id, safe='')}",
                tool="memory_document_get",
                method="GET",
                params={"speaker": speaker},
            )

        @mcp.tool()
        async def memory_document_patch(
            document_id: str,
            expected_text: str,
            replacement_text: str,
            reason: str,
            speaker: str = "monica",
        ) -> dict[str, Any]:
            """对原始记忆文档做可审计的精确替换。先 Get 核对原文；expected_text 必须且只能命中一次。冲突或歧义时停止并向用户确认，不要整篇重写。"""
            return await _gateway(
                f"/v1/documents/{quote(document_id, safe='')}/patch",
                {
                    "speaker": speaker,
                    "expected_text": expected_text,
                    "replacement_text": replacement_text,
                    "reason": reason,
                    "client_id": DEFAULT_CLIENT_ID,
                },
                tool="memory_document_patch",
            )

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
