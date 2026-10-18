"""Synthetic QA server with a graceful file sentinel; never used in production."""
import asyncio
import os
from pathlib import Path

import uvicorn
from .app import create_app


async def main():
    sentinel = Path(os.environ["NIGHTINGALE_QA_STOP"])
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=int(os.environ['NIGHTINGALE_PORT']), log_level="warning"))
    async def watch():
        while not sentinel.exists():
            await asyncio.sleep(.25)
        server.should_exit = True
    watcher = asyncio.create_task(watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


if __name__ == '__main__':
    asyncio.run(main())
