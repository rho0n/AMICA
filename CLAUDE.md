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
| **Inference engine** | llama.cpp (llama-server), ARM64 CPU-only (no Vulkan — see graveyard) |
| **Demo phone** | iPhone 13. Connects to UNO Q over Wi-Fi, accesses browser UI. |
| **Client** | Any phone browser — no app, no install required |

### Actual Performance (measured on device)
- Without system prompt: ~1.88 tokens/second (measured 2026-05-16)
- With AMICA system prompt (~308 tokens): ~1.77 tokens/second
- Prompt processing: ~358ms/token → 308-token prompt takes ~25s before first token
- Typical response time: 25s (prompt) + 10-20s (generation) = ~35-45s total
- CPU features: fp asimd evtstrm aes pmull sha1 sha2 crc32 cpuid — NO asimddp, no SVE, no i8mm. Cannot be improved by recompiling.
- RAM at idle: 593MB used, 3.0GB available, 1.8GB swap

### SSH Access
- User: `arduino`, password: `password`
- Use `sshpass -e` with `SSHPASS=password` env var for non-interactive scripts
- **Never use `pkill -f <string>` over SSH if `<string>` appears in the SSH command itself** — it kills the SSH session. Use `fuser -k 8080/tcp` to kill by port instead.
- Kill llama-server: `echo 'password' | sudo -S fuser -k 8080/tcp`
- Kill uvicorn: `pkill -f uvicorn` is safe (no "uvicorn" in the SSH command string itself... unless you pass it inline)
- **Testing SSE over SSH**: do NOT pipe curl output directly — the SSH pseudo-TTY swallows SSE newlines. Always redirect: `curl ... > /tmp/out.txt; cat /tmp/out.txt`

---

## Architecture

```
Arduino UNO Q 4GB (Debian Linux)
  llama-server (port 8080)
    /home/arduino/amica/models/google_gemma-4-E2B-it-Q4_K_M.gguf
  amica_server.py (FastAPI, port 5000)
    ~/amica/venv (Python virtualenv)
    Reads memory.json, builds system prompt
    Proxies and streams to llama-server
    Serves static/index.html to phones
  Wi-Fi hotspot: "AMICA" / 10.42.0.1 (NOT YET CONFIGURED)
  Currently accessible at: 192.168.1.156 on home Wi-Fi
```

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

---

## How to Start Everything Manually (systemd NOT set up yet)

### Terminal 1 — llama-server
```bash
sudo /root/llama.cpp/build/bin/llama-server \
  -m ~/amica/models/google_gemma-4-E2B-it-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 \
  --ctx-size 2048 \
  --threads 4 \
  --n-predict 256 \
  --reasoning off
```
Wait for: server is listening on http://0.0.0.0:8080

**NOTE: `--reasoning off` is required.** Gemma 4E defaults to thinking/reasoning mode, emitting
all tokens as `delta.reasoning_content` before any `delta.content`. Without this flag, the model
can spend 100-200 tokens (60-120 seconds) in a silent thinking phase before the first visible word.
With `--reasoning off`, tokens flow directly as `delta.content` from the first generation step.

### Terminal 2 — Python API
```bash
cd ~/amica
source venv/bin/activate
uvicorn amica_server:app --host 0.0.0.0 --port 5000
```

### Health checks
```bash
curl http://localhost:8080/health
# should return {"status":"ok"}
curl http://localhost:5000/api/health
# should return {"amica":"ok","llama_server":"ready","user":"Margaret"}
```

### Phone access (currently)
Phone on same home Wi-Fi → browser → http://192.168.1.156:5000

---

## Current Status

### WORKING (as of 2026-05-16)
- llama-server running Gemma 4 E2B on UNO Q CPU with `--reasoning off`
- FastAPI server serving UI and proxying to llama-server
- Memory system loading Margaret's profile — system prompt trimmed to ~308 tokens
- **SSE streaming confirmed working** — tokens arrive word-by-word in browser (verified via file-redirect curl test on localhost)
- Phone UI loads correctly on laptop and phone (same Wi-Fi)
- Health endpoints returning correct status
- "Reply in 2-3 sentences maximum" baked into system prompt

### BROKEN — FIX THESE NEXT

**1. Wi-Fi hotspot not configured (PRIORITY 1 for offline demo)**
Phone currently needs home Wi-Fi. For offline use, UNO Q needs its own "AMICA" access point.
First check Wi-Fi interface name: iw dev
Then: sudo apt-get install -y hostapd dnsmasq
Configure hostapd for SSID "AMICA", password "amica2024", static IP 10.42.0.1

**2. Systemd services not set up (PRIORITY 1)**
Both processes die when SSH closes. Need systemd units for auto-start on boot.
setup_amica.sh has the definitions but was never fully run.
The llama-server systemd unit MUST include `--reasoning off` in the ExecStart line.

**3. ~25 second wait before first token (PRIORITY 2)**
The 308-token system prompt takes ~25s of prompt processing before any word appears. The UI
already has a typing indicator (dots animation) that shows immediately on send — this helps. But
for the demo, consider further trimming the system prompt or caching the KV state.

