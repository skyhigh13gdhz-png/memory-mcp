"""Memory MCP：把 MCP 工具调用翻译成 Memory Gateway REST API。

职责边界：不直接访问 Hindsight、不保存个人记忆、不包含客户端专属逻辑。
"""
from __future__ import annotations
import json
import logging
import os
import time
import uuid
from typing import Any

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
RETAIN_TIMEOUT = float(os.environ.get("MEMORY_RETAIN_TIMEOUT", "90"))
REFLECT_TIMEOUT = float(os.environ.get("MEMORY_REFLECT_TIMEOUT", "180"))
MCP_HOST = os.environ.get("MEMORY_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MEMORY_MCP_PORT", "8000"))
PUBLIC_HOST = os.environ.get("MEMORY_MCP_PUBLIC_HOST", "memory.skyhighmonica.fyi").strip()
TOOL_MODE = os.environ.get("MEMORY_MCP_TOOL_MODE", "full").strip().lower()
TIMING_ENABLED = os.environ.get("MEMORY_MCP_TIMING_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
VALID_TOOL_MODES = {"full", "recall-only", "recall-schema", "ping-only"}
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
        "访问用户自己的长期外置记忆。普通事实查找使用 memory_recall；"
        "明确要求保存时使用 memory_retain；只有需要综合多条长期记忆时才使用 memory_reflect。"
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
    payload: dict[str, Any],
    *,
    tool: str,
    timeout: float | None = None,
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
            response = await client.post(
                f"{GATEWAY_BASE_URL}{path}", headers=_headers(), json=payload
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
# recall-schema  : 仅一个字符串参数、固定字符串，不访问后端；隔离 Tool input schema。
# recall-only    : 真实 Recall（query + max_results），访问 Gateway/Hindsight。
# full           : 正常三工具。
if TOOL_MODE == "ping-only":
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
    async def memory_recall(query: str, max_results: int = 10) -> dict[str, Any]:
        """快速查询过去的事实、决定、经历、项目进度和历史讨论。"""
        return await _gateway(
            "/v1/memories/recall",
            {"query": query, "max_results": max(1, min(max_results, 100)), "client_id": DEFAULT_CLIENT_ID},
            tool="memory_recall",
            timeout=RECALL_TIMEOUT,
        )

    if TOOL_MODE == "full":
        @mcp.tool()
        async def memory_retain(content: str) -> dict[str, Any]:
            """保存用户明确要求长期记住的信息。不要用于普通闲聊或重复保存整段聊天。"""
            return await _gateway(
                "/v1/memories/retain",
                {"content": content, "client_id": DEFAULT_CLIENT_ID},
                tool="memory_retain",
                timeout=RETAIN_TIMEOUT,
            )

        @mcp.tool()
        async def memory_reflect(query: str) -> dict[str, Any]:
            """综合多条长期记忆进行归纳或反思。普通事实查找不要使用本工具。"""
            return await _gateway(
                "/v1/memories/reflect",
                {"query": query, "client_id": DEFAULT_CLIENT_ID},
                tool="memory_reflect",
                timeout=REFLECT_TIMEOUT,
            )

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
