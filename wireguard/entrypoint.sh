#!/bin/sh
set -eu
cd /etc/wireguard
umask 077
if [ ! -f server.key ]; then
  wg genkey | tee server.key | wg pubkey > server.pub
  chmod 600 server.key
fi
if [ ! -f server.pub ]; then
  wg pubkey < server.key > server.pub
fi
touch peers.conf

build_conf() {
  cat > wg0.conf <<EOF
[Interface]
PrivateKey = $(cat server.key)
Address = 10.7.0.1/24, 10.7.1.1/24
ListenPort = 51820

$(cat peers.conf)
EOF
}

start_interface() {
  if wg-quick up wg0; then
    return 0
  fi
  echo "Kernel WireGuard is unavailable. Starting the userspace tunnel."
  wireguard-go wg0
  wg-quick strip wg0 > /tmp/wg0.stripped
  wg setconf wg0 /tmp/wg0.stripped
  ip addr add 10.7.0.1/24 dev wg0
  ip addr add 10.7.1.1/24 dev wg0
  ip link set up dev wg0
}

write_handshakes() {
  wg show wg0 latest-handshakes > handshakes.tmp 2>/dev/null && mv handshakes.tmp handshakes
}

build_conf
start_interface
# Peers only reach 10.7.0.1, never each other.
iptables -C FORWARD -i wg0 -o wg0 -j DROP 2>/dev/null \
  || iptables -A FORWARD -i wg0 -o wg0 -j DROP \
  || echo "Could not add the rule that blocks peer-to-peer traffic"
(while true; do socat TCP-LISTEN:8847,bind=10.7.0.1,fork,reuseaddr TCP:${SERVER_PROXY:-server:8847} || true; sleep 1; done) &
(while true; do socat TCP-LISTEN:8847,bind=10.7.1.1,fork,reuseaddr TCP:${SERVER_PROXY:-server:8847} || true; sleep 1; done) &
(while true; do socat TCP-LISTEN:8080,bind=10.7.0.1,fork,reuseaddr TCP:${LIBRESPEED_PROXY:-librespeed:8080} || true; sleep 1; done) &
(while true; do socat TCP-LISTEN:8080,bind=10.7.1.1,fork,reuseaddr TCP:${LIBRESPEED_PROXY:-librespeed:8080} || true; sleep 1; done) &

stamp=""
while true; do
  now=$(stat -c %Y peers.conf 2>/dev/null || echo 0)
  if [ "$now" != "$stamp" ]; then
    build_conf
    wg-quick strip wg0 > /tmp/wg0.stripped
    wg syncconf wg0 /tmp/wg0.stripped || true
    stamp=$now
  fi
  write_handshakes || true
  sleep 2
done
