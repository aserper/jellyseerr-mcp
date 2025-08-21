import httpx
import asyncio

async def main():
    session_id = None
    async with httpx.AsyncClient() as client:
        async with client.stream("GET", "http://127.0.0.1:8797/sse") as response:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    session_id = line.split("session_id=")[1]
                    break
        
        print(f"Session ID: {session_id}")

        if session_id:
            response = await client.post(
                f"http://127.0.0.1:8797/messages/?session_id={session_id}",
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "ping",
                        "arguments": {}
                    },
                    "id": 1
                }
            )
            print(response.text)

if __name__ == "__main__":
    asyncio.run(main())
