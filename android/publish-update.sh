#!/usr/bin/env bash
# Publishes a CI-built APK as the newest Android agent, if its version code is higher than the one published.
# Each version gets its own release (android-v<version>, the APK); the android-latest release holds latest.json,
# which the app reads when it starts. Usage: android/publish-update.sh <apk>. Needs gh (GH_TOKEN), jq and the
# Android build tools (ANDROID_HOME).
set -euo pipefail

apk=$1
repo=${GITHUB_REPOSITORY:?}
aapt2=$(ls "$ANDROID_HOME"/build-tools/*/aapt2 | tail -1)
badging=$("$aapt2" dump badging "$apk" | head -1)
code=$(sed -n "s/.*versionCode='\([0-9]*\)'.*/\1/p" <<<"$badging")
name=$(sed -n "s/.*versionName='\([^']*\)'.*/\1/p" <<<"$badging")
test -n "$code" && test -n "$name"

published=$(curl -fsSL "https://github.com/$repo/releases/download/android-latest/latest.json" | jq -r .versionCode) || published=0
if [ "$code" -le "$published" ]; then
  echo "Version $name ($code) is not newer than the published $published; nothing to publish."
  exit 0
fi

tag="android-v$name"
file="homenetwatch-agent-$name.apk"
cp "$apk" "$file"
sha=$(sha256sum "$file" | cut -d' ' -f1)

# The APK's release first, so latest.json never points at a file that does not exist yet.
gh release create "$tag" "$file" --repo "$repo" --target "$GITHUB_SHA" --latest=false \
  --title "Android agent $name" \
  --notes "HomeNetWatch Android agent $name (version code $code). The app offers it on the TV when it starts; see android/README.md. SHA-256: $sha"

jq -n --argjson code "$code" --arg name "$name" --arg sha "$sha" \
  --arg apk "https://github.com/$repo/releases/download/$tag/$file" \
  '{versionCode: $code, versionName: $name, apk: $apk, sha256: $sha}' > latest.json
if ! gh release view android-latest --repo "$repo" >/dev/null 2>&1; then
  gh release create android-latest --repo "$repo" --target "$GITHUB_SHA" --latest=false \
    --title "Android agent: newest version" \
    --notes "Holds latest.json, which the Android agent reads to find its newest version. The APKs are on the android-v releases."
fi
gh release upload android-latest latest.json --repo "$repo" --clobber
echo "Published $name ($code)."