**4. Google Fonts CDN in index.html (PRIORITY 3)**
Falls back to Georgia when offline. Fine functionally, fix for polish.

---

## What Didn't Work (Graveyard — Do Not Revisit)

| Approach | Why Abandoned |
|---|---|
| MediaPipeTasksGenAI + .litertlm | RET_CHECK GPU crashes, wrong compression, no manifest. Hours wasted. |
| ONNX Runtime genai-objc | 9 config files, custom tokenisation — too much for hackathon timeline. |
| Running model on iPhone 13 | Not enough RAM/performance. |
| Vulkan on UNO Q Adreno | glslc installed but SPIR-V headers missing. Multiple failed attempts. Do not retry. |
| Parallel heavy tasks | Compile + download at same time killed both. Do one at a time on this board. |
| huggingface-cli | Deprecated. Use ~/.local/bin/hf instead. |
| pip without --break-system-packages | Debian blocks it. Use venv or add the flag. |
| httpx `aiter_lines()` for SSE streaming | Buffers internally — tokens arrive in bursts not individually. Use `aiter_bytes()` with manual line splitting instead (current implementation). |
| Running llama-server without `--reasoning off` | Gemma 4E thinking mode emits 100-200 `reasoning_content` tokens silently before any `content` token. Parser sees nothing for 60-120s then all text at once. Always use `--reasoning off`. |
| `pkill -f <string>` over SSH when string is in command | Kills the SSH session itself. Use `fuser -k PORT/tcp` to kill by port. |
| Testing SSE by piping curl over SSH | SSH pseudo-TTY swallows SSE newline formatting. Always redirect: `curl ... > /tmp/out.txt` then read the file. |
| `budget_tokens: 0` API param to disable thinking | Llama-server ignores it. Must use `--reasoning off` server flag. |

---

## Planned / Not Yet Built

### Priority 1 — Done ✓
- [x] Fix SSE streaming — `aiter_bytes()` + `--reasoning off` on llama-server
- [x] Trim system prompt to ~308 tokens (from 700+)
- [x] Short response enforcement — "Reply in 2-3 sentences maximum" in system prompt
- [x] Typing indicator already present in UI (dots animation)

### Priority 2 — Needed for proper demo
- [ ] Wi-Fi hotspot (AMICA network, offline)
- [ ] Systemd auto-start services (must include `--reasoning off` in llama-server unit)

### Priority 3 — Strong for hackathon score
- [ ] Quick-reply buttons: "What's on this week?" / "My medicines" / "Who's visiting?"
- [ ] Auto-speak AMICA responses (voice on by default)
- [ ] Family setup portal at /setup — web form to edit memory.json

### Priority 4 — Stretch
- [ ] Camera: phone photo → AMICA describes it (meds label, family photo). E2B supports vision natively.
- [ ] Memory auto-update: AMICA writes new facts back to memory.json
- [ ] Proactive medication reminders

---

## Key Decisions & Rationale

| Decision | Rationale |
|---|---|
| llama.cpp not Ollama | llama.cpp Special Track ($10k) — more specific, less competition |
| E2B not E4B | 4GB RAM hard limit. E2B Q4_K_M is 3.22GB — fits with ~800MB headroom |
| CPU-only, no Vulkan | SPIR-V headers missing from Debian image, not worth fighting. Future optimisation. |
| Browser UI not native app | Universal (any phone, no install), faster to build, easier to demo |
| Slow responses are okay | 35-45s is borderline but acceptable — "thoughtful pause" framing. Short prompts help. |
| memory.json not a DB | Simple, editable by family, no dependencies |
| `--reasoning off` on llama-server | Gemma 4E thinking mode uses 100-200 silent tokens before any visible output. Disabling it cuts first-token latency by 60-120 seconds. llama-server v1 (build 1348f67) supports `--reasoning [on\|off\|auto]`. |
| `aiter_bytes()` not `aiter_lines()` in httpx | `aiter_lines()` has internal buffering that holds back SSE lines. `aiter_bytes()` with manual `\n` splitting forwards each token immediately. |
| System prompt under 350 tokens | Each token in the prompt costs ~358ms of processing time on this hardware. 308 tokens = ~25s latency. 700 tokens (old) = ~50s latency before first word. |

---

## Hackathon Submission Checklist

- [x] Fix streaming + trim system prompt
- [ ] Wi-Fi hotspot working
- [ ] Systemd services
- [ ] Demo video (3 min max, YouTube) — story: elderly person, kitchen table, phone, privacy
- [ ] Kaggle Writeup (1500 words max) — select llama.cpp track + Digital Equity track
- [ ] Public GitHub repo
- [ ] Cover image for media gallery
- [ ] Submit before May 19, 2026 at 12:59 AM GMT+1

---

## Style & Constraints

- UI text: simple and clear, reading age 10-12 years, no jargon
- AMICA tone: warm, patient, never condescending, never makes user feel bad for forgetting
- Responses: short (2-3 sentences) — better for elderly users AND faster on this hardware
- Everything must work 100% offline once hotspot is configured
- SSH into UNO Q will be sluggish during active inference — this is normal on 4 cores
