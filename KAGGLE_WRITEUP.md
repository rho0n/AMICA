# AMICA: A Private AI Companion for the Elderly, Running Entirely on a $59 Board

**Subtitle:** Gemma 4 E2B running fully offline on a Qualcomm ARM SBC via llama.cpp — no cloud, no subscription, no data plan, any phone.

**Track:** llama.cpp Special Technology / Digital Equity & Inclusivity

---

## The Problem

Loneliness is one of the leading health risks for elderly adults. Studies consistently link social isolation to cognitive decline, depression, and early mortality. At the same time, many elderly people live with early-stage memory loss — forgetting medication schedules, upcoming visits from family, the names of new neighbours. They need a patient, low-pressure way to stay connected and stay organised.

Most AI solutions don't reach this population. Voice assistants require internet. Chatbot apps require app installs, accounts, subscriptions, and reliable data plans. Many elderly users in rural or lower-income settings have none of these. Their carers worry — rightly — about privacy: conversations about health, family, and daily life shouldn't be uploaded to a data centre.

AMICA (AI Memory & Intelligent Care Assistant) is built to serve exactly this group.

---

## What AMICA Is

AMICA is a conversational AI companion that:
- Runs **100% offline** on a device the size of a deck of cards
- Requires **no app install** — any phone browser works
- **Remembers** the user's family, medications, and upcoming events across conversations
- Is accessible to **any age, any device, any network situation**
- Costs under **$60 to build**

The user connects their phone to AMICA's private Wi-Fi hotspot ("AMICA", password "amica2024") and opens a browser. That's it. No accounts. No internet. No data ever leaves the room.

A family member or carer can use the companion family portal (`/family`) to pre-load information — the user's name, their medications, who their children are, upcoming appointments. AMICA then references all of this naturally in conversation without being asked.

---

## Hardware

The server is an **Arduino UNO Q 4GB** — a $59 single-board computer built around the Qualcomm Dragonwing QRB2210 (quad-core ARM Cortex-A53 @ 2GHz, 4GB LPDDR4, 32GB eMMC, Wi-Fi 5). It runs Debian Linux and hosts everything: the model, the API server, and the Wi-Fi hotspot simultaneously.

The model is **Gemma 4 E2B** (Q4_K_M quantisation, 3.22GB), served via **llama.cpp's llama-server**. This is a genuinely resource-constrained deployment — not a large cloud instance pretending to be embedded. The Cortex-A53 has no dotprod, no SVE, no i8mm. Inference runs on basic NEON SIMD and the model fits in RAM with ~780MB to spare.

---

## Architecture

```
Arduino UNO Q
  llama-server (port 8080, localhost only)
    Gemma 4 E2B Q4_K_M via llama.cpp
  amica_server.py (FastAPI, port 5000)
    memory.json — persistent user memory
    POST /api/chat/queue → job_id
    GET  /api/chat/stream/{job_id} → SSE token stream
    GET  /family → carer portal
  Wi-Fi hotspot → any phone browser
```

The Python API layer (FastAPI + uvicorn) sits between the phone and llama-server. It injects a personalised system prompt into every request, handles streaming, and manages memory persistence. Both services start automatically on boot via systemd — the device works out of the box with no SSH required.

---

## How Gemma 4 Is Used

Gemma 4 E2B handles two distinct tasks:

**1. Conversation.** Every chat message is sent to Gemma with a compact system prompt (~180 tokens) containing the user's profile, up to 10 people, up to 5 upcoming events, and current medications. Gemma's instruction-following is strong enough to maintain a warm, patient persona and consistently refer to the user as "you" rather than their name — a small but important detail for elderly users who find being addressed formally condescending.

**2. Memory extraction.** When a user says something worth remembering ("remind me to meet Karl tomorrow at 6pm", "I met my new neighbour Stacey"), AMICA needs to detect and save it. We use a three-layer cascade:

- **Layer 1 — [MEM:] tag:** The system prompt instructs Gemma to end its reply with a structured tag when it detects a save intent. Example: `[MEM:person|Stacey|friend|likes gardening]` or `[MEM:event|2026-05-19|Karl visiting at 6pm]`. This is the most reliable path — LLM reasoning outperforms regex on messy natural language.
- **Layer 2 — Regex:** A two-pass regex system catches saves that don't produce a valid tag. Pass 1 runs structural patterns on the whole message (no trigger word needed), which handles typos like "rememeber". Pass 2 uses explicit trigger detection.
- **Layer 3 — Background LLM:** If both layers miss, a compact JSON extraction prompt fires 3 seconds later on the idle llama-server.

