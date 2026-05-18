# AMICA — Project Context for Claude Code

> This file is read automatically by Claude Code on every session.
> It contains the full project history, decisions, and current status.

---

## What Is This Project?

**AMICA** (AI Memory & Intelligent Care Assistant) is a 100% offline, privacy-first conversational companion for elderly and vulnerable adults. It runs **Gemma 4 E2B** locally on an **Arduino UNO Q 4GB** via `llama.cpp`. Any phone connects to AMICA's private Wi-Fi hotspot and opens a browser — no app install, no internet, no data ever leaves the room.

**Hackathon:** Gemma 4 Good Hackathon on Kaggle (Google DeepMind)
**Deadline:** May 19, 2026 at 12:59 AM GMT+1
**URL:** https://kaggle.com/competitions/gemma-4-good-hackathon

### Target Prize Tracks
| Track | Prize | Why We Fit |
|---|---|---|
| **llama.cpp Special Technology** | $10,000 | Gemma 4 E2B on a $59 ARM64 SBC (Qualcomm QRB2210) via llama.cpp — genuinely resource-constrained |
| **Digital Equity & Inclusivity** (Impact) | $10,000 | Elderly users, zero data plan, any phone, no app install needed |
| **Main Track** | up to $50,000 | Compelling real-world impact story — loneliness + memory loss in elderly adults |

---

## Hardware

| Component | Details |
|---|---|
| **Server device** | Arduino UNO Q 4GB — Qualcomm Dragonwing QRB2210 (quad-core ARM Cortex-A53 @ 2GHz, Adreno GPU), Debian Linux (Trixie), 4GB LPDDR4 RAM, 32GB eMMC, Wi-Fi 5, BT 5.1. ~$59. Hostname: Beepboop |
| **Model** | Gemma 4 E2B — bartowski/google_gemma-4-E2B-it-GGUF, file google_gemma-4-E2B-it-Q4_K_M.gguf (3.22GB actual). Apache 2.0 — no token needed. |
| **Inference engine** | llama.cpp (llama-server), ARM64 CPU-only. Vulkan attempted — Adreno 702 Turnip driver crashes on every real inference (see graveyard). |
| **Demo phone** | iPhone 13. Connects to UNO Q over Wi-Fi, accesses browser UI. |
| **Client** | Any phone browser — no app, no install required |

### Actual Performance (measured on device)
- Prompt processing: ~349ms/token
- Generation: ~400-565ms/token (~1.77–2.5 tok/s)
- System prompt ~180 tokens → ~63s prompt processing before first token
- Typical total response time: 63s (prompt) + ~14s (generation) = **~77s** with `max_tokens: 35`
- `max_tokens: 35` — caps generation at ~14s (reduced from 90/51s — average AMICA reply is 15-25 tokens)
- CPU features: fp asimd evtstrm aes pmull sha1 sha2 crc32 cpuid — NO asimddp, no SVE, no i8mm
- RAM at idle: 593MB used, 3.0GB available, 1.8GB swap
- Generation speed varies noticeably — the Cortex-A53 frequency-scales up after load starts. First response of a session is slower.
- **KV cache persistence:** after first warmup, same-day restarts restore instantly from disk (warmup.bin + warmup.hash)

### SSH Access
- User: `arduino`, password: `password`
- Use `sshpass -e` with `SSHPASS=password` env var for non-interactive scripts
- **Never use `pkill -f <string>` over SSH if `<string>` appears in the SSH command itself** — it kills the SSH session. Use `fuser -k 8080/tcp` to kill by port instead.
- Kill llama-server: `echo 'password' | sudo -S fuser -k 8080/tcp`
- Kill uvicorn: `pkill -f uvicorn` is safe
- **Testing SSE over SSH**: do NOT pipe curl output directly — the SSH pseudo-TTY swallows SSE newlines. Always redirect: `curl ... > /tmp/out.txt; cat /tmp/out.txt`

---

## Architecture

```
Arduino UNO Q 4GB (Debian Linux)
  llama-server (port 8080, localhost only)
    /home/arduino/amica/models/google_gemma-4-E2B-it-Q4_K_M.gguf
  amica_server.py (FastAPI, port 5000)
    ~/amica/venv (Python virtualenv)
    Reads memory.json, builds system prompt
    POST /api/chat/queue  → starts inference background task, returns job_id
    GET  /api/chat/stream/{job_id} → EventSource SSE stream (iOS Safari compatible)
    POST /api/chat  → legacy non-streaming fallback
    GET  /family    → family/carer portal (edit memory, people, meds, events)
    Serves static/index.html to phones
  Wi-Fi hotspot: "AMICA" / "amica2024", board at 10.42.0.1
  Development access: 192.168.1.156 on home Wi-Fi (IP may change)
```

