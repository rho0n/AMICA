#!/bin/bash
# ============================================================
#  AMICA Setup Script — Arduino UNO Q 4GB (Debian Linux)
#  Run once via SSH: sudo ./setup_amica.sh
# ============================================================
set -e

AMICA_DIR="$HOME/amica"
MODELS_DIR="$AMICA_DIR/models"
MODEL_REPO="bartowski/google_gemma-4-E2B-it-GGUF"
MODEL_NAME="google_gemma-4-E2B-it-Q4_K_M.gguf"
LLAMA_DIR="$HOME/llama.cpp"
HOTSPOT_SSID="AMICA"
HOTSPOT_PASS="amica2024"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🌸  AMICA Setup — Gemma 4 on UNO Q"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. System Dependencies ──────────────────────────────────
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y --no-install-recommends \
  build-essential cmake git curl wget \
  python3 python3-pip python3-venv \
  hostapd dnsmasq iptables \
  libvulkan-dev vulkan-tools \
  net-tools iproute2

# ── 2. Build llama.cpp (ARM64 + Vulkan) ─────────────────────
echo "[2/7] Cloning and building llama.cpp..."
if [ ! -d "$LLAMA_DIR" ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
fi
cd "$LLAMA_DIR"
git pull --ff-only 2>/dev/null || true
cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_VULKAN=ON \
  -DLLAMA_BUILD_SERVER=ON \
  -DCMAKE_CXX_FLAGS="-march=armv8-a" \
  -DCMAKE_C_FLAGS="-march=armv8-a"
cmake --build build --config Release -j$(nproc)

echo "  ✓ llama.cpp built at $LLAMA_DIR/build/bin/llama-server"

# ── 3. Download Gemma 4 E2B Model ──────────────────────────
echo "[3/7] Downloading Gemma 4 E2B Q4_K_M GGUF..."
echo "  (Apache 2.0 — no login required)"
mkdir -p "$MODELS_DIR"
if [ ! -f "$MODELS_DIR/$MODEL_NAME" ]; then
  pip install -q huggingface_hub
  huggingface-cli download "$MODEL_REPO" \
    --include "$MODEL_NAME" \
    --local-dir "$MODELS_DIR"
  echo "  ✓ Model downloaded to $MODELS_DIR/$MODEL_NAME"
else
  echo "  ✓ Model already present, skipping download."
fi

# ── 4. Python virtual environment ──────────────────────────
echo "[4/7] Setting up Python environment..."
mkdir -p "$AMICA_DIR/static"
python3 -m venv "$AMICA_DIR/venv"
"$AMICA_DIR/venv/bin/pip" install --quiet \
  fastapi uvicorn httpx python-multipart

echo "  ✓ Python venv ready at $AMICA_DIR/venv"

# ── 5. Wi-Fi Hotspot ────────────────────────────────────────
echo "[5/7] Configuring Wi-Fi hotspot: '$HOTSPOT_SSID'..."

# Identify the Wi-Fi interface
WIFI_IF=$(iw dev | awk '$1=="Interface"{print $2}' | head -1)
if [ -z "$WIFI_IF" ]; then
  echo "  ⚠  No Wi-Fi interface found. Hotspot step skipped."
  echo "     Connect UNO Q and phone to the same router instead."
else
  echo "  Using Wi-Fi interface: $WIFI_IF"

  # hostapd config
  cat > /etc/hostapd/amica.conf <<EOF
interface=$WIFI_IF
ssid=$HOTSPOT_SSID
hw_mode=g
channel=6
auth_algs=1
wpa=2
wpa_passphrase=$HOTSPOT_PASS
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF

  # dnsmasq config
  cat > /etc/dnsmasq.d/amica.conf <<EOF
interface=$WIFI_IF
dhcp-range=10.42.0.10,10.42.0.50,12h
# Redirect all DNS to AMICA so phone browser can use hostname
address=/#/10.42.0.1
EOF

  # Static IP for hotspot interface
  cat >> /etc/dhcpcd.conf <<EOF

# AMICA hotspot
interface $WIFI_IF
static ip_address=10.42.0.1/24
nohook wpa_supplicant
EOF

  # IP forwarding off (fully offline, no internet sharing needed)
  sed -i 's/#net.ipv4.ip_forward=1/net.ipv4.ip_forward=0/' /etc/sysctl.conf

  # Enable services
  systemctl unmask hostapd
  systemctl enable hostapd dnsmasq
  echo "  ✓ Hotspot will start on next boot as '$HOTSPOT_SSID' / '$HOTSPOT_PASS'"
fi

# ── 6. Systemd Services ─────────────────────────────────────
echo "[6/7] Creating systemd services..."

# llama-server service
cat > /etc/systemd/system/amica-llama.service <<EOF
[Unit]
Description=AMICA llama.cpp inference server
After=network.target

[Service]
User=$USER
WorkingDirectory=$LLAMA_DIR
ExecStart=$LLAMA_DIR/build/bin/llama-server \\
  --model $MODELS_DIR/google_gemma-4-E2B-it-Q4_K_M.gguf \\
  --host 127.0.0.1 --port 8080 \\
  --ctx-size 4096 \\
  --n-predict 512 \\
  --threads $(nproc) \\
  --flash-attn \\
  --log-disable
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# AMICA Python API service
cat > /etc/systemd/system/amica-api.service <<EOF
[Unit]
Description=AMICA FastAPI server
After=amica-llama.service
Requires=amica-llama.service

[Service]
User=$USER
WorkingDirectory=$AMICA_DIR
ExecStart=$AMICA_DIR/venv/bin/uvicorn amica_server:app \\
  --host 0.0.0.0 --port 5000 \\
  --log-level warning
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable amica-llama amica-api

echo "  ✓ Services registered and enabled"

# ── 7. Create default memory file ───────────────────────────
echo "[7/7] Creating default memory file..."
if [ ! -f "$AMICA_DIR/memory.json" ]; then
cat > "$AMICA_DIR/memory.json" <<'MEMORY'
{
  "profile": {
    "name": "Friend",
    "notes": "Edit this file to personalise AMICA. Replace 'Friend' with the user's first name."
  },
  "medications": [],
  "family_and_friends": [],
  "upcoming_events": [],
  "recent_notes": [],
  "last_updated": "Setup"
}
MEMORY
  echo "  ✓ Created memory.json — edit it to personalise AMICA"
  echo "     File: $AMICA_DIR/memory.json"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅  AMICA setup complete!"
echo ""
echo "  Start services now:"
echo "    sudo systemctl start amica-llama"
echo "    sudo systemctl start amica-api"
echo ""
echo "  Or reboot for everything including hotspot:"
echo "    sudo reboot"
echo ""
echo "  Phone access:"
echo "    Wi-Fi: $HOTSPOT_SSID  |  Password: $HOTSPOT_PASS"
echo "    Browser: http://10.42.0.1:5000"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
