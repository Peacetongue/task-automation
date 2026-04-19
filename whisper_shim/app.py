"""OpenAI-compatible /v1/audio/transcriptions shim over COMPANY Transcribe.

COMPANY Transcribe (ml-platform-big.company.loc:9204) already exposes an
OpenAI-shaped endpoint at /api/v1/audio/transcriptions, BUT:

  - its default `stream=true` returns an SSE-like stream of
    `transcript.text.delta` / `transcript.text.done` events, which Hermes'
    STT client does NOT understand;
  - `stream=false` returns the plain `{"text": "..."}` that OpenAI clients
    expect.

Hermes' client has no way to set `stream=false` on its side, so this shim
sits in the middle, injects that flag, and otherwise forwards the request
verbatim.
"""
from __future__ import annotations

import os
import socket
from typing import Optional

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

UPSTREAM = os.getenv(
    "TRANSCRIBE_SERVICE_URL",
    "http://ml-platform-big.company.loc:9204",
).rstrip("/") + "/api/v1/audio/transcriptions"

UPSTREAM_CONNECT_TIMEOUT = 10.0
UPSTREAM_READ_TIMEOUT = float(os.getenv("TRANSCRIBE_POLL_TIMEOUT", "120"))

app = FastAPI(title="whisper-shim", version="0.3.0")


@app.get("/healthz")
async def healthz() -> dict:
    # Unconditional OK so docker healthcheck passes even when company.loc
    # isn't resolvable (macOS dev outside VPN).
    return {"ok": True}


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form("ru"),
    prompt: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json"),
    temperature: Optional[float] = Form(None),
) -> JSONResponse:
    del prompt, temperature  # upstream doesn't use them

    audio = await file.read()
    timeout = httpx.Timeout(
        connect=UPSTREAM_CONNECT_TIMEOUT,
        read=UPSTREAM_READ_TIMEOUT,
        write=UPSTREAM_READ_TIMEOUT,
        pool=UPSTREAM_READ_TIMEOUT,
    )
    files = {"file": (file.filename or "audio.bin", audio, file.content_type or "application/octet-stream")}
    data = {
        # ↓ The whole reason this shim exists: force non-streaming.
        "stream": "false",
        "response_format": response_format or "json",
        "chunking_strategy": "auto",
    }
    if model:
        data["model"] = model
    if language:
        data["language"] = language

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(UPSTREAM, files=files, data=data)
    except (httpx.ConnectError, socket.gaierror) as e:
        raise HTTPException(status_code=503, detail=f"Transcribe unreachable: {e}") from e
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail=f"Upstream timeout: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream {r.status_code}: {r.text[:300]}",
        )

    ctype = r.headers.get("content-type", "")
    if "application/json" in ctype:
        body = r.json()
        # Already in OpenAI shape if the service honored stream=false.
        if isinstance(body, dict) and "text" in body:
            return JSONResponse({"text": body["text"]})
        # Just in case it still streams: find the last 'done' event payload.
    # Fall-back parser: upstream returned SSE/ndjson even with stream=false.
    text = _collapse_stream(r.text)
    return JSONResponse({"text": text})


def _collapse_stream(raw: str) -> str:
    """Best-effort: walk the SSE-like payload and collect the final text.

    Events look like:
      {"type":"transcript.text.delta","payload":"{\\"delta\\":\\"...\\",...}"}
      {"type":"transcript.text.done","payload":"{\\"text\\":\\"...\\",...}"}
    """
    import json
    done_text: Optional[str] = None
    deltas: list[str] = []
    for chunk in raw.split("}{"):
        # Naive re-split: braces are unbalanced if multiple JSONs stuck together.
        if not chunk.strip():
            continue
        s = chunk
        if not s.startswith("{"):
            s = "{" + s
        if not s.endswith("}"):
            s = s + "}"
        try:
            obj = json.loads(s)
        except Exception:
            continue
        payload = obj.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        if not isinstance(payload, dict):
            continue
        if obj.get("type") == "transcript.text.done" and "text" in payload:
            done_text = payload["text"]
        elif obj.get("type") == "transcript.text.delta" and "delta" in payload:
            deltas.append(payload["delta"])
    return (done_text or "".join(deltas)).strip()