### Streaming architecture (EventSource pattern)
iOS Safari does not support incremental `fetch` ReadableStream or XHR `onprogress`.
The working solution uses the native `EventSource` API (GET-based SSE):
1. Client POSTs `/api/chat/queue` → gets `job_id` back in <1s
2. Client opens `new EventSource('/api/chat/stream/{job_id}')` — GET request
3. Server streams tokens as they arrive from llama-server
4. Client appends tokens to a live bubble — word-by-word visual effect
5. SSE keepalive comments (`: keepalive\n\n`) sent every 15s to prevent connection drop during the ~63s silent prompt-processing phase

### Memory save architecture (three-layer cascade)
After every chat turn, facts are saved via three independent layers — any one is sufficient:

**Layer 1 — [MEM:] tag (most reliable):** The system prompt instructs Gemma to end its reply with a structured tag when it detects a save intent. Server parses the tag from `full_response` before it reaches the client. Client strips the tag from displayed text using regex. Example: `[MEM:person|Stacey|friend|likes gardening]`

**Layer 2 — Regex on user message (fast fallback):** `parse_and_save_explicit_memory()` in memory_manager.py runs in two passes:
- Pass 1: `_PERSON_RE`, `_MET_PERSON_RE`, `_MED_RE` on whole message — **no trigger word needed**. This catches misspelled triggers ("rememeber"). `_PERSON_RE` requires known relation word + Title Case name in Pass 1 to avoid false positives.
- Pass 2: `_EXPLICIT_TRIGGERS` regex → extract fact → run all extractors (med, person, event, profile note).

**Layer 3 — Background LLM extraction (last resort):** If layers 1 and 2 both miss, an async task fires 3s later (only if llama-server is idle) with a compact JSON extraction prompt. Result merged via `merge_extracted_facts()`.

### Memory tag format
`[MEM:person|NAME|RELATION|TRAITS]` — e.g. `[MEM:person|Stacey|friend|likes gardening]`
`[MEM:event|YYYY-MM-DD|DESCRIPTION]` — e.g. `[MEM:event|2026-05-18|Karl visiting at 6pm]`
`[MEM:med|NAME|DOSE|TIME]` — e.g. `[MEM:med|Metformin|500mg|morning]`

### System prompt token budget
The system prompt is built fresh every turn by `build_system_prompt()` in memory_manager.py.
Target: ~180 tokens → ~63s prompt processing + ~51s generation = ~114s, within LLAMA_TIMEOUT=125s.
Client hard fallback: 140s (raised from 90s to match).

Contents (compact format):
- Line 1: identity + today's date + "say you/your, not name" + "2-3 sentences"
- Line 2: "About [name]:" + profile notes (70-char truncated)
- Meds: name + dose + time keyword only (e.g. "morning" not "one tablet with breakfast")
- People: most-recently-updated first, up to 10, name + relation (15 chars) + traits (25 chars)
- Events: sorted by date, up to 5 upcoming, 40-char description + relative label (today/tomorrow/in Nd)
- MEM tag instruction: tells Gemma the tag format + today/tomorrow dates

---

## Actual File Locations on the UNO Q

| Thing | Path |
|---|---|
| llama.cpp source + build | /root/llama.cpp/ (cloned as sudo — always use sudo) |
| llama-server binary | /root/llama.cpp/build/bin/llama-server |
| AMICA Python app | /home/arduino/amica/ |
| Python virtualenv | /home/arduino/amica/venv/ |
| Model | /home/arduino/amica/models/google_gemma-4-E2B-it-Q4_K_M.gguf |
| Memory file | /home/arduino/amica/memory.json |
| Phone UI | /home/arduino/amica/static/index.html |
| Family portal | /home/arduino/amica/static/family.html |

---

## How to Start Everything Manually

### Terminal 1 — llama-server
```bash
sudo /root/llama.cpp/build/bin/llama-server \
  -m ~/amica/models/google_gemma-4-E2B-it-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8080 \
  --ctx-size 2048 \
  --threads 4 \
  --n-predict 256 \
  --reasoning off
```
Wait for: server is listening on http://127.0.0.1:8080

**NOTE: `--reasoning off` is required.** Gemma 4E defaults to thinking/reasoning mode, emitting
all tokens as `delta.reasoning_content` before any `delta.content`. Without this flag, the model
can spend 100-200 tokens (60-120 seconds) in a silent thinking phase before the first visible word.

