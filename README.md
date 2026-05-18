# AMICA — Offline AI Companion for Elderly Adults

Gemma 4 E2B running fully offline on a $59 ARM SBC via llama.cpp. Any phone connects to its Wi-Fi hotspot and opens a browser. No app, no internet, no data ever leaves the room.

**Hackathon:** Gemma 4 Good Hackathon (Kaggle / Google DeepMind)
**Tracks:** llama.cpp Special Technology + Digital Equity & Inclusivity

---

## What Is AMICA?

AMICA (AI Memory & Intelligent Care Assistant) is a conversational AI companion for elderly and vulnerable people. It runs entirely on an Arduino UNO Q 4GB — a $59 single-board computer — with no internet connection required.

AMICA remembers who the user knows, their medications, and upcoming events. A family member or carer can pre-load this information via a browser portal. The user connects to AMICA's private Wi-Fi hotspot and chats through any phone browser — no app install, no account, no data plan.

The core principle: conversations about health, family, and daily life should never leave the room.

---

## Hardware

| Component | Details |
|---|---|
| **Server** | Arduino UNO Q 4GB — Qualcomm Dragonwing QRB2210, quad-core ARM Cortex-A53 at 2GHz, 4GB LPDDR4, 32GB eMMC, Wi-Fi 5, Debian Linux. ~$59. |
| **Model** | Gemma 4 E2B — `bartowski/google_gemma-4-E2B-it-GGUF`, file `google_gemma-4-E2B-it-Q4_K_M.gguf`, 3.22GB, Apache 2.0 |
| **Inference** | llama.cpp (llama-server), ARM64 CPU-only |
| **Client** | Any phone browser — no app, no install required |

---

## Architecture

```
Arduino UNO Q 4GB (Debian Linux)
  llama-server      port 8080 (localhost only)
    Gemma 4 E2B Q4_K_M GGUF
  amica_server.py   port 5000
    FastAPI — injects personalised system prompt, handles streaming,
    manages memory, serves phone UI and family portal
  Wi-Fi hotspot: "AMICA" / "amica2024"
  Board address: 10.42.0.1

Phone connects to "AMICA" Wi-Fi -> opens browser -> http://10.42.0.1:5000
Family/carer portal: http://10.42.0.1:5000/family
```

Both services start automatically on boot via systemd — no SSH required.

### Streaming

iOS Safari does not support incremental `fetch` ReadableStream or XHR `onprogress`. AMICA uses the native `EventSource` API:

1. Client POSTs `/api/chat/queue` — gets a `job_id` back in under 1 second
2. Client opens `EventSource('/api/chat/stream/{job_id}')` — GET request
3. Server streams tokens word by word as they arrive from llama-server
4. SSE keepalive comments sent every 15 seconds during the ~60s silent prompt-processing phase

### Memory

Memory is stored in `memory.json` — plain JSON, human-readable, editable directly by a carer. AMICA updates it automatically from conversation using a three-layer cascade:

1. **[MEM:] tag** — Gemma ends its reply with a structured tag when it detects a save intent. Most reliable path.
2. **Regex** — two-pass regex system; Pass 1 runs without requiring a trigger word (catches typos like "rememeber").
3. **Background LLM** — if both miss, a compact JSON extraction prompt fires 3 seconds later on the idle llama-server.

---

## Performance

The Cortex-A53 has no dotprod, no SVE, no i8mm. Inference runs on basic NEON SIMD.

- Prompt processing: ~349ms/token (~120s for the 180-token system prompt on first load)
- Generation: ~565ms/token (~1.77 tok/s)
- `max_tokens: 35` — typical replies are 15-25 tokens, generation takes ~14s
- **KV cache persistence** — warmup state is saved to `warmup.bin` after the first load. Same-day restarts restore it instantly, skipping the ~120s warmup entirely.
- Background re-warmup fires automatically whenever the family portal is saved, so the system is warm before the user's first message.

One critical discovery: Gemma 4E defaults to thinking/reasoning mode, spending 100-200 tokens in silent internal reasoning before any visible output. On hardware doing 349ms/token this adds 60-120 silent seconds. The fix is `--reasoning off` on llama-server. There is no API parameter that overrides it reliably — this flag must be set at the server level.

---

## Setup

### Prerequisites

- Arduino UNO Q 4GB (or any Linux ARM64 SBC with 4GB+ RAM)
- SSH access to the board
- Internet connection on the board for initial setup only

### 1. Copy files to the board

```bash
scp -r amica_server.py memory_manager.py static/ arduino@<board-ip>:/home/arduino/amica/
```

### 2. Install Python dependencies

```bash
ssh arduino@<board-ip>
cd ~/amica
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn httpx
```

### 3. Build llama.cpp

```bash
sudo apt-get install -y build-essential cmake git
sudo git clone https://github.com/ggerganov/llama.cpp /root/llama.cpp
cd /root/llama.cpp
sudo cmake -B build -DGGML_NATIVE=ON
sudo cmake --build build --config Release -j4
```

