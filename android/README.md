# HomeNetWatch Android agent

An Android (TV) version of the Raspberry Pi agent. Version 1 is meant for a device such as an NVIDIA Shield: it enrolls
like a Pi, then runs a speed test and a continuous two-way G.711 voice stream against the server.

**Status: work in progress.** Enrollment (installer code, signed check-in, approval poll) and the setup page work.
The WireGuard tunnel, the voice stream and the speed test are not built yet.

## How enrollment works

1. Start the app. It shows an installer code and a setup address, for example `http://192.168.1.50:8080`.
2. On a phone on the same network, open that address and **name the device** (for example "Living Room Shield"). That name becomes the sensor's name on the dashboard.
3. On the dashboard, open Remote Agent Onboarding, enter the installer code (the name field there can hold any text) and register it.
4. The dashboard shows a **Setup key** (the server address and its TLS key pins in one string) with a Copy button. Paste it into the setup page on the phone and save.
5. Approve the device on the dashboard, as for a Pi. The app receives its token and tunnel settings.

The app sends `platform: android` and one WireGuard key. The server gives it one tunnel and does not assign the Pi's HTTP and ping checks.

## Build

Needs JDK 17 and the Android SDK (`ANDROID_HOME`).

```bash
cd android
./gradlew testDebugUnitTest      # includes a check that signatures match the server's library
./gradlew assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
```

Install on a device or emulator with `adb install -r app/build/outputs/apk/debug/app-debug.apk`.
Nothing in this folder holds a secret: the server address, TLS pin and keys are entered on the device.
