#!/usr/bin/env bash
# Builds the traffic sender (rtnp-send, from evaneyou/RTN_PackGen) for Android and puts it where the app
# packs native libraries. Android only installs files named lib*.so from there, so it is renamed
# librtnp_send.so; the app runs it from its native library folder. The binaries are not committed.
#
# Needs Rust, cargo-ndk and the Android NDK (ANDROID_NDK_HOME). Run on Linux or WSL:
#   ./build-sender.sh            # arm64 for the Shield, x86 for the Android TV emulator
set -euo pipefail

TAG="${RTNP_TAG:-v0.2.1}"
SRC="${RTNP_SRC:-$HOME/RTN_PackGen}"
OUT="$(cd "$(dirname "$0")" && pwd)/app/src/main/jniLibs"

if [ ! -d "$SRC/.git" ]; then
  git clone -q https://github.com/evaneyou/RTN_PackGen "$SRC"
fi
git -C "$SRC" fetch -q --tags
git -C "$SRC" checkout -q "$TAG"

for abi in arm64-v8a x86; do
  case "$abi" in
    arm64-v8a) triple=aarch64-linux-android ;;
    x86) triple=i686-linux-android ;;
  esac
  (cd "$SRC" && cargo ndk -t "$abi" build --release --bin rtnp-send)
  mkdir -p "$OUT/$abi"
  cp "$SRC/target/$triple/release/rtnp-send" "$OUT/$abi/librtnp_send.so"
  echo "$abi: $(du -h "$OUT/$abi/librtnp_send.so" | cut -f1)"
done