This takes approximately 20-30 minutes on the Cortex-A53.

### 4. Download the model

```bash
pip install hf_transfer huggingface_hub
~/.local/bin/hf download bartowski/google_gemma-4-E2B-it-GGUF \
  google_gemma-4-E2B-it-Q4_K_M.gguf \
  --local-dir ~/amica/models/
```

The model is 3.22GB. Download takes 10-20 minutes depending on connection speed.

### 5. Create the start script

Create `/home/arduino/amica/start-llama.sh`:

```bash
#!/bin/bash
B=/root/llama.cpp/build/bin/llama-server
M=/home/arduino/amica/models/google_gemma-4-E2B-it-Q4_K_M.gguf
S=/home/arduino/amica/
exec $B --parallel 1 -m $M --host 127.0.0.1 --port 8080 \
  --ctx-size 1024 --threads 4 --n-predict 256 -fa on \
  --reasoning off --slot-save-path $S --log-disable
```

```bash
chmod +x /home/arduino/amica/start-llama.sh
```

### 6. Set up systemd services

Create `/etc/systemd/system/amica-llama.service`:

```ini
[Unit]
Description=AMICA llama.cpp inference server
After=local-fs.target

[Service]
Type=simple
User=root
ExecStart=/home/arduino/amica/start-llama.sh
Restart=on-failure
RestartSec=10
StandardOutput=append:/var/log/amica-llama.log
StandardError=append:/var/log/amica-llama.log

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/amica-api.service`:

```ini
[Unit]
Description=AMICA FastAPI server
After=network.target

[Service]
Type=simple
User=arduino
WorkingDirectory=/home/arduino/amica
ExecStart=/home/arduino/amica/venv/bin/uvicorn amica_server:app --host 0.0.0.0 --port 5000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable amica-llama amica-api
sudo systemctl start amica-llama amica-api
```

### 7. Configure Wi-Fi hotspot

Set up a Wi-Fi access point named "AMICA" with password "amica2024" using NetworkManager or hostapd so the board is accessible at `10.42.0.1`.

### 8. Create memory.json

```bash
cp memory_example.json ~/amica/memory.json
```

Edit it with the user's name, medications, family members, and upcoming events. Or use the family portal at `http://<board-ip>:5000/family` once the server is running.

---

## Memory File Format

```json
{
  "profile": {
    "name": "Margaret",
    "notes": []
  },
  "people": [],
  "medications": [],
  "events": []
}
```

See `memory_example.json` for a complete example with all fields.

---

## Deploying Updates

From your development machine:

```bash
# If your board is on your home/local network, set its IP first:
BOARD_HOME_IP=192.168.1.100 bash deploy.sh

# If using the AMICA hotspot only, just run:
bash deploy.sh
```

This copies `index.html`, `memory_manager.py`, and `amica_server.py` to the board and restarts the service.

To find your board's local IP, run `ip addr` or `hostname -I` on the board over SSH. The script falls back to the hotspot IP (`10.42.0.1`) automatically if the home IP is not set or unreachable.

The script uses `sshpass` — install it first if needed:
```bash
# macOS
brew install hudochenkov/sshpass/sshpass

# Debian/Ubuntu
sudo apt-get install sshpass
```

---

## Health Checks

```bash
# llama-server
curl http://<board-ip>:8080/health

# AMICA API
curl http://<board-ip>:5000/api/health
```

---

## Design Notes

**Why llama.cpp:** The only framework that runs a quantised Gemma model on a 4-core ARM Cortex-A53 without a GPU, cloud backend, or specialist hardware. Server mode provides streaming, slot-based KV cache management, and a clean HTTP API.

**Why browser UI:** Any phone, any OS, zero install friction. The entire UI is a single HTML file with no framework dependencies.

**Why memory.json:** Plain JSON is human-readable, directly editable by a carer, and needs no migrations, schemas, or dependencies.

**Why offline-first:** Privacy is the feature. Conversations about medications, family names, and daily life should not be uploaded to a data centre. A cloud-connected alternative would be faster, but it would require consent, data governance, and infrastructure that puts it out of reach for the target population.

**What didn't work:** Vulkan GPU offload via the Adreno 702 and Mesa Turnip driver crashes with a device lost error on every inference request with a real system prompt. OpenBLAS is slower than GGML's native Q4_K kernels on this chip due to float32 dequantisation overhead. CPU-only is stable and predictable.

---

## Files

| File | Description |
|---|---|
| `amica_server.py` | FastAPI server — routing, streaming, memory injection, warmup |
| `memory_manager.py` | Memory read/write, system prompt builder, three-layer save cascade |
| `static/index.html` | Phone UI — single HTML file, no framework dependencies |
| `memory_example.json` | Example memory file structure (no personal data) |
| `deploy.sh` | Deploy script — copies files to board and restarts service |
| `setup_amica.sh` | One-shot setup script for a fresh board |

---

## License

Apache 2.0 — same as Gemma 4.
