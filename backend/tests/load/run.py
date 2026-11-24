"""Load test of the base product (docs/ROADMAP_AND_DOD.md, stage 9).

    docker compose exec backend python -m tests.load.run --users 300 --rooms 20

Against the running backend (`--url`, default ws://localhost:8000 inside the backend container):

1. Prepares data straight in the database (registration through the API would be rate limited and spends
   most of the time in Argon2): `--users` users with a session each, `--rooms` rooms (every user is in a
   few of them) and one big room with `--big-room` members (the active users + idle filler accounts).
2. Opens one WebSocket per active user, then for `--duration` seconds every user sends messages at random
   (`--rate` per second on average, a fifth of them to the big room) while a prober keeps closing and
   reopening random users' sockets.
3. Measures, with one clock (sender and receivers live in this process):
   - delivery: send → `new_message` at every other online member of the chat;
   - presence: socket closed / reopened → `presence_update` at every online room-mate;
   - lost deliveries, `error` events, unexpected socket closes.
4. Deletes everything it created (`--keep` leaves it) and exits 1 if p95 delivery ≥ 3 s, p95 presence
   ≥ 2 s or anything went wrong.
"""

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, insert, text
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from app.core.config import get_settings
from app.core.passwords import hash_password
from app.core.tokens import hash_token, new_token
from app.db.models import Room, RoomMember, RoomVisibility, User, UserSession
from app.db.session import SessionLocal, engine

DELIVERY_P95_LIMIT = 3.0
PRESENCE_P95_LIMIT = 2.0
ROOMS_PER_USER = 3
BIG_ROOM_SHARE = 0.2


@dataclass
class Client:
    index: int
    user_id: int
    token: str
    rooms: list[int]
    ws: ClientConnection | None = None
    reader: asyncio.Task[None] | None = None
    probing: bool = False  # closed on purpose by the presence prober


@dataclass
class Stats:
    delivery: list[float] = field(default_factory=list)
    ack: list[float] = field(default_factory=list)  # send → ack: the server's own processing time
    presence: list[float] = field(default_factory=list)
    expected: int = 0
    received: int = 0
    sent: int = 0
    acks: int = 0
    errors: list[str] = field(default_factory=list)
    unexpected_closes: int = 0
    duplicates: int = 0


