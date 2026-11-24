import json
import logging

from app.core.logging import JsonFormatter, request_ctx, set_user
from app.ws.manager import ConnectionManager


def test_json_lines_carry_request_and_user() -> None:
    token = request_ctx.set({"request_id": "abc"})
    set_user(7)
    try:
        record = logging.makeLogRecord(
            {"name": "app.access", "levelno": 20, "levelname": "INFO", "msg": "request", "extra_field": 1}
        )
        record.status = 201
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_ctx.reset(token)
    assert line["msg"] == "request" and line["level"] == "INFO" and line["logger"] == "app.access"
    assert line["request_id"] == "abc" and line["user_id"] == 7 and line["status"] == 201
    assert "ts" in line


def test_metrics_snapshot_resets_the_latency_window() -> None:
    m = ConnectionManager()
    m.presence = {1: "online", 2: "AFK", 3: "online"}
    m.fanout_latencies.extend([0.001] * 95 + [0.5] * 5)
    m.frames_sent = 100
    snap = m.snapshot_metrics()
    assert snap == {
        "connections": 0,
        "users_online": 2,
        "users_afk": 1,
        "frames_sent": 100,
        "fanout_p95_ms": 500.0,
    }
    assert m.snapshot_metrics()["fanout_p95_ms"] is None
