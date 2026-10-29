# HomeNetWatch Android agent

An Android (TV) version of the Raspberry Pi agent. Version 1 is meant for a device such as an NVIDIA Shield: it enrolls
like a Pi, then runs a speed test and a continuous two-way G.711 voice stream against the server.

**Status: work in progress.** Enrollment (installer code, signed check-in, approval poll) and the setup page work.
The WireGuard tunnel, the voice stream and the speed test are not built yet.

## How enrollment works

1. Start the app. It shows an installer code and a setup address, for example `http://192.168.1.50:8080`.
2. On a phone on the same network, open that address and enter the server address and its TLS key pin (the same pin the Pi setup page asks for).
3. On the dashboard, register the installer code on the Enrollment page and approve the device, as for a Pi.
4. The app receives its token and tunnel settings.

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
