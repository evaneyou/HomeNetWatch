# HomeNetWatch Android agent

An Android (TV) version of the Raspberry Pi agent, for a device such as an NVIDIA Shield. It enrolls like a Pi, then
runs a continuous two-way G.711 voice stream and the Speed Test over Wi-Fi, and reports to the server.

**Status:** enrollment, the WireGuard tunnel, health reports, the voice stream, the Speed Test and start on boot work
on the Android TV emulator. It has not run on a real Shield yet.

## What it does

- **Tunnel:** one WireGuard tunnel from the settings the server issues at approval. It only routes the server's
  tunnel address (`10.7.0.1`): health reports, the voice stream's control channel and the Speed Test go through it.
  Android asks once on the TV ("Connection request") before an app may open a VPN; choose OK.
- **Health report** every minute (or as set on the dashboard), which marks the agent online.
- **Voice stream:** runs the bundled `rtnp-send` for each flow the dashboard turns on (Wi-Fi only) and relays each
  interval to the server. The voice packets go straight to the server's public address over Wi-Fi, outside the
  tunnel, as on the Pi. A flow that reports nothing for 3 intervals, or loses every packet for 6, is restarted.
- **Speed Test:** started from the dashboard's Speed Test page; LibreSpeed over the tunnel.
- **Starts by itself** after a reboot, a power cut or an app update.

## Install on the Shield

1. Download the APK: on GitHub, open **Actions → Android agent**, pick the latest green run and download the
   `homenetwatch-agent-apk` artifact (a zip holding `app-debug.apk`).
2. Sideload it, either:
   - **adb:** turn on Developer options and Network debugging on the Shield, then
     `adb connect <shield-address>` and `adb install -r app-debug.apk`; or
   - **Downloader app:** put the APK somewhere the Shield can reach and open it with the Downloader app.
3. Open HomeNetWatch from the Shield's apps and follow the setup below.

Each CI build is signed with that run's debug key, so a newer build may refuse to install over an older one. Then
uninstall first and set the device up again (press Menu on the TV to start over).

## Setup

Nothing is typed on the TV, and the phone does not need to be on the same network:

1. **Scan the QR code** on the TV. It opens the dashboard's Remote Agent Onboarding page (sign in as an admin) with
   the installer code filled in.
2. Give the device a name, for example "Living Room Shield", and tap **Register**.
3. The TV collects its setup key and name from the server by itself and checks in, a few seconds later.
   Tap **Approve** on its card.

The QR code holds the dashboard address built into the app (`-PhnwDashboard=...` at build time; it defaults to
`https://live.homenetwatch.com`), the installer code and a random pairing token. Registering from that link stores the
token's hash with the code. Until it is set up, the device asks `POST /api/v1/agents/pairing` on the dashboard's site
every 5 seconds, over ordinary HTTPS, and only takes a setup key for that dashboard. Without a phone camera, open the
setup address the TV shows (for example `http://192.168.1.50:8080`) from a computer and follow the checklist there,
pasting the setup key.

Then allow the VPN connection on the TV, and turn on **Voice G.711** for the device (path **Wi-Fi**) in the
dashboard's Real-Time Application Health settings.

The app signs its check-in, including `platform: android` and the name. The server gives it one tunnel, does not
assign the Pi's HTTP and ping checks, and accepts only Wi-Fi tests for it.

## Build

Needs JDK 17 and the Android SDK (`ANDROID_HOME`). The traffic sender is built from
[RTN_PackGen](https://github.com/evaneyou/RTN_PackGen) and is not committed; build it once (Linux or WSL, with Rust,
`cargo-ndk` and the NDK in `ANDROID_NDK_HOME`):

```bash
cd android
./build-sender.sh                # app/src/main/jniLibs/{arm64-v8a,x86}/librtnp_send.so
./gradlew testDebugUnitTest      # includes checks that signatures and the sender's arguments match the Pi's
./gradlew assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
```

Install on a device or emulator with `adb install -r app/build/outputs/apk/debug/app-debug.apk`.
Nothing in this folder holds a secret: the server address, TLS pins and keys are entered on, or made by, the device.
