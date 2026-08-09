import asyncio
import json
import os
import sys
import httpx
import traceback

MCP_URL = os.getenv("OM_MCP_URL", "http://localhost:8585/mcp")
TOKEN = os.getenv("OM_INGESTION_BOT_TOKEN") or "eyJraWQiOiJsb2NhbC1kZXYta2V5IiwiYWxnIjoiUlMyNTYiLCJ0eXAiOiJKV1QifQ.eyJpc3MiOiJvcGVuLW1ldGFkYXRhLm9yZyIsInN1YiI6ImluZ2VzdGlvbi1ib3QiLCJyb2xlcyI6WyJJbmdlc3Rpb25Cb3RSb2xlIl0sImVtYWlsIjoiaW5nZXN0aW9uLWJvdEBvcGVuLW1ldGFkYXRhLm9yZyIsImlzQm90Ijp0cnVlLCJ0b2tlblR5cGUiOiJCT1QiLCJ1c2VybmFtZSI6ImluZ2VzdGlvbi1ib3QiLCJwcmVmZXJyZWRfdXNlcm5hbWUiOiJpbmdlc3Rpb24tYm90IiwiaWF0IjoxNzg1NTU2NjExLCJleHAiOm51bGx9.S-zCq5FIBBbrZkb9aQbke0ryft5-VPavkdvNoszV5DumjOCDpwTppp2sjey5q7SBV2I09oFBen_N1v15g7vZKuVpGOGkWBYYNDJwL_TkZKas9Syaal3Vt9bcIe7jsAHmOUAn21baU-2HLprzjkoeqeNJ4KaLvc5bn8HYG3H1H6320Qls_i0EppRauXuZ-oueYJSOPjuRYaRkIC4kdTtMZesre0vPUma1nEeLLZQWtngJv68tOxAtTAMiOrc7zFk2NyF9fWxpRRb8umEm1y7QW9JBkxtzm8oDu74YkX9Da6v4uLv-CjWTlDqugCbL1nKWUIYFQ_yXqvZzYLF0GCW_-A"

async def read_sse(client, post_url_event):
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "text/event-stream"}
    async with client.stream("GET", MCP_URL, headers=headers, timeout=None) as response:
        response.raise_for_status()
        
        event_type = None
        async for line in response.aiter_lines():
            if not line:
                continue
            
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
                if event_type == "endpoint":
                    # Absolute URL or relative
                    if data.startswith("http"):
                        post_url = data
                    else:
                        base = MCP_URL.rsplit('/', 1)[0]
                        post_url = f"{base}{data}" if data.startswith("/") else f"{MCP_URL}/{data}"
                    post_url_event.set_result(post_url)
                elif event_type == "message":
                    # Forward to Claude via stdout
                    sys.stdout.write(data + "\n")
                    sys.stdout.flush()

async def read_stdin(client, post_url_event):
    post_url = await post_url_event
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
            
        line = line.strip()
        if not line:
            continue
            
        # Forward to OpenMetadata via POST
        await client.post(post_url, content=line, headers=headers, timeout=30.0)

async def main():
    post_url_event = asyncio.get_running_loop().create_future()
    async with httpx.AsyncClient() as client:
        try:
            await asyncio.gather(
                read_sse(client, post_url_event),
                read_stdin(client, post_url_event)
            )
        except Exception:
            pass # Exit on EOF or connection close

if __name__ == "__main__":
    asyncio.run(main())