### Terminal 2 — Python API
```bash
cd ~/amica
source venv/bin/activate
uvicorn amica_server:app --host 0.0.0.0 --port 5000
```

### Health checks
```bash
curl http://localhost:8080/health
curl http://localhost:5000/api/health
```

### Auto-start (systemd)
Services start automatically on boot with no SSH needed.
```bash
sudo systemctl status amica-llama amica-api
sudo journalctl -u amica-llama -f
sudo journalctl -u amica-api -f
```

### Phone access — home Wi-Fi mode (development)
Phone on same home Wi-Fi → browser → `http://192.168.1.156:5000`

### Phone access — hotspot mode (demo / fully offline)
```
Phone: connect to Wi-Fi "AMICA", password "amica2024"
Browser: http://10.42.0.1:5000
Family portal: http://10.42.0.1:5000/family
```

To switch back to home WiFi temporarily (for deploying updates):
- Connect Mac to "AMICA" WiFi → `ssh arduino@10.42.0.1` → `bash ~/amica/hotspot-off.sh`

### Deploying updates
```bash
bash deploy.sh   # tries 192.168.1.156 then 10.42.0.1
```
deploy.sh copies index.html, memory_manager.py, amica_server.py and restarts the service.

---

## Current Status

### WORKING (as of 2026-05-18) — MVP complete + performance improvements
- llama-server running Gemma 4 E2B on UNO Q CPU with `--reasoning off`
- Token-by-token streaming on iOS Safari via EventSource queue/stream pattern
- Phone UI confirmed working on home Wi-Fi and AMICA hotspot
- Systemd auto-start — works out of the box at boot, no SSH needed
- Wi-Fi hotspot "AMICA" / "amica2024", board at 10.42.0.1
- **Memory saving from chat — confirmed working end-to-end:**
  - Three-layer cascade: [MEM:] tag → regex → background LLM
  - New people, medications, events saved reliably from natural chat
  - Traits saved (e.g. "likes gardening") and recalled in subsequent chats
  - Typo-tolerant ("rememeber", "tomorrwo") — Pass 1 runs before trigger check
  - [MEM:] tag stripped from displayed text (complete and partial tags during streaming)
- **Family portal (/family) — confirmed working:**
  - Add/edit/delete people, medications, events, profile
  - People sorted by most-recently-updated so new additions always show in system prompt (up to 10)
  - Portal saves appear in AMICA's next chat immediately (load_memory() reads fresh every turn)
- Memory recall in new chats — all saved facts appear in system prompt
- AMICA always says "you/your" — never uses the user's name in third person
- Profile notes: only saved when there's an explicit trigger word (prevents casual "I" statements polluting profile)
- **KV cache persistence** — warmup.bin saved after first start; warmup.hash checked on restart. Same-day restarts are instant if memory hasn't changed.
- **Session system prompt snapshot** — `_session_system_prompt` frozen at warmup start; reused for whole session so mid-session memory saves don't invalidate the KV cache.
- **Background re-warmup on portal saves** — any family portal save (person/med/event/profile) triggers `_trigger_background_warmup()`. By the time user navigates from portal to chat, warmup is done.
- `max_tokens: 35` — generation capped at ~14s (down from ~51s). AMICA's responses average 15-25 tokens so nothing is lost.

### KNOWN ISSUES / ACCEPTED TRADE-OFFS
- **~63-77s total response time** — 63s prompt processing + ~14s generation. UI shows animated dots + "Still thinking…" / "Almost there…" messages. Accepted for demo.
- **Variable generation speed** — Cortex-A53 frequency-scales after load starts. First response of a session is noticeably slower. Expected hardware behaviour.
- **Google Fonts CDN in index.html** — falls back to Georgia when offline. Cosmetic only.
- **In-chat editing/deleting not supported** — chat can ADD to memory but not remove. Use /family portal to correct mistakes. This is intentional (safe design for elderly users).
- **Profile notes can accumulate garbage** — if old code ran before the has_trigger gate was added. Fix: manually edit "About them" in /family portal to remove any junk sentences.

---

## What Didn't Work (Graveyard — Do Not Revisit)

