"""Memory MCP：把 MCP 工具调用翻译成 Memory Gateway REST API。

职责边界：不直接访问 Hindsight、不保存个人记忆、不包含客户端专属逻辑。
"""
from __future__ import annotations
import os, time
from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

GATEWAY_BASE_URL = os.environ.get("MEMORY_GATEWAY_URL", "http://127.0.0.1:8787").rstrip("/")
GATEWAY_TOKEN = os.environ.get("MEMORY_GATEWAY_TOKEN", "")
DEFAULT_CLIENT_ID = os.environ.get("MEMORY_CLIENT_ID", "mcp-client")
REQUEST_TIMEOUT = float(os.environ.get("MEMORY_GATEWAY_TIMEOUT", "60"))
MCP_HOST = os.environ.get("MEMORY_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MEMORY_MCP_PORT", "8000"))
PUBLIC_HOST = os.environ.get("MEMORY_MCP_PUBLIC_HOST", "memory.skyhighmonica.fyi").strip()
TOOL_MODE = os.environ.get("MEMORY_MCP_TOOL_MODE", "full").strip().lower()
VALID_TOOL_MODES = {"full", "recall-only", "ping-only"}
if TOOL_MODE not in VALID_TOOL_MODES:
    raise RuntimeError(
        f"Invalid MEMORY_MCP_TOOL_MODE={TOOL_MODE!r}; expected one of {sorted(VALID_TOOL_MODES)}"
    )

# MCP Python SDK 默认只允许 localhost Host header，以防 DNS rebinding。
# Memory MCP 通过 Cloudflare Tunnel 对外发布，因此显式只允许 localhost 和配置的公网域名，
# 而不是关闭 DNS rebinding 防护。
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

async def _gateway(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(
            f"{GATEWAY_BASE_URL}{path}", headers=_headers(), json=payload
        )
        response.raise_for_status()
        data = response.json()
    data["mcp_adapter_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return data

# full：正常生产工具集。
# recall-only：A/B 测试，只暴露纯读取记忆工具，排除 write/reflect 能力影响。
# ping-only：最小 MCP 兼容性测试，不访问 Gateway/Hindsight。
if TOOL_MODE == "ping-only":
    @mcp.tool()
    async def ping() -> str:
        """只读连通性测试；返回固定文本，不访问任何外部服务。"""
        return "pong"
else:
    @mcp.tool()
    async def memory_recall(query: str, max_results: int = 10) -> dict[str, Any]:
        """快速查询过去的事实、决定、经历、项目进度和历史讨论。"""
        return await _gateway(
            "/v1/memories/recall",
            {
                "query": query,
                "max_results": max(1, min(max_results, 100)),
                "client_id": DEFAULT_CLIENT_ID,
            },
        )

    if TOOL_MODE == "full":
        @mcp.tool()
        async def memory_retain(content: str) -> dict[str, Any]:
            """保存用户明确要求长期记住的信息。不要用于普通闲聊或重复保存整段聊天。"""
            return await _gateway(
                "/v1/memories/retain",
                {"content": content, "client_id": DEFAULT_CLIENT_ID},
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
