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

The TV shows an installer code and a setup address, for example `http://192.168.1.50:8080`. Open that address on a
phone or computer on the same network. The page is a checklist:

1. **Name this device**, for example "Living Room Shield". This becomes the sensor's name on the dashboard.
2. **Register the code** on the dashboard's Remote Agent Onboarding page (Copy button on the page).
3. **Paste the setup key** shown on the code's card on the dashboard.
4. **Approve** it on the dashboard. The page updates by itself and shows Setup complete.

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