One critical Gemma-specific discovery: **Gemma 4E defaults to thinking/reasoning mode**, spending 100-200 tokens on `reasoning_content` before producing any visible output. On hardware doing 349ms/token, this adds 60-120 silent seconds. The fix is `--reasoning off` on llama-server — a flag that must be set explicitly. This alone made the difference between unusable and deployable.

---

## Technical Challenges

**iOS Safari streaming.** The phone UI must stream tokens incrementally so the user sees a live response rather than waiting for the full reply. iOS Safari doesn't support incremental `fetch` ReadableStream or XHR `onprogress`. The solution is the native `EventSource` API (GET-based SSE). The client POSTs to `/api/chat/queue`, gets a job_id in under 1 second, then opens `new EventSource('/api/chat/stream/{job_id}')`. Tokens arrive word by word. SSE keepalive comments (`: keepalive`) are sent every 15 seconds during the silent prompt-processing phase to prevent the connection dropping.

**KV cache warmup latency.** With a 180-token system prompt and 349ms/token processing, every conversation starts with a 63-second wait before the first token appears. We addressed this two ways. First, we capped `max_tokens` at 35 (AMICA's replies average 15-25 tokens), cutting generation from ~51s to ~14s and total response time from ~115s to ~77s. Second, we implemented KV cache persistence: after warmup, slot 0 is saved to disk alongside a SHA256 hash of the system prompt. On the next restart, if the hash matches, the cache is restored instantly — same-day restarts skip the 63-second warmup entirely. Third, any family portal save triggers an immediate background re-warmup, so by the time a carer finishes explaining the portal and the user opens chat, it's already warm.

**GPU acceleration attempts.** We attempted Vulkan GPU offload via the Adreno 702 GPU and the Mesa Turnip driver. After patching a conservative shared-memory check in `ggml-vulkan.cpp` (which incorrectly rejected Q4_K_M despite its 4224-byte requirement being well within the 16384-byte limit), the binary loaded successfully. However, the Turnip driver throws `vk::DeviceLostError` on every inference request with a real system prompt. The crash is a GPU timeout — the compute shader runs too long on this specific Adreno 702. This is a known Turnip stability limitation, not a llama.cpp bug. We fell back to CPU-only, which is stable and predictable.

---

## Why These Choices Were Right

**llama.cpp over alternatives.** llama.cpp is the only framework that runs a quantised Gemma model on a 4-core ARM Cortex-A53 without requiring GPU, cloud, or specialist hardware. Ollama has more overhead. ONNX Runtime GenAI required 9 separate config files for this model. MediaPipe crashed with GPU assertion failures. llama.cpp's server mode gives us a clean HTTP API, streaming support, and slot-based KV cache management — all essential here.

**Browser UI over native app.** Any phone, any OS, zero install friction. For an elderly user being handed a device for the first time, this matters enormously. The entire UI is a single HTML file with no framework dependencies — it loads instantly even over the local hotspot.

**memory.json over a database.** Plain JSON is human-readable, directly editable by a carer, and requires no migrations or dependencies. The family portal is essentially a structured editor for this file.

**Offline-first as a feature, not a constraint.** AMICA's privacy guarantee — no data ever leaves the room — is what makes it appropriate for elderly care. A cloud-connected alternative would be faster, but it would also require consent, data governance, and infrastructure costs that price it out of reach for the target population.

---

## Impact

AMICA demonstrates that meaningful AI assistance for vulnerable populations doesn't require expensive hardware or internet connectivity. A $59 ARM board, an open-weight model, and a standard phone browser are sufficient to deliver a private, persistent, genuinely useful companion. The same architecture could run on any Linux SBC — Raspberry Pi 4, Orange Pi, Rock 5 — making it reproducible at scale. The total cost of materials for a deployed unit is under $80 including power supply and SD card.

The core thesis: **privacy and accessibility are not in tension with capability**. You don't need a data centre to give someone's grandmother a companion who remembers her medication schedule and asks how her son Karl's visit went.
