"""Manual stdio smoke check: initialize, discover tools and ping, no backend calls."""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "arrchestra_mcp"], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            tools = await session.list_tools()
            ping = await session.call_tool("ping", {})
            print(f"Connected to {info.serverInfo.name}: {len(tools.tools)} tools")
            print("Ping failed" if ping.isError else "Ping OK")


if __name__ == "__main__":
    asyncio.run(main())
