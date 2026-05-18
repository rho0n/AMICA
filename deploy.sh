#!/bin/bash
# Tries home WiFi first, then AMICA hotspot
for IP in 192.168.1.155 192.168.1.156 10.42.0.1; do
  if sshpass -p password ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no arduino@$IP "echo ok" &>/dev/null; then
    HOST=arduino@$IP
    echo "Connected via $IP"
    break
  fi
done
if [ -z "$HOST" ]; then echo "ERROR: Board not reachable on either IP"; exit 1; fi

sshpass -p password scp static/index.html $HOST:/home/arduino/amica/static/index.html && echo "index.html OK"
sshpass -p password scp memory_manager.py $HOST:/home/arduino/amica/memory_manager.py && echo "memory_manager.py OK"
sshpass -p password scp amica_server.py $HOST:/home/arduino/amica/amica_server.py && echo "amica_server.py OK"
sshpass -p password ssh $HOST "sudo systemctl restart amica-api" && echo "Restarted OK"
sleep 3
sshpass -p password ssh $HOST "curl -s http://localhost:5000/api/health" && echo ""
