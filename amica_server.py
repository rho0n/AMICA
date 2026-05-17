"""
amica_server.py — AMICA FastAPI Server
Serves the phone UI and proxies chat requests to llama-server,
injecting memory context into every conversation.

Run: uvicorn amica_server:app --host 0.0.0.0 --port 5000
"""
import json
import re
import uuid
import asyncio
import logging
from pathlib import Path
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse, StreamingResponse, JSONResponse, HTMLResponse
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger("amica")

from memory_manager import (
    load_memory, build_system_prompt, get_profile_name,
    add_medication, remove_medication, add_person, remove_person,
    add_event, remove_event, update_profile, clean_old_events,
    parse_and_save_explicit_memory, merge_extracted_facts, parse_mem_tag,
)

LLAMA_URL = "http://127.0.0.1:8080"
STATIC_DIR = Path(__file__).parent / "static"
LLAMA_TIMEOUT = 125  # seconds — 180-token prompt ≈ 63s + 90-token reply ≈ 51s = ~114s; 125s gives headroom
MAX_HISTORY_TURNS = 4  # keep last N user+assistant pairs to prevent context overflow

app = FastAPI(title="AMICA", docs_url=None, redoc_url=None)

# In-memory job store for EventSource streaming: job_id -> (queue, task)
_jobs: dict[str, tuple] = {}

# Tracks when the current llama-server request started (0 = idle)
_llama_busy_since: float = 0.0


