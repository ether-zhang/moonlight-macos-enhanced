#!/bin/bash
# Build the RC7 personal variant without installing an app or system helper.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [[ -z "${DEVELOPER_DIR:-}" && -d /Applications/Xcode.app/Contents/Developer ]]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi
export ARCHS="${ARCHS:-$(uname -m)}"
BUILD_DIR=${PERSONAL_BUILD_DIR:-"$ROOT/build-personal"}
OUTPUT="$ROOT/dist/personal"
DISPLAY_VERSION=${PERSONAL_DISPLAY_VERSION:-1.3.9-rc.7+sleepfix.1}
BUILD_NUMBER=${PERSONAL_BUILD_NUMBER:-1539}
cd "$ROOT"
xcodebuild -version

git -c core.hooksPath=/dev/null submodule update --init --recursive --depth 1
if git submodule status --recursive | /usr/bin/grep -Eq '^[-+U]'; then
  echo 'A submodule does not match its pinned commit' >&2
  exit 1
fi
git submodule foreach --recursive 'git diff --quiet HEAD --'
bash scripts/prepare_personal_dependencies.sh

xcodebuild -resolvePackageDependencies -project Moonlight.xcodeproj \
  -scheme 'Moonlight for macOS' -clonedSourcePackagesDirPath "$BUILD_DIR/SourcePackages" \
  -onlyUsePackageVersionsFromResolvedFile
xcodebuild -project Moonlight.xcodeproj -scheme 'Moonlight for macOS' \
  -configuration Release -derivedDataPath "$BUILD_DIR/DerivedData" \
  -clonedSourcePackagesDirPath "$BUILD_DIR/SourcePackages" \
  -destination 'generic/platform=macOS' -disableAutomaticPackageResolution -skipPackageUpdates \
  ARCHS="$ARCHS" ONLY_ACTIVE_ARCH=NO \
  CODE_SIGN_IDENTITY=- CODE_SIGNING_REQUIRED=NO CODE_SIGNING_ALLOWED=NO \
  BUILD_NUMBER="$BUILD_NUMBER" MARKETING_VERSION=1.3.9 \
  UPDATE_DISPLAY_VERSION="$DISPLAY_VERSION" UPDATE_CHANNEL=beta build

APP="$BUILD_DIR/DerivedData/Build/Products/Release/MoonlightEnhanced.app"
python3 scripts/sign_app_bundle.py "$APP" --entitlements Moonlight.entitlements \
  --extension-entitlements FileProviderExtension/FileProvider.entitlements
python3 - "$APP" <<'PY'
import pathlib, plistlib, sys
app = pathlib.Path(sys.argv[1])
info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
if 'SMPrivilegedExecutables' in info or any('AwdlPrivilegedHelper' in p.name for p in app.rglob('*')):
    raise SystemExit('The personal app must not contain an AWDL privileged helper.')
print('Verified personal app:', info['UpdateDisplayVersion'], info['CFBundleVersion'])
PY
mkdir -p "$OUTPUT"
rm -rf "$OUTPUT/MoonlightEnhanced.app"
ditto "$APP" "$OUTPUT/MoonlightEnhanced.app"
archive="MoonlightEnhanced-$DISPLAY_VERSION-$(printf '%s' "$ARCHS" | tr ' ' '_').zip"
ditto -c -k --sequesterRsrc --keepParent "$OUTPUT/MoonlightEnhanced.app" "$OUTPUT/$archive"
(cd "$OUTPUT" && shasum -a 256 "$archive" > SHA256SUMS)
printf '%s\n' "Personal app and ZIP: $OUTPUT"