| Approach | Why Abandoned |
|---|---|
| MediaPipeTasksGenAI + .litertlm | RET_CHECK GPU crashes, wrong compression, no manifest. Hours wasted. |
| ONNX Runtime genai-objc | 9 config files, custom tokenisation — too much for hackathon timeline. |
| Running model on iPhone 13 | Not enough RAM/performance. |
| Vulkan GPU acceleration (-ngl 10/4/1) | Adreno 702 Turnip driver throws `vk::DeviceLostError` on every real inference. Works with tiny prompts but crashes on the full 180-token system prompt. `VK_ICD_FILENAMES=""` does not disable Vulkan — loader still finds ICDs via default paths. Fixed by renaming `/usr/share/vulkan/icd.d/freedreno_icd.json` to `.bak`. Do not retry GPU acceleration. |
| OpenBLAS BLAS backend | Installed libopenblas-dev, rebuilt with `-DGGML_BLAS=ON`. Made prompt processing SLOWER (90s vs 63s) because GGML Q4_K native kernels are faster than dequantize-to-float32 + BLAS on Cortex-A53 without dotprod. Rebuilt without BLAS. |
| VK_ICD_FILENAMES="" to disable Vulkan | Empty string does NOT override default ICD search path (`/usr/share/vulkan/icd.d/`). Vulkan still loads. Must rename the ICD JSON file instead. |
| -ub 32 micro-batch flag | Reducing micro-batch size to limit GPU matrix size didn't prevent DeviceLostError crash. |
| Parallel heavy tasks | Compile + download at same time killed both. Do one at a time on this board. |
| huggingface-cli | Deprecated. Use ~/.local/bin/hf instead. |
| pip without --break-system-packages | Debian blocks it. Use venv or add the flag. |
| httpx `aiter_lines()` for SSE streaming | Buffers internally — tokens arrive in bursts. Use `aiter_bytes()` with manual `\n` splitting. |
| Running llama-server without `--reasoning off` | Gemma 4E thinking mode: 100-200 silent `reasoning_content` tokens before any `content`. 60-120s silence then all text at once. Always use `--reasoning off`. |
| `pkill -f <string>` over SSH when string is in command | Kills the SSH session. Use `fuser -k PORT/tcp`. |
| Testing SSE by piping curl over SSH | SSH pseudo-TTY swallows SSE newline formatting. Always redirect to file then cat. |
| `budget_tokens: 0` API param to disable thinking | Llama-server ignores it. Must use `--reasoning off` server flag. |
| `fetch` ReadableStream for SSE on iOS Safari | Does not deliver chunks incrementally. Perpetual dots. |
| XHR `onprogress` for streaming on iOS Safari | Same — does not stream progressively on iOS Safari. |
| `max_tokens: 256` | 144s worst-case — exceeds browser timeouts. |
| `max_tokens: 90` | 51s worst-case generation. Reduced to 35 — AMICA's replies average 15-25 tokens, so nothing cut off. ~14s generation now. |
| Regex-only memory extraction | Misspelled trigger words ("rememeber") caused `return False` before person detection. Fixed by Pass 1 / Pass 2 restructure. |
| Profile note fallback without trigger gate | Saved every "I …" statement as a profile note, polluting the "About them" field with chat content. Fixed by gating fallback behind `has_trigger`. |
| Client hard fallback at 90s | With ~180-token system prompt, prompt processing takes ~63s. 90s was too close to first-token arrival. Raised to 140s. |

---

## Planned / Not Yet Built

### Done ✓
- [x] Fix SSE streaming — `aiter_bytes()` + `--reasoning off` on llama-server
- [x] Compact system prompt (~180 tokens)
- [x] Token-by-token visual streaming on iOS Safari — EventSource queue/stream pattern
- [x] Works out of the box at boot — no SSH needed
- [x] Wi-Fi hotspot — "AMICA" / amica2024, board at 10.42.0.1:5000
- [x] Systemd auto-start with boot race condition fixed
- [x] Family portal at /family — guardian web form to add/edit/delete everything
- [x] Three-layer memory save cascade — [MEM:] tag + regex (Pass 1/2) + background LLM
- [x] Voice input — webkitSpeechRecognition mic button, works on iOS Safari
- [x] AMICA says "you/your" not third-person name
- [x] Traits saved and recalled for people
- [x] People sorted by recency, 10-person cap
- [x] `max_tokens: 35` — generation time cut from ~51s to ~14s
- [x] KV cache persistence — warmup.bin + warmup.hash, instant same-day restarts
- [x] Session system prompt snapshot — KV cache never invalidated mid-session
- [x] Background re-warmup on family portal saves — chat is warm when user arrives

### Remaining / Stretch
- [ ] Demo video (3 min max, YouTube) — story: elderly person, kitchen table, phone, privacy
- [ ] Kaggle Writeup (1500 words max) — select llama.cpp track + Digital Equity track
- [ ] Public GitHub repo
- [ ] Cover image for media gallery
- [ ] **Submit before May 19, 2026 at 12:59 AM GMT+1**
- [ ] Camera vision (see notes below)
- [ ] Quick-reply buttons: "What's on this week?" / "My medicines" / "Who's visiting?"
- [ ] Auto-speak AMICA responses (voice on by default)
- [ ] Proactive medication reminders

