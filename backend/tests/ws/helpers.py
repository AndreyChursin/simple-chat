import asyncio
import json
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

ORIGIN = "http://testserver"


async def ws_connect(
    server: str, client: httpx.AsyncClient | None, *, tab: str = "tab-1", origin: str | None = ORIGIN
) -> ClientConnection:
    headers = {}
    if client is not None:
        headers["Cookie"] = f"chat_session={client.cookies.get('chat_session')}"
    return await connect(f"ws://{server}/ws?tab_id={tab}", additional_headers=headers, origin=origin)  # type: ignore[arg-type]


async def ws_open(server: str, client: httpx.AsyncClient, *, tab: str = "tab-1") -> ClientConnection:
    """Connect and consume `hello`."""
    ws = await ws_connect(server, client, tab=tab)
    hello = json.loads(await asyncio.wait_for(ws.recv(), 3))
    assert hello["type"] == "hello", hello
    return ws


async def recv_type(ws: ClientConnection, event_type: str, wait: float = 3.0) -> dict[str, Any]:
    """Next event of the given type (others are skipped)."""
    async with asyncio.timeout(wait):
        while True:
            event: dict[str, Any] = json.loads(await ws.recv())
            if event["type"] == event_type:
                return event


async def nothing_of_type(ws: ClientConnection, event_type: str, wait: float = 0.3) -> bool:
    try:
        await recv_type(ws, event_type, wait)
    except TimeoutError:
        return True
    return False


async def close_code(ws: ClientConnection, wait: float = 3.0) -> int | None:
    try:
        async with asyncio.timeout(wait):
            while True:
                await ws.recv()
    except ConnectionClosed as exc:
        return exc.rcvd.code if exc.rcvd else None


async def recv_types(ws: ClientConnection, *event_types: str, wait: float = 3.0) -> dict[str, dict[str, Any]]:
    """The first event of each given type, in whatever order they arrive."""
    found: dict[str, dict[str, Any]] = {}
    async with asyncio.timeout(wait):
        while len(found) < len(event_types):
            event: dict[str, Any] = json.loads(await ws.recv())
            if event["type"] in event_types and event["type"] not in found:
                found[event["type"]] = event
    return found
