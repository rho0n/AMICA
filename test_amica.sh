#!/bin/bash
PASS=password
for IP in 192.168.1.156 10.42.0.1; do
  if sshpass -p $PASS ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no arduino@$IP "echo ok" &>/dev/null; then
    BOARD_IP=$IP; break
  fi
done
if [ -z "$BOARD_IP" ]; then echo "ERROR: Board not reachable"; exit 1; fi
HOST=http://$BOARD_IP:5000
SSH="sshpass -p $PASS ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=no arduino@$BOARD_IP"
echo "Board found at $BOARD_IP"

# ── 0. Direct llama-server probe (via SSH) ─────────
echo ""; echo "[ 0 ] Direct llama-server — non-streaming, small prompt"
$SSH "curl -s --max-time 120 -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{\"model\":\"gemma\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi.\"}],\"stream\":false,\"max_tokens\":40}' \
  > /tmp/llama_probe.json; cat /tmp/llama_probe.json"
echo ""
echo "  (content field):"
$SSH "cat /tmp/llama_probe.json | python3 -c \"
import json,sys
d=json.load(sys.stdin)
c=d.get('choices',[{}])[0]
print('finish_reason:', c.get('finish_reason'))
print('content:', c.get('message',{}).get('content','[EMPTY]'))
print('reasoning_content:', str(c.get('message',{}).get('reasoning_content','[NONE]'))[:100])
\" 2>&1"
echo ""

# ── 0b. Check llama-server --reasoning flag ─────────
echo ""; echo "[ 0b ] llama-server launch args"
$SSH "ps aux | grep llama-server | grep -v grep | head -3"

# ── 1. Health ──────────────────────────────
echo ""; echo "[ 1 ] Health"
curl -sf "$HOST/api/health"
echo ""

# ── 2. Queue + stream (the core test) ──────
echo ""; echo "[ 2 ] Full chat round-trip (queue → stream)"
QRAW=$(curl -sf -X POST "$HOST/api/chat/queue" \
  -H "Content-Type: application/json" \
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"Say hello briefly.\"}],\"stream\":true,\"client_time\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}")
echo "  Queue response: $QRAW"
JOB=$(echo "$QRAW" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
echo "  job_id: $JOB"

if [ -z "$JOB" ]; then
  echo "  ERROR: no job_id returned — queue endpoint failed"
else
  echo "  Streaming (90s timeout, first 80 lines)..."
  STREAM_OUT=$(curl -s --max-time 130 "$HOST/api/chat/stream/$JOB")
  echo "$STREAM_OUT" | head -80
  TOKENS=$(echo "$STREAM_OUT" | grep -c '"token"')
  echo "  --- token lines received: $TOKENS ---"
fi

# ── 3. Server log around that request ──────
echo ""; echo "[ 3 ] amica-api uvicorn log (all output, last 40 lines)"
$SSH "echo $PASS | sudo -S journalctl -u amica-api -n 40 --no-pager --output=cat 2>/dev/null"

# ── 4. System prompt ───────────────────────
echo ""; echo "[ 4 ] System prompt char/token count"
curl -sf "$HOST/api/debug/prompt" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('chars:', d['char_count'])
prompt = d['prompt']
print('--- prompt ---')
print(prompt)
"

# ── 5. Event via "remind me to" ────────────
echo ""; echo "[ 5 ] Event: 'remind me to meet Karl tomorrow at 6pm'"
QRAW2=$(curl -sf -X POST "$HOST/api/chat/queue" \
  -H "Content-Type: application/json" \
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"remind me to meet Karl tomorrow at 6pm\"}],\"stream\":true,\"client_time\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}")
JOB2=$(echo "$QRAW2" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
curl -s --max-time 130 "$HOST/api/chat/stream/$JOB2" | grep '"token"' | head -5
echo ""
echo "  Events in memory:"
curl -sf "$HOST/api/memory" | python3 -c "
import json,sys
m=json.load(sys.stdin)
evts=m.get('upcoming_events',[])
for e in sorted(evts,key=lambda x:x.get('date','')):
    print(f'    {e.get(\"date\",\"?\")} | {e.get(\"description\",\"?\")}')
"

# ── 6. Event via trigger at end ─────────────
echo ""; echo "[ 6 ] Event: 'Karl visiting tomorrow at 6pm. Remind me.'"
QRAW3=$(curl -sf -X POST "$HOST/api/chat/queue" \
  -H "Content-Type: application/json" \
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"Karl visiting tomorrow at 6pm. Remind me.\"}],\"stream\":true,\"client_time\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}")
JOB3=$(echo "$QRAW3" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
curl -s --max-time 130 "$HOST/api/chat/stream/$JOB3" | grep '"token"' | head -5
echo ""
echo "  Events in memory (last 3):"
curl -sf "$HOST/api/memory" | python3 -c "
import json,sys
m=json.load(sys.stdin)
evts=sorted(m.get('upcoming_events',[]),key=lambda x:x.get('date',''))
for e in evts[-3:]:
    print(f'    {e.get(\"date\",\"?\")} | {e.get(\"description\",\"?\")}')
"

# ── 7. Person via "remember this" at end ───
echo ""; echo "[ 7 ] Person: 'Met Stacey my new neighbour. Remember this.'"
QRAW4=$(curl -sf -X POST "$HOST/api/chat/queue" \
  -H "Content-Type: application/json" \
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"Met Stacey my new neighbour. Remember this.\"}],\"stream\":true,\"client_time\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}")
JOB4=$(echo "$QRAW4" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
curl -s --max-time 130 "$HOST/api/chat/stream/$JOB4" | grep '"token"' | head -5
echo ""
echo "  People in memory (last 3):"
curl -sf "$HOST/api/memory" | python3 -c "
import json,sys
m=json.load(sys.stdin)
ppl=m.get('family_and_friends',[])
for p in ppl[-3:]:
    print(f'    {p.get(\"name\")} ({p.get(\"relation\")})')
"
