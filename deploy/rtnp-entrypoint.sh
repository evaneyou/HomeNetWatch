#!/bin/sh
set -eu
# Data is UDP 8998 on the public IPv4 address. Port 8999 is not published on
# the host. RTNP 0.2.1 listens for control on the bind address (every address
# in this network namespace) and reads one hex SRTP key, not a directory.
while [ ! -s /var/lib/hns-rtnp/key ]; do
  echo "waiting for the RTNP control key"
  sleep 1
done
while [ ! -d /var/lib/hns-rtnp/keys ] || [ -z "$(ls -A /var/lib/hns-rtnp/keys 2>/dev/null || true)" ]; do
  echo "waiting for an SRTP key"
  sleep 1
done
count=0
keyfile=
for candidate in /var/lib/hns-rtnp/keys/*; do
  if [ -f "$candidate" ]; then
    count=$((count + 1))
    keyfile=$candidate
  fi
done
if [ "$count" -ne 1 ]; then
  echo "RTNP 0.2.1 supports one SRTP key; found $count" >&2
  exit 1
fi
# The server publishes a raw 30-byte key. 0.2.1 wants 60 hex characters.
# Do not print the key.
od -An -tx1 -v "$keyfile" | tr -d ' \n' > /tmp/rtnp-srtp.key
printf '\n' >> /tmp/rtnp-srtp.key
chmod 400 /tmp/rtnp-srtp.key
size=$(wc -c < /tmp/rtnp-srtp.key | tr -d ' ')
if [ "$size" -ne 61 ]; then
  echo "SRTP key is not 30 bytes" >&2
  exit 1
fi
exec rtnp-recv \
  --bind 0.0.0.0 \
  --udp-port 8998 \
  --no-tcp \
  --control-port 8999 \
  --allow-reverse \
  --key-file /var/lib/hns-rtnp/key \
  --srtp-key-file /tmp/rtnp-srtp.key \
  --report-interval 0s