### Camera / Vision notes
Gemma 4 E2B is natively multimodal and llama.cpp supports vision via a `--mmproj` file.
**What's needed:**
1. Download the Gemma 4E mmproj GGUF from HuggingFace (separate file, ~300-500MB)
2. Restart llama-server with `--mmproj /path/to/mmproj.gguf`
3. Phone UI: `<input type="file" accept="image/*" capture="camera">` → base64 encode → send as vision message in the chat completions payload
4. Server: include `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}` in the messages array

**RAM concern:** model is 3.22GB, board has 4GB total. mmproj adds ~300-500MB — very tight. May need to reduce `--ctx-size` from 2048 to 1024 to free headroom. Test carefully before demo.

---

## Key Decisions & Rationale

| Decision | Rationale |
|---|---|
| llama.cpp not Ollama | llama.cpp Special Track ($10k) — more specific, less competition |
| E2B not E4B | 4GB RAM hard limit. E2B Q4_K_M is 3.22GB — fits with ~780MB headroom |
| CPU-only, no Vulkan | SPIR-V headers missing from Debian image, not worth fighting |
| Browser UI not native app | Universal (any phone, no install), faster to build, easier to demo |
| Slow responses are okay | 60-115s is long but acceptable — "thoughtful pause" framing. Short prompts help. |
| memory.json not a DB | Simple, editable by family, no dependencies |
| `--reasoning off` on llama-server | Gemma 4E thinking mode: 100-200 silent tokens before visible output. Disabling cuts first-token latency by 60-120s. |
| `aiter_bytes()` not `aiter_lines()` in httpx | `aiter_lines()` has internal buffering. `aiter_bytes()` with manual `\n` splitting forwards tokens immediately. |
| System prompt ~180 tokens | 349ms/token × 180 = 63s prompt latency. Deliberately compact — every token costs ~350ms. |
| EventSource (GET SSE) not fetch SSE | iOS Safari's fetch ReadableStream and XHR onprogress don't stream incrementally. Native `EventSource` does. |
| `max_tokens: 35` | 35 × ~400ms = ~14s worst-case generation. AMICA's replies average 15-25 tokens — nothing cut. Down from 90 (~51s). |
| KV cache persistence | After warmup, slot 0 saved to disk (warmup.bin) + SHA256 of system prompt (warmup.hash). On restart, if hash matches, instant restore. Same-day restarts skip 63s warmup entirely. |
| Session system prompt snapshot | `_session_system_prompt` locked at warmup time, reused for whole session. Mid-session memory saves (via chat or portal) don't change the prompt hash, so the KV cache prefix never changes mid-conversation. |
| Background re-warmup on portal saves | `_trigger_background_warmup()` called on every family portal write. Demo strategy: save in portal → warmup fires → explain portal for 90s → navigate to chat, first response is just ~14s generation. |
| [MEM:] tag in AMICA's response | LLM reasoning detects save intent far more reliably than regex on messy user input. Tag format is controlled and consistent. Regex runs on LLM output, not user input. |
| Pass 1 structural patterns before trigger check | Misspelled trigger words ("rememeber") used to exit before person detection. Pass 1 runs `_PERSON_RE` / `_MET_PERSON_RE` / `_MED_RE` on whole message regardless of triggers. |
| Profile note fallback gated behind has_trigger | Bare "I …" statements (e.g. "I like climbing too") would pollute profile notes. Only save notes when user explicitly asks. |
| People sorted by recency in system prompt | Newly added people (via chat or /family) always appear in the top 10. Oldest entries fall off rather than newest. |
| /family portal for editing, chat for adding | Safer for elderly users — chat adds naturally, deliberate portal UI required to delete/correct. Prevents accidental deletions. |
| LLAMA_TIMEOUT: 125s, client hard fallback: 140s | Server needs 114s worst-case; client gives 15s margin beyond that before showing timeout error. |

---

## Style & Constraints

- UI text: simple and clear, reading age 10-12 years, no jargon
- AMICA tone: warm, patient, never condescending, never makes user feel bad for forgetting
- Responses: short (2-3 sentences) — better for elderly users AND faster on this hardware
- Everything must work 100% offline once hotspot is configured
- SSH into UNO Q will be sluggish during active inference — this is normal on 4 cores
