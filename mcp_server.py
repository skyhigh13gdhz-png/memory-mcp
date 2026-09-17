"""Memory MCP：把 MCP 工具调用翻译成 Memory Gateway REST API。

职责边界：
- 不直接访问 Hindsight；
- 不保存个人记忆；
- 不包含任何 AI 客户端专属逻辑；
- 只依赖 Memory Gateway 的稳定 HTTP API。
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

GATEWAY_BASE_URL = os.environ.get("MEMORY_GATEWAY_URL", "http://127.0.0.1:8787").rstrip("/")
GATEWAY_TOKEN = os.environ.get("MEMORY_GATEWAY_TOKEN", "")
DEFAULT_CLIENT_ID = os.environ.get("MEMORY_CLIENT_ID", "mcp-client")
REQUEST_TIMEOUT = float(os.environ.get("MEMORY_GATEWAY_TIMEOUT", "60"))
MCP_HOST = os.environ.get("MEMORY_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MEMORY_MCP_PORT", "8000"))

mcp = FastMCP(
    "Personal Memory",
    host=MCP_HOST,
    port=MCP_PORT,
    instructions=(
        "访问用户自己的长期外置记忆。普通事实查找使用 memory_recall；"
        "明确要求保存时使用 memory_retain；只有需要综合多条长期记忆时才使用 memory_reflect。"
    ),
)


def _headers() -> dict[str, str]:
    if not GATEWAY_TOKEN:
        raise RuntimeError("MEMORY_GATEWAY_TOKEN is not configured")
    return {"Authorization": f"Bearer {GATEWAY_TOKEN}"}


async def _gateway(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(
            f"{GATEWAY_BASE_URL}{path}",
            headers=_headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    data["mcp_adapter_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return data


@mcp.tool()
async def memory_retain(content: str) -> dict[str, Any]:
    """保存用户明确要求长期记住的信息。不要用于普通闲聊或重复保存整段聊天。"""
    return await _gateway(
        "/v1/memories/retain",
        {"content": content, "client_id": DEFAULT_CLIENT_ID},
    )


@mcp.tool()
async def memory_recall(query: str, max_results: int = 10) -> dict[str, Any]:
    """快速查询过去的事实、决定、经历、项目进度和历史讨论。"""
    return await _gateway(
        "/v1/memories/recall",
        {"query": query, "max_results": max(1, min(max_results, 100)), "client_id": DEFAULT_CLIENT_ID},
    )


@mcp.tool()
async def memory_reflect(query: str) -> dict[str, Any]:
    """综合多条长期记忆进行归纳或反思。普通事实查找不要使用本工具。"""
    return await _gateway(
        "/v1/memories/reflect",
        {"query": query, "client_id": DEFAULT_CLIENT_ID},
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
