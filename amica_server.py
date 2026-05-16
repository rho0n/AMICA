"""
amica_server.py — AMICA FastAPI Server
Serves the phone UI and proxies chat requests to llama-server,
injecting memory context into every conversation.

Run: uvicorn amica_server:app --host 0.0.0.0 --port 5000
"""
import json
import asyncio
from pathlib import Path
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse, StreamingResponse, JSONResponse, HTMLResponse
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from memory_manager import (
    load_memory, build_system_prompt, add_note,
    save_memory, get_profile_name
)

LLAMA_URL = "http://127.0.0.1:8080"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="AMICA", docs_url=None, redoc_url=None)

# Mount static files (the phone UI)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Models ───────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    stream: bool = True


# ── Routes ───────────────────────────────────────────────────

@app.get("/", response_class=FileResponse)
async def root():
    """Serve the phone UI."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/profile")
async def get_profile():
    """Return basic profile info for the UI greeting."""
    memory = load_memory()
    return {
        "name": memory.get("profile", {}).get("name", "Friend"),
        "medication_count": len(memory.get("medications", [])),
        "people_count": len(memory.get("family_and_friends", [])),
        "event_count": len(memory.get("upcoming_events", [])),
    }


@app.get("/api/memory")
async def get_memory():
    """Return full memory for the memory viewer screen."""
    return load_memory()


@app.post("/api/memory/note")
async def post_note(request: Request):
    data = await request.json()
    add_note(data.get("content", ""))
    return {"ok": True}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """
    Main chat endpoint. Injects the memory system prompt, then
    forwards to llama-server. Streams the response back to the phone.
    """
    memory = load_memory()
    system_prompt = build_system_prompt(memory)

    # Build message list with injected system context
    llama_messages = [{"role": "system", "content": system_prompt}]
    for m in req.messages:
        llama_messages.append({"role": m.role, "content": m.content})

    payload = {
        "model": "gemma",  # llama-server ignores this but requires it
        "messages": llama_messages,
        "stream": req.stream,
        "max_tokens": 256,
        "temperature": 0.7,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
    }

    if req.stream:
        return StreamingResponse(
            _stream_llama(payload),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
    else:
        return await _complete_llama(payload)


async def _stream_llama(payload: dict) -> AsyncGenerator[str, None]:
    """Stream tokens from llama-server back to the phone as SSE."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{LLAMA_URL}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                yield f"data: {json.dumps({'error': error_body.decode()})}\n\n"
                return

            # aiter_bytes() avoids httpx's internal line-buffering so tokens
            # arrive at the client immediately rather than in one big flush.
            buf = ""
            async for raw in response.aiter_bytes():
                buf += raw.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if line == "data: [DONE]":
                        yield "data: [DONE]\n\n"
                        return
                    if line.startswith("data: "):
                        try:
                            chunk = json.loads(line[6:])
                            token = (
                                chunk.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content", "")
                            )
                            if token:
                                yield f"data: {json.dumps({'token': token})}\n\n"
                        except json.JSONDecodeError:
                            pass

    yield "data: [DONE]\n\n"


async def _complete_llama(payload: dict) -> JSONResponse:
    """Non-streaming completion."""
    payload["stream"] = False
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{LLAMA_URL}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        result = response.json()
        content = (
            result.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "Sorry, I couldn't respond right now.")
        )
        return JSONResponse({"response": content})


@app.get("/api/health")
async def health():
    """Check if llama-server is ready."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{LLAMA_URL}/health")
            llama_ok = r.status_code == 200
    except Exception:
        llama_ok = False
    return {
        "amica": "ok",
        "llama_server": "ready" if llama_ok else "loading",
        "user": get_profile_name(),
    }
