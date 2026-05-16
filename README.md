# AMICA — Offline AI Companion for Elderly & Vulnerable Adults

> **Gemma 4 Good Hackathon** — targeting `llama.cpp` Special Technology Track ($10k) + Digital Equity & Inclusivity Impact Track ($10k)

---

## What Is AMICA?

**AMICA** (AI Memory & Intelligent Care Assistant) is a 100% offline, privacy-first conversational companion powered by **Gemma 4 E2B** running on an **Arduino UNO Q 4GB** via `llama.cpp`.

Any phone (iPhone, Android — no app install needed) connects to AMICA's private Wi-Fi hotspot and opens a browser to talk with it. No internet. No cloud. No data ever leaves the room.

Designed for elderly and vulnerable people who need a gentle, patient, non-judgmental assistant that remembers their world — medications, family names, upcoming visits, important dates — so they never have to feel embarrassed about forgetting.

---

## Hardware

| Component | Details |
|---|---|
| **Server** | Arduino UNO Q 4GB (Qualcomm QRB2210, quad-core ARM Cortex-A53 @ 2GHz, Adreno GPU, Debian Linux) |
| **Model** | Gemma 4 E2B — GGUF Q4_K_M quantisation (~1.4 GB, fits in 4 GB RAM) |
| **Inference** | `llama.cpp` (llama-server) — ARM64 + optional Vulkan via Adreno GPU |
| **Client** | Any phone browser — no app, no install |
| **Network** | UNO Q creates a Wi-Fi Access Point; phone connects to "AMICA" hotspot |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              Arduino UNO Q 4GB                       │
│                                                     │
│  ┌─────────────┐    ┌──────────────────────────┐   │
│  │ llama-server│◄───│  amica_server.py          │   │
│  │ (port 8080) │    │  FastAPI + Memory Manager │   │
│  │  Gemma 4 E2B│    │  (port 5000)              │   │
│  └─────────────┘    └──────────┬───────────────┘   │
│                                │                    │
│                        memory.json                  │
│                    (family, meds, events)            │
│                                                     │
│  Wi-Fi Access Point: "AMICA" / 10.42.0.1            │
└─────────────────────────────────────────────────────┘
                          ▲
                   Wi-Fi (offline)
                          │
              ┌───────────┴───────────┐
              │    Any Phone Browser   │
              │  http://10.42.0.1:5000 │
              │                       │
              │  • Large text UI      │
              │  • Voice input/output │
              │  • No app install     │
              └───────────────────────┘
```

---

## Quick Start

### 1. SSH into your UNO Q

```bash
ssh arduino@<uno-q-ip>
# Default password: arduino (change this!)
```

### 2. Run the one-shot setup script

```bash
chmod +x setup_amica.sh
sudo ./setup_amica.sh
```

This will:
- Install dependencies (build-essential, cmake, python3, etc.)
- Clone and compile `llama.cpp` for ARM64 + Vulkan
- Download Gemma 4 E2B Q4_K_M GGUF from HuggingFace
- Set up the Python FastAPI server
- Configure Wi-Fi hotspot ("AMICA" network)
- Register everything as systemd services (auto-start on boot)

### 3. Connect your phone

1. Open Wi-Fi settings on any phone
2. Connect to **"AMICA"** (password: `amica2024`)
3. Open browser → `http://10.42.0.1:5000`
4. AMICA introduces herself and explains how she works

---

## Editing the Memory File

The memory file lives at `~/amica/memory.json`. Edit it to personalise AMICA for each user.

```bash
nano ~/amica/memory.json
```

AMICA also **updates it herself** after conversations — if she learns something new about a family member or hears about a medication change, she logs it with a timestamp.

---

## File Structure

```
~/amica/
├── setup_amica.sh          # One-shot install script
├── hotspot_setup.sh        # Wi-Fi AP configuration
├── amica_server.py         # FastAPI server + memory management
├── memory_manager.py       # Memory read/write/update logic
├── memory.json             # The user's personal memory file
├── static/
│   └── index.html          # Phone UI (served to browser)
└── models/
    └── gemma-4-e2b-Q4_K_M.gguf
```

---

## Hackathon Track Positioning

| Track | Justification |
|---|---|
| **llama.cpp Special** ($10k) | Gemma 4 E2B on Qualcomm ARM64 + Vulkan Adreno GPU, resource-constrained ($59 SBC) |
| **Digital Equity** ($10k) | Zero-cost connectivity (no SIM, no data plan), works for any phone, voice interface |
| **Main Track** | Compelling real-world impact story — loneliness + memory loss in elderly adults |
