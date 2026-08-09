import asyncio
import os
import sys
import traceback

import httpx

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


MCP_URL = os.getenv(
    "OM_MCP_URL",
    "http://localhost:8585/mcp",
)

TOKEN = "eyJraWQiOiJsb2NhbC1kZXYta2V5IiwiYWxnIjoiUlMyNTYiLCJ0eXAiOiJKV1QifQ.eyJpc3MiOiJvcGVuLW1ldGFkYXRhLm9yZyIsInN1YiI6ImluZ2VzdGlvbi1ib3QiLCJyb2xlcyI6WyJJbmdlc3Rpb25Cb3RSb2xlIl0sImVtYWlsIjoiaW5nZXN0aW9uLWJvdEBvcGVuLW1ldGFkYXRhLm9yZyIsImlzQm90Ijp0cnVlLCJ0b2tlblR5cGUiOiJCT1QiLCJ1c2VybmFtZSI6ImluZ2VzdGlvbi1ib3QiLCJwcmVmZXJyZWRfdXNlcm5hbWUiOiJpbmdlc3Rpb24tYm90IiwiaWF0IjoxNzg1NTU2NjExLCJleHAiOm51bGx9.S-zCq5FIBBbrZkb9aQbke0ryft5-VPavkdvNoszV5DumjOCDpwTppp2sjey5q7SBV2I09oFBen_N1v15g7vZKuVpGOGkWBYYNDJwL_TkZKas9Syaal3Vt9bcIe7jsAHmOUAn21baU-2HLprzjkoeqeNJ4KaLvc5bn8HYG3H1H6320Qls_i0EppRauXuZ-oueYJSOPjuRYaRkIC4kdTtMZesre0vPUma1nEeLLZQWtngJv68tOxAtTAMiOrc7zFk2NyF9fWxpRRb8umEm1y7QW9JBkxtzm8oDu74YkX9Da6v4uLv-CjWTlDqugCbL1nKWUIYFQ_yXqvZzYLF0GCW_-A"


async def main():
    if not TOKEN:
        print("ERROR: OM_INGESTION_BOT_TOKEN chưa được set")
        print()
        print("Ví dụ:")
        print("  export OM_INGESTION_BOT_TOKEN='eyJ...'")
        sys.exit(1)

    print("=" * 70)
    print("OpenMetadata MCP test")
    print("=" * 70)
    print(f"URL   : {MCP_URL}")
    print(f"Token : {'*' * 10}{TOKEN[-8:]}")
    print()

    headers = {
        "Authorization": f"Bearer {TOKEN}",
    }

    timeout = httpx.Timeout(
        connect=10.0,
        read=30.0,
        write=30.0,
        pool=30.0,
    )

    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        ) as http_client:

            print("[1] Connecting to MCP server...")

            async with streamable_http_client(
                MCP_URL,
                http_client=http_client,
            ) as (
                read_stream,
                write_stream,
                get_session_id,
            ):

                print("[2] Transport connected")

                async with ClientSession(
                    read_stream,
                    write_stream,
                ) as session:

                    print("[3] Initializing MCP session...")

                    init_result = await session.initialize()

                    print()
                    print("========== INITIALIZE OK ==========")

                    try:
                        print(
                            init_result.model_dump_json(
                                indent=2,
                                by_alias=True,
                            )
                        )
                    except Exception:
                        print(init_result)

                    # -------------------------------------------------
                    # LIST TOOLS
                    # -------------------------------------------------

                    print()
                    print("[4] Listing MCP tools...")

                    tools_result = await session.list_tools()

                    print()
                    print(
                        f"========== TOOLS ({len(tools_result.tools)}) =========="
                    )

                    for i, tool in enumerate(tools_result.tools, 1):
                        print()
                        print(f"{i}. {tool.name}")

                        description = getattr(
                            tool,
                            "description",
                            None,
                        )

                        if description:
                            print(f"   {description}")

                    # -------------------------------------------------
                    # LIST RESOURCES
                    # -------------------------------------------------

                    print()
                    print("[5] Listing MCP resources...")

                    try:
                        resources_result = await session.list_resources()

                        print()
                        print(
                            "========== RESOURCES "
                            f"({len(resources_result.resources)}) =========="
                        )

                        for resource in resources_result.resources:
                            print(f"- {resource.uri}")

                    except Exception as e:
                        print(
                            "Resources không available "
                            f"hoặc bot không có quyền: {e}"
                        )

                    # -------------------------------------------------
                    # LIST PROMPTS
                    # -------------------------------------------------

                    print()
                    print("[6] Listing MCP prompts...")

                    try:
                        prompts_result = await session.list_prompts()

                        print()
                        print(
                            "========== PROMPTS "
                            f"({len(prompts_result.prompts)}) =========="
                        )

                        for prompt in prompts_result.prompts:
                            print(f"- {prompt.name}")

                    except Exception as e:
                        print(
                            "Prompts không available "
                            f"hoặc bot không có quyền: {e}"
                        )

                    print()
                    print("=" * 70)
                    print("MCP TEST SUCCESS")
                    print("=" * 70)

    except Exception as e:
        print()
        print("=" * 70)
        print("MCP TEST FAILED")
        print("=" * 70)
        print()
        print(f"{type(e).__name__}: {e}")
        print()

        traceback.print_exc()

        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())