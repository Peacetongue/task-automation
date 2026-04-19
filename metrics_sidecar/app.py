"""Prometheus metrics sidecar for the Hermes pilot stack.

Hermes itself doesn't expose /metrics — we derive counters/histograms by:
  1. Tailing /opt/data/logs/agent.log for "response ready" lines.
  2. Polling /opt/data/state.db (SQLite) for tool-call aggregates.

Corp monitoring uses VictoriaMetrics + Docker SD. This container advertises
itself with standard labels (see Dockerfile) and listens on :8000/metrics.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

LOG_PATH = Path(os.getenv("HERMES_LOG_PATH", "/hermes_data/logs/agent.log"))
STATE_DB = Path(os.getenv("HERMES_STATE_DB", "/hermes_data/state.db"))
POLL_INTERVAL = float(os.getenv("STATE_DB_POLL_INTERVAL", "15"))

# Separate registry so Python process metrics don't pollute output.
REG = CollectorRegistry()

MSGS_IN = Counter(
    "hermes_messages_total",
    "Inbound messages handled by Hermes",
    ["platform", "user_hash"],
    registry=REG,
)
RESPONSES = Counter(
    "hermes_responses_total",
    "Responses sent by Hermes",
    ["platform", "user_hash"],
    registry=REG,
)
API_CALLS = Counter(
    "hermes_api_calls_total",
    "LLM API calls attributed to a turn (from 'response ready' log line)",
    ["platform", "user_hash"],
    registry=REG,
)
LATENCY = Histogram(
    "hermes_response_latency_seconds",
    "End-to-end response latency per turn",
    ["platform"],
    buckets=(0.5, 1, 2, 4, 8, 16, 32, 64, 128),
    registry=REG,
)
RESP_CHARS = Histogram(
    "hermes_response_chars",
    "Response size in characters",
    ["platform"],
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000),
    registry=REG,
)
TOOL_INVOCATIONS = Counter(
    "hermes_tool_invocations_total",
    "Cumulative tool invocations observed in state.db",
    ["tool"],
    registry=REG,
)
ERRORS = Counter(
    "hermes_errors_total",
    "Error-level log lines",
    ["kind"],
    registry=REG,
)

# Regex for the structured log line Hermes writes on every completed turn:
#   gateway.run: response ready: platform=telegram chat=602736458 time=3.9s api_calls=2 response=309 chars
RESPONSE_READY_RE = re.compile(
    r"gateway\.run: response ready: "
    r"platform=(?P<platform>\S+) "
    r"chat=(?P<chat>\S+) "
    r"time=(?P<time>[\d.]+)s "
    r"api_calls=(?P<api_calls>\d+) "
    r"response=(?P<chars>\d+) chars"
)
INBOUND_RE = re.compile(
    r"gateway\.run: inbound message: platform=(?P<platform>\S+) "
    r"user=\S+ chat=(?P<chat>\S+) msg="
)
ERROR_LINE_RE = re.compile(r"\bERROR\b.*")

# In-memory set to not double-count the same tool row across poll iterations.
_seen_tool_row_ids: set[int] = set()

logger = logging.getLogger("metrics_sidecar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _hash_user(raw: str) -> str:
    """User IDs are PII-ish — we emit only a short hash for label cardinality control."""
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


async def tail_log() -> None:
    """Tail agent.log line by line, forever. Resilient to file rotation / truncation."""
    pos = 0
    while True:
        try:
            if not LOG_PATH.exists():
                await asyncio.sleep(2)
                continue
            size = LOG_PATH.stat().st_size
            if size < pos:                  # file rotated / truncated
                pos = 0
            with LOG_PATH.open("r", errors="replace") as fh:
                fh.seek(pos)
                chunk = fh.read()
                pos = fh.tell()
            for line in chunk.splitlines():
                _handle_log_line(line)
        except Exception as e:              # never die
            logger.warning("tail_log error: %s", e)
        await asyncio.sleep(1)


def _handle_log_line(line: str) -> None:
    m = RESPONSE_READY_RE.search(line)
    if m:
        platform = m["platform"]
        uh = _hash_user(m["chat"])
        RESPONSES.labels(platform=platform, user_hash=uh).inc()
        API_CALLS.labels(platform=platform, user_hash=uh).inc(int(m["api_calls"]))
        LATENCY.labels(platform=platform).observe(float(m["time"]))
        RESP_CHARS.labels(platform=platform).observe(int(m["chars"]))
        return
    m = INBOUND_RE.search(line)
    if m:
        MSGS_IN.labels(platform=m["platform"], user_hash=_hash_user(m["chat"])).inc()
        return
    if ERROR_LINE_RE.search(line):
        ERRORS.labels(kind="log").inc()


async def poll_state_db() -> None:
    """Every POLL_INTERVAL seconds, pull new tool-invocation rows from state.db."""
    while True:
        try:
            if STATE_DB.exists():
                con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=2)
                try:
                    cur = con.cursor()
                    # Hermes stores tool invocations in the `messages` table
                    # with non-null tool_name. Schema: id, session_id, role,
                    # content, tool_name, created, ... (see exploration).
                    cur.execute(
                        "SELECT id, tool_name FROM messages "
                        "WHERE tool_name IS NOT NULL AND tool_name != '' "
                        "ORDER BY id DESC LIMIT 500"
                    )
                    rows = cur.fetchall()
                finally:
                    con.close()
                new = [(rid, t) for rid, t in rows if rid not in _seen_tool_row_ids]
                for rid, tool in new:
                    TOOL_INVOCATIONS.labels(tool=tool).inc()
                    _seen_tool_row_ids.add(rid)
                # Cap the dedup set so it can't grow forever.
                if len(_seen_tool_row_ids) > 50_000:
                    keep = sorted(_seen_tool_row_ids)[-10_000:]
                    _seen_tool_row_ids.clear()
                    _seen_tool_row_ids.update(keep)
        except Exception as e:
            logger.warning("poll_state_db error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tail = asyncio.create_task(tail_log())
    poll = asyncio.create_task(poll_state_db())
    logger.info("metrics_sidecar ready: tailing %s, polling %s", LOG_PATH, STATE_DB)
    try:
        yield
    finally:
        tail.cancel()
        poll.cancel()


app = FastAPI(title="hermes-metrics", version="0.2.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "log_path_present": LOG_PATH.exists(),
        "state_db_present": STATE_DB.exists(),
    }


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(REG), media_type=CONTENT_TYPE_LATEST)
