#!/usr/bin/env python3
"""验证 MCP → Gateway → Hindsight 三条真实工具链并打印端到端耗时。"""

import asyncio
import json
import os
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = os.environ.get("MEMORY_MCP_TEST_URL", "http://127.0.0.1:8000/mcp")
TEST_FACT = "外置记忆第一阶段优先测试手动调用效率。"


async def call(session: ClientSession, name: str, arguments: dict) -> float:
    started = time.perf_counter()
    result = await session.call_tool(name, arguments=arguments)
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    print(f"\n{name}: MCP 客户端端到端 {elapsed} ms")
    if result.isError:
        raise RuntimeError(f"{name} failed: {result.content}")
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        print(json.dumps(structured, ensure_ascii=False, indent=2))
    else:
        print(result.content)
    return elapsed


async def main() -> None:
    async with streamable_http_client(MCP_URL) as (read_stream, write_stream, *_):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            required = {"memory_retain", "memory_recall", "memory_reflect"}
            missing = required - set(names)
            if missing:
                raise RuntimeError(f"缺少 MCP tools: {sorted(missing)}")
            print(f"[✓] MCP tools: {', '.join(sorted(required))}")

            timings = {}
            timings["retain"] = await call(session, "memory_retain", {"content": TEST_FACT})
            timings["recall"] = await call(session, "memory_recall", {"query": "外置记忆第一阶段优先测试什么？", "max_results": 5})
            timings["reflect"] = await call(session, "memory_reflect", {"query": "为什么外置记忆第一阶段先测试手动调用效率？"})

    print("\n========== Memory MCP 端到端验收 ==========")
    print("[✓] MCP → Gateway → Hindsight Retain")
    print("[✓] MCP → Gateway → Hindsight Recall")
    print("[✓] MCP → Gateway → Hindsight → Memory LLM Reflect")
    print(f"耗时：Retain {timings['retain']} ms / Recall {timings['recall']} ms / Reflect {timings['reflect']} ms")
    print("结果：Memory MCP 本机核心链路正常。")


if __name__ == "__main__":
    asyncio.run(main())
