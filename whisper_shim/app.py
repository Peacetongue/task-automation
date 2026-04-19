"""OpenAI-compatible /v1/audio/transcriptions shim over COMPANY Transcribe.

COMPANY Transcribe (ml-platform-big.company.loc:9204) has an async API:
  POST /api/upload           → {"task_id": "..."}
  GET  /api/status/{task_id} → {"status": "pending|in_progress|done|failed", ...}
  GET  /api/result/{task_id} → {"segments": [{"text": "...", ...}], ...}

Hermes' STT tool speaks OpenAI's /v1/audio/transcriptions contract
(multipart `file`, returns `{"text": "..."}`). We bridge the two.
"""
from __future__ import annotations

import asyncio
import os
import socket
from typing import Optional

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

TRANSCRIBE_SERVICE_URL = os.getenv(
    "TRANSCRIBE_SERVICE_URL", "http://ml-platform-big.company.loc:9204"
).rstrip("/")
POLL_TIMEOUT = float(os.getenv("TRANSCRIBE_POLL_TIMEOUT", "120"))
POLL_INTERVAL = float(os.getenv("TRANSCRIBE_POLL_INTERVAL", "2"))
UPSTREAM_CONNECT_TIMEOUT = 10.0
UPSTREAM_READ_TIMEOUT = 30.0

app = FastAPI(title="whisper-shim", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict:
    # Unconditional OK: we intentionally do NOT ping the upstream company
    # host — on macOS dev without VPN, company.loc does not resolve, and we
    # still want the container's healthcheck to pass so dependents start.
    return {"ok": True}


def _join_segments(result: dict) -> str:
    segments = result.get("segments") or []
    if segments:
        return " ".join(
            (s.get("text") or "").strip() for s in segments if s.get("text")
        ).strip()
    # Fallback: some deployments return top-level text.
    return (result.get("text") or "").strip()


async def _upload(client: httpx.AsyncClient, audio: bytes, filename: str, content_type: Optional[str]) -> str:
    files = {"file": (filename or "audio.bin", audio, content_type or "application/octet-stream")}
    r = await client.post(f"{TRANSCRIBE_SERVICE_URL}/api/upload", files=files)
    r.raise_for_status()
    data = r.json()
    task_id = data.get("task_id") or data.get("id")
    if not task_id:
        raise HTTPException(status_code=502, detail=f"Upstream did not return task_id: {data}")
    return task_id


async def _poll(client: httpx.AsyncClient, task_id: str) -> None:
    deadline = asyncio.get_event_loop().time() + POLL_TIMEOUT
    while True:
        r = await client.get(f"{TRANSCRIBE_SERVICE_URL}/api/status/{task_id}")
        r.raise_for_status()
        status = (r.json().get("status") or "").lower()
        if status in ("done", "success", "completed", "finished"):
            return
        if status in ("failed", "error"):
            raise HTTPException(status_code=502, detail=f"Transcribe upstream failed for task {task_id}")
        if asyncio.get_event_loop().time() >= deadline:
            raise HTTPException(status_code=504, detail=f"Transcribe timed out after {POLL_TIMEOUT}s")
        await asyncio.sleep(POLL_INTERVAL)


async def _fetch_result(client: httpx.AsyncClient, task_id: str) -> dict:
    r = await client.get(f"{TRANSCRIBE_SERVICE_URL}/api/result/{task_id}")
    r.raise_for_status()
    return r.json()


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json"),
    temperature: Optional[float] = Form(None),
) -> JSONResponse:
    # We accept the OpenAI-shaped knobs (model/language/prompt/...) for
    # compatibility but COMPANY Transcribe doesn't expose them — discarded.
    del model, language, prompt, temperature

    audio = await file.read()
    timeout = httpx.Timeout(
        connect=UPSTREAM_CONNECT_TIMEOUT,
        read=UPSTREAM_READ_TIMEOUT,
        write=UPSTREAM_READ_TIMEOUT,
        pool=UPSTREAM_READ_TIMEOUT,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            task_id = await _upload(client, audio, file.filename or "audio.bin", file.content_type)
            await _poll(client, task_id)
            result = await _fetch_result(client, task_id)
    except (httpx.ConnectError, socket.gaierror) as e:
        # company.loc not resolvable (e.g. macOS dev outside VPN) or refused.
        raise HTTPException(status_code=503, detail=f"Transcribe unreachable: {e}") from e
    except httpx.HTTPStatusError as e:
        code = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=502, detail=f"Upstream {code}: {e}") from e
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail=f"Upstream timeout: {e}") from e

    text = _join_segments(result)
    if response_format == "text":
        return JSONResponse(content=text, media_type="text/plain")
    return JSONResponse(content={"text": text})
