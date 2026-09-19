#!/usr/bin/env python3
"""验证 MCP 原始文档 List/Get/Patch 与 CAS 冲突保护。"""

import asyncio
import json
import os
from datetime import datetime, timezone

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = os.environ.get("MEMORY_MCP_TEST_URL", "http://127.0.0.1:8000/mcp")


def result_text(result) -> str:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return "\n".join(getattr(item, "text", str(item)) for item in result.content)


async def call(session: ClientSession, name: str, arguments: dict, *, expect_error: bool = False):
    result = await session.call_tool(name, arguments=arguments)
    if bool(result.isError) != expect_error:
        state = "失败" if result.isError else "成功"
        raise RuntimeError(f"{name} 意外{state}: {result.content}")
    print(f"[✓] {name}: {'按预期拒绝冲突' if expect_error else '成功'}")
    return result


async def main() -> None:
    marker = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    document_id = f"mcp-document-{marker}"
    speaker = "audit-mcp"
    original = f"MCP 文档验收 {marker}：初始预算为 42 元。"
    expected = "初始预算为 42 元"
    replacement = "初始预算为 52 元"
    required = {
        "memory_retain",
        "memory_document_list",
        "memory_document_get",
        "memory_document_patch",
    }

    async with streamable_http_client(MCP_URL) as (read_stream, write_stream, *_):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            missing = required - names
            if missing:
                raise RuntimeError(f"缺少 MCP tools: {sorted(missing)}")

            await call(session, "memory_retain", {
                "content": original,
                "speaker": speaker,
                "document_id": document_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "update_mode": "replace",
            })
            listed = await call(session, "memory_document_list", {
                "speaker": speaker,
                "query": document_id,
                "limit": 10,
            })
            if document_id not in result_text(listed):
                raise RuntimeError("列表结果中未找到新文档")

            fetched = await call(session, "memory_document_get", {
                "document_id": document_id,
                "speaker": speaker,
            })
            if expected not in result_text(fetched):
                raise RuntimeError("新文档原文不符合预期")

            patch_args = {
                "document_id": document_id,
                "speaker": speaker,
                "expected_text": expected,
                "replacement_text": replacement,
                "reason": "MCP Document API 端到端验收",
            }
            await call(session, "memory_document_patch", patch_args)
            patched = await call(session, "memory_document_get", {
                "document_id": document_id,
                "speaker": speaker,
            })
            patched_text = result_text(patched)
            if replacement not in patched_text or expected in patched_text:
                raise RuntimeError("Patch 后原文不符合预期")

            await call(session, "memory_document_patch", patch_args, expect_error=True)
            await call(session, "memory_document_get", {
                "document_id": document_id,
                "speaker": "monica",
            }, expect_error=True)

    print("\n========== Memory MCP Document API 验收 ==========")
    print(f"[✓] document_id: {document_id}")
    print("[✓] Retain → List → Get → Patch → Get")
    print("[✓] 重复 Patch 冲突保护")
    print("[✓] Speaker 跨用户读取隔离")


if __name__ == "__main__":
    asyncio.run(main())