async def _cancel_llama_slot() -> None:
    """Ask llama-server to cancel its active slot. Best-effort — silently ignored if unavailable."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{LLAMA_URL}/slots")
            if r.status_code != 200:
                return
            for slot in r.json():
                if slot.get("state") == 1:  # 1 = processing
                    await client.post(
                        f"{LLAMA_URL}/slots/{slot['id']}",
                        json={"action": "cancel"},
                    )
    except Exception:
        pass


@app.on_event("startup")
async def startup():
    clean_old_events(days=30)

# Mount static files (the phone UI)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Models ───────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    stream: bool = True
    client_time: str = ""  # ISO string from the phone's clock


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


@app.get("/family", response_class=FileResponse)
async def family_portal():
    return FileResponse(str(STATIC_DIR / "family.html"))


# ── Memory CRUD (family portal) ───────────────────────────

@app.post("/api/memory/profile")
async def api_update_profile(request: Request):
    d = await request.json()
    update_profile(name=d.get("name", ""), notes=d.get("notes"))
    return {"ok": True}

@app.post("/api/memory/medication")
async def api_add_medication(request: Request):
    d = await request.json()
    add_medication(d.get("name", ""), d.get("dose", ""), d.get("time", ""))
    return {"ok": True}

@app.delete("/api/memory/medication/{name}")
async def api_remove_medication(name: str):
    remove_medication(name)
    return {"ok": True}

@app.post("/api/memory/person")
async def api_add_person(request: Request):
    d = await request.json()
    add_person(d.get("name", ""), d.get("relation", ""), d.get("traits", ""))
    return {"ok": True}

@app.delete("/api/memory/person/{name}")
async def api_remove_person(name: str):
    remove_person(name)
    return {"ok": True}

@app.post("/api/memory/event")
async def api_add_event(request: Request):
    d = await request.json()
    add_event(d.get("date", ""), d.get("description", ""))
    return {"ok": True}

@app.delete("/api/memory/event/{index}")
async def api_remove_event(index: int):
    remove_event(index)
    return {"ok": True}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """
    Main chat endpoint. Injects the memory system prompt, then
    forwards to llama-server. Streams the response back to the phone.
    """
    memory = load_memory()
    system_prompt = build_system_prompt(memory, client_time=req.client_time)

    # Build message list with injected system context
    llama_messages = [{"role": "system", "content": system_prompt}]
    for m in req.messages[-(MAX_HISTORY_TURNS * 2):]:
        llama_messages.append({"role": m.role, "content": m.content})

    payload = {
        "model": "gemma",  # llama-server ignores this but requires it
        "messages": llama_messages,
        "stream": req.stream,
        "max_tokens": 90,
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


@app.post("/api/chat/queue")
async def chat_queue(req: ChatRequest):
    """Queue a chat job and return a job_id immediately.
    Client then connects to /api/chat/stream/{job_id} via EventSource.
    """
    # If a previous request has been running longer than the timeout, cancel it now
    # so this new request isn't queued behind a permanently stuck one.
    if _llama_busy_since and (asyncio.get_running_loop().time() - _llama_busy_since) > LLAMA_TIMEOUT:
        await _cancel_llama_slot()

    memory = load_memory()
    system_prompt = build_system_prompt(memory, client_time=req.client_time)
    llama_messages = [{"role": "system", "content": system_prompt}]
    # Trim history to last N turns so context never overflows the 2048-token window
    messages = req.messages[-(MAX_HISTORY_TURNS * 2):]
    for m in messages:
        llama_messages.append({"role": m.role, "content": m.content})
    payload = {
        "model": "gemma",
        "messages": llama_messages,
        "stream": True,
        "max_tokens": 90,
        "temperature": 0.7,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
    }
    last_user = next(
        (m.content for m in reversed(req.messages) if m.role == "user"), ""
    )
    job_id = uuid.uuid4().hex[:8]
    queue: asyncio.Queue = asyncio.Queue()
    # Store payload only — task starts when EventSource connects, avoiding race condition
    _jobs[job_id] = (queue, payload, last_user, req.client_time)
    return JSONResponse({"job_id": job_id})


@app.get("/api/chat/stream/{job_id}")
async def chat_stream_es(job_id: str):
    """EventSource endpoint — streams tokens for a queued job.
    Uses GET so iOS Safari's native EventSource API works correctly.
    Task is started HERE (not at queue time) to avoid a race condition where
    inference finishes before the client connects and tokens are lost.
    """
    async def generate():
        entry = _jobs.get(job_id)
        if not entry:
            yield f"data: {json.dumps({'error': 'job not found'})}\n\n"
            return
        queue, payload, last_user, client_time = entry
        # Start inference now that we are listening — no tokens can be missed
        task = asyncio.create_task(
            _run_stream_job(job_id, payload, last_user, client_time, queue)
        )
        _jobs[job_id] = (queue, task)
        # Flush headers immediately so the connection is confirmed live before
        # the 25-second prompt-processing wait begins.
        yield ": keepalive\n\n"
        try:
            while True:
                try:
                    token = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Send keepalive during prompt processing to prevent
                    # browser/client from dropping the connection.
                    yield ": keepalive\n\n"
                    continue
                if token is None:
                    yield "data: [DONE]\n\n"
                    return
                yield f"data: {json.dumps({'token': token})}\n\n"
        except Exception:
            yield "data: [DONE]\n\n"
        finally:
            _jobs.pop(job_id, None)
            task.cancel()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _llm_extract_memory(user_msg: str, client_time: str) -> None:
    """Background LLM call to extract facts when regex parsing finds nothing.
    Waits 3 s then checks llama is idle before firing, so it never collides with chat.
    """
    await asyncio.sleep(3)
    if _llama_busy_since:
        return  # new chat already started — skip extraction this turn
    try:
        from datetime import datetime
        try:
            now = datetime.fromisoformat(client_time) if client_time else datetime.now()
        except ValueError:
            now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        tomorrow = (now.replace(hour=0, minute=0, second=0) +
                    __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
        prompt = (
            f"Today is {today}. Tomorrow is {tomorrow}.\n"
            "Extract any new people, medications, or upcoming events from the message below.\n"
            "Output JSON only — no other text.\n"
            'Format: {"people":[{"name":"NAME","relation":"RELATION","traits":""}],'
            '"medications":[{"name":"NAME","dose":"","time":""}],'
            '"events":[{"date":"YYYY-MM-DD or empty","description":"DESC"}]}\n'
            "Only include facts explicitly mentioned. Output {} if nothing to extract.\n\n"
            'Message: "I met a new friend Stacey who likes gardening"\n'
            '{"people":[{"name":"Stacey","relation":"friend","traits":"likes gardening"}],'
            '"medications":[],"events":[]}\n\n'
            f'Message: "{user_msg}"\n'
        )
        payload = {
            "model": "gemma",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": 90,
            "temperature": 0.1,
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(f"{LLAMA_URL}/v1/chat/completions", json=payload)
            if r.status_code != 200:
                return
            content = (
                r.json().get("choices", [{}])[0]
                .get("message", {}).get("content", "")
            )
            m = re.search(r'\{.*\}', content, re.DOTALL)
            if not m:
                return
            facts = json.loads(m.group())
            if facts:
                merge_extracted_facts(facts)
                log.info("LLM extraction saved: %s", facts)
    except Exception as exc:
        log.debug("LLM extraction failed: %s", exc)


async def _run_stream_job(job_id: str, payload: dict, user_msg: str, client_time: str, queue: asyncio.Queue):
    """Background task: runs inference and pushes tokens into the job queue."""
    global _llama_busy_since
    full_response = ""
    _llama_busy_since = asyncio.get_running_loop().time()
    timed_out = False
    try:
        async with asyncio.timeout(LLAMA_TIMEOUT):
            async with httpx.AsyncClient(timeout=LLAMA_TIMEOUT) as client:
                async with client.stream(
                    "POST", f"{LLAMA_URL}/v1/chat/completions",
                    json=payload, headers={"Content-Type": "application/json"},
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        log.error("llama-server %d: %s", response.status_code, body[:200])
                        return
                    buf = ""
                    async for raw in response.aiter_bytes():
                        buf += raw.decode("utf-8", errors="replace")
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            if line == "data: [DONE]":
                                await queue.put(None)
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
                                        full_response += token
                                        await queue.put(token)
                                except json.JSONDecodeError:
                                    pass
    except TimeoutError:
        timed_out = True
        log.warning("llama-server timed out after %ds", LLAMA_TIMEOUT)
    except asyncio.CancelledError:
        pass  # client disconnected — normal
    except Exception as exc:
        log.error("_run_stream_job error: %s", exc)
    finally:
        _llama_busy_since = 0.0
        await queue.put(None)

    if timed_out:
        # Cancel the stuck llama-server slot so the next request isn't queued behind it
        asyncio.create_task(_cancel_llama_slot())

    if user_msg:
        # Layer 1 (most reliable): parse [MEM:...] tag from AMICA's own response
        tag_saved = parse_mem_tag(full_response, client_time)
        if not tag_saved:
            # Layer 2: regex patterns on user message
            regex_saved = parse_and_save_explicit_memory(user_msg, client_time)
            if not regex_saved:
                # Layer 3: background LLM extraction as last resort
                asyncio.create_task(_llm_extract_memory(user_msg, client_time))




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


@app.get("/api/debug/prompt")
async def debug_prompt():
    """Returns the exact system prompt currently sent to the model. For verification only."""
    memory = load_memory()
    prompt = build_system_prompt(memory)
    return {"prompt": prompt, "char_count": len(prompt), "memory_keys": list(memory.keys())}


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