class LoadTest:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.run_id = uuid.uuid4().hex[:6]
        self.stats = Stats()
        self.clients: list[Client] = []
        self.room_members: dict[int, set[int]] = defaultdict(set)  # room id → active user ids
        self.by_user: dict[int, Client] = {}
        self.online: set[int] = set()
        self.seen: set[tuple[int, int]] = set()  # (receiver, message id)
        self.sent_at: dict[str, float] = {}  # req_id → send time
        # presence probes: (user id, presence) → (t0, observers still waiting)
        self.probes: dict[tuple[int, str], tuple[float, set[int]]] = {}
        self.user_ids: list[int] = []
        self.room_ids: list[int] = []
        self.origin = get_settings().public_origin
        self.stopping = False

    # --- data ---------------------------------------------------------------------------------------

    async def prepare(self) -> None:
        a = self.args
        filler = max(a.big_room - a.users, 0)
        total = a.users + filler
        pw = await hash_password(uuid.uuid4().hex)  # nobody logs in with it
        now = datetime.now(UTC)
        async with SessionLocal() as db:
            rows = [
                {
                    "email": f"lt{self.run_id}-{i}@load.test",
                    "username": f"lt{self.run_id}_{i}",
                    "password_hash": pw,
                    "created_at": now,
                    "password_changed_at": now,
                }
                for i in range(total)
            ]
            self.user_ids = list(
                await db.scalars(insert(User).returning(User.id, sort_by_parameter_order=True), rows)
            )
            active = self.user_ids[: a.users]
            tokens = [new_token() for _ in active]
            await db.execute(
                insert(UserSession),
                [
                    {
                        "user_id": uid,
                        "token_hash": hash_token(tok),
                        "persistent": True,
                        "user_agent": "load-test",
                        "created_at": now,
                        "last_seen_at": now,
                        "expires_at": now + timedelta(days=1),
                    }
                    for uid, tok in zip(active, tokens, strict=True)
                ],
            )
            owner = active[0]
            room_rows = [
                {
                    "name": f"lt{self.run_id}-{i}" if i else f"lt{self.run_id}-big",
                    "description": "load test",
                    "visibility": RoomVisibility.public,
                    "owner_id": owner,
                    "created_at": now,
                    "member_count": 0,
                }
                for i in range(a.rooms)
            ]
            self.room_ids = list(
                await db.scalars(insert(Room).returning(Room.id, sort_by_parameter_order=True), room_rows)
            )
            big, small = self.room_ids[0], self.room_ids[1:]
            members: dict[int, set[int]] = defaultdict(set)
            members[big] = set(self.user_ids[: max(a.big_room, a.users)])
            for uid in active:
                for rid in random.sample(small, min(ROOMS_PER_USER, len(small))):
                    members[rid].add(uid)
            members[big].add(owner)
            for rid in small:
                members[rid].add(owner)
            await db.execute(
                insert(RoomMember),
                [
                    {"room_id": rid, "user_id": uid, "is_admin": uid == owner, "joined_at": now}
                    for rid, uids in members.items()
                    for uid in uids
                ],
            )
            for rid, uids in members.items():
                await db.execute(
                    text("UPDATE rooms SET member_count = :n WHERE id = :r"), {"n": len(uids), "r": rid}
                )
            await db.commit()
        active_set = set(active)
        for rid, uids in members.items():
            self.room_members[rid] = uids & active_set
        for i, (uid, tok) in enumerate(zip(active, tokens, strict=True)):
            rooms = [rid for rid, uids in members.items() if uid in uids]
            client = Client(index=i, user_id=uid, token=tok, rooms=rooms)
            self.clients.append(client)
            self.by_user[uid] = client
        sizes = sorted((len(m) for r, m in members.items() if r != big), reverse=True)
        print(
            f"prepared {a.users} active + {filler} idle users, {a.rooms} rooms "
            f"(big room: {len(members[big])} members, others: {sizes[-1]}–{sizes[0]})",
            flush=True,
        )

    async def cleanup(self) -> None:
        async with SessionLocal() as db:
            await db.execute(delete(Room).where(Room.id.in_(self.room_ids)))
            await db.execute(delete(User).where(User.id.in_(self.user_ids)))
            await db.commit()
        print("cleaned up", flush=True)

    # --- sockets --------------------------------------------------------------------------------------

    async def open(self, client: Client) -> None:
        ws = await connect(
            f"{self.args.url}/ws?tab_id=load-{client.index}",
            additional_headers={"Cookie": f"chat_session={client.token}"},
            origin=self.origin,  # type: ignore[arg-type]
            max_size=2**20,
        )
        hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
        assert hello["type"] == "hello", hello
        client.ws = ws
        client.reader = asyncio.create_task(self.read(client, ws))
        self.online.add(client.user_id)

    async def read(self, client: Client, ws: ClientConnection) -> None:
        try:
            async for raw in ws:
                self.on_event(client, json.loads(raw), time.time())
        except ConnectionClosed:
            pass
        if not client.probing and not self.stopping:
            self.stats.unexpected_closes += 1
        self.online.discard(client.user_id)

    def on_event(self, client: Client, event: dict[str, Any], now: float) -> None:
        kind = event["type"]
        if kind == "new_message":
            msg = event["message"]
            parts = msg["content"].split("|")
            if parts[0] != "LT" or msg["author"]["id"] == client.user_id:
                return
            key = (client.user_id, msg["id"])
            if key in self.seen:
                self.stats.duplicates += 1
                return
            self.seen.add(key)
            self.stats.received += 1
            self.stats.delivery.append(now - float(parts[1]))
        elif kind == "presence_update":
            probe = self.probes.get((event["user_id"], event["presence"]))
            if probe is not None and client.user_id in probe[1]:
                probe[1].discard(client.user_id)
                self.stats.presence.append(now - probe[0])
        elif kind == "ack":
            self.stats.acks += 1
            started = self.sent_at.pop(event.get("req_id") or "", None)
            if started is not None:
                self.stats.ack.append(now - started)
        elif kind == "error":
            self.stats.errors.append(f"{event.get('code')}: {event.get('message')}")

    def observers(self, user_id: int) -> set[int]:
        """Online users sharing a room with `user_id` (they are in its presence audience)."""
        result: set[int] = set()
        for rid in self.by_user[user_id].rooms:
            result |= self.room_members[rid]
        return (result & self.online) - {user_id}

    # --- traffic --------------------------------------------------------------------------------------

    async def sender(self, client: Client, until: float) -> None:
        big = self.room_ids[0]
        small = [r for r in client.rooms if r != big]
        last_activity = time.monotonic()
        while True:
            await asyncio.sleep(random.expovariate(self.args.rate))
            if time.monotonic() >= until:
                return
            if client.ws is None or client.probing:
                continue
            if time.monotonic() - last_activity > 20:  # a user typing is active (no AFK in the middle)
                last_activity = time.monotonic()
                try:
                    await client.ws.send('{"type":"activity"}')
                except ConnectionClosed:
                    continue
            rid = big if (random.random() < BIG_ROOM_SHARE or not small) else random.choice(small)
            receivers = (self.room_members[rid] & self.online) - {client.user_id}
            content = f"LT|{time.time():.6f}|{uuid.uuid4().hex[:8]}"
            req_id = uuid.uuid4().hex[:12]
            self.sent_at[req_id] = time.time()
            try:
                await client.ws.send(
                    json.dumps(
                        {
                            "type": "send_message",
                            "req_id": req_id,
                            "chat": {"type": "room", "id": rid},
                            "client_msg_id": str(uuid.uuid4()),
                            "content": content,
                        }
                    )
                )
            except ConnectionClosed:
                continue
            self.stats.sent += 1
            self.stats.expected += len(receivers)

    async def prober(self, until: float) -> None:
        """Close a random user's socket, wait for `offline` at their room-mates, reopen, wait for `online`."""
        while time.monotonic() < until - 6:
            client = random.choice(self.clients[1:])
            if client.ws is None:
                continue
            client.probing = True
            watchers = self.observers(client.user_id)
            self.probes[(client.user_id, "offline")] = (time.time(), set(watchers))
            await client.ws.close()
            if client.reader:
                await client.reader
            client.ws = None
            await asyncio.sleep(2.5)
            self.probes.pop((client.user_id, "offline"), None)
            watchers = self.observers(client.user_id)
            self.probes[(client.user_id, "online")] = (time.time(), set(watchers))
            try:
                await self.open(client)
            except Exception as exc:  # a failed reconnect is a finding, not a crash of the whole run
                self.stats.errors.append(f"reconnect of user {client.user_id} failed: {exc!r}")
                self.probes.pop((client.user_id, "online"), None)
                continue
            client.probing = False
            await asyncio.sleep(2.5)
            self.probes.pop((client.user_id, "online"), None)

    async def run(self) -> int:
        a = self.args
        await self.prepare()
        try:
            t0 = time.perf_counter()
            for start in range(0, len(self.clients), 50):
                await asyncio.gather(*(self.open(c) for c in self.clients[start : start + 50]))
            print(f"{len(self.clients)} sockets open in {time.perf_counter() - t0:.1f}s", flush=True)
            await asyncio.sleep(1)
            until = time.monotonic() + a.duration
            tasks = [asyncio.create_task(self.sender(c, until)) for c in self.clients]
            tasks.append(asyncio.create_task(self.prober(until)))
            try:
                await asyncio.gather(*tasks)
            finally:  # never leave senders running into the cleanup below
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(3)  # drain
            self.stopping = True
            for c in self.clients:
                if c.ws is not None:
                    await c.ws.close()
            return self.report()
        finally:
            self.stopping = True
            if not a.keep:
                await self.cleanup()
            await engine.dispose()

    def report(self) -> int:
        s = self.stats

        def pct(values: list[float], p: float) -> float:
            if not values:
                return float("nan")
            if len(values) == 1:
                return values[0]
            return statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1]

        # A user reconnected by the prober is subscribed a moment before it counts as online again, so a
        # few deliveries can exceed the expectation; only a shortfall matters.
        lost = max(s.expected - s.received, 0)
        print(
            f"\nmessages: sent {s.sent}, acked {s.acks}; deliveries {s.received}/{s.expected} "
            f"(lost {lost}, duplicates {s.duplicates})"
        )

        def ms(values: list[float], p: float) -> str:
            return f"{pct(values, p) * 1000:.0f} ms"

        if s.delivery:
            print(
                f"delivery  p50 {ms(s.delivery, 50)}  p95 {ms(s.delivery, 95)}  p99 {ms(s.delivery, 99)}  "
                f"max {max(s.delivery) * 1000:.0f} ms  ({len(s.delivery)} deliveries)"
            )
        if s.ack:
            print(
                f"ack       p50 {ms(s.ack, 50)}  p95 {ms(s.ack, 95)}  p99 {ms(s.ack, 99)}  "
                f"max {max(s.ack) * 1000:.0f} ms  (send → ack: server processing)"
            )
        if s.presence:
            print(
                f"presence  p50 {ms(s.presence, 50)}  p95 {ms(s.presence, 95)}  "
                f"max {max(s.presence) * 1000:.0f} ms  ({len(s.presence)} observations)"
            )
        print(f"errors {len(s.errors)}  unexpected closes {s.unexpected_closes}")
        for e in s.errors[:5]:
            print("  ", e)
        failures = []
        if not s.delivery or pct(s.delivery, 95) >= DELIVERY_P95_LIMIT:
            failures.append(f"p95 delivery must be < {DELIVERY_P95_LIMIT} s")
        if not s.presence or pct(s.presence, 95) >= PRESENCE_P95_LIMIT:
            failures.append(f"p95 presence must be < {PRESENCE_P95_LIMIT} s")
        # Messages sent right as a receiver is being probed may legitimately miss it; allow that slack only.
        slack = max(2, s.sent // 100)
        if (
            lost > s.expected * 0.01
            or s.sent - s.acks > slack
            or s.errors
            or s.unexpected_closes
            or s.duplicates
        ):
            failures.append("lost deliveries, errors or unexpected closes")
        print("\nRESULT:", "PASS" if not failures else "FAIL — " + "; ".join(failures))
        return 0 if not failures else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--users", type=int, default=300, help="concurrent users (one socket each)")
    parser.add_argument("--rooms", type=int, default=20, help="rooms, including the big one")
    parser.add_argument("--big-room", type=int, default=1000, help="members of the big room")
    parser.add_argument("--duration", type=float, default=60, help="seconds of traffic")
    parser.add_argument("--rate", type=float, default=0.1, help="messages per user per second")
    parser.add_argument("--url", default="ws://localhost:8000", help="backend WebSocket base URL")
    parser.add_argument("--keep", action="store_true", help="do not delete the generated data")
    args = parser.parse_args()
    if args.rooms < 2:
        parser.error("--rooms must be at least 2")
    sys.exit(asyncio.run(LoadTest(args).run()))


if __name__ == "__main__":
    main()
