#!/bin/bash
echo "Setting static IP so DHCP failure doesn't block us..."
sudo networksetup -setmanual Wi-Fi 10.42.0.10 255.255.255.0 10.42.0.1
echo "Done. Now manually join the AMICA WiFi in your menu bar."
echo "This script will keep retrying until it gets through..."
echo ""

cd /Users/rhoonracoon/AMICA

while true; do
  if sshpass -p password scp -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
      memory_manager.py amica_server.py \
      arduino@10.42.0.1:/home/arduino/amica/ 2>/dev/null; then
    echo "Files copied OK!"
    sshpass -p password ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
      arduino@10.42.0.1 "sudo systemctl restart amica-api" 2>/dev/null
    echo "Service restarted. Restoring DHCP..."
    sudo networksetup -setdhcp Wi-Fi
    echo "Done! Reconnect to AMICA WiFi normally now."
    exit 0
  fi
  printf "."
  sleep 2
done
