#!/bin/bash
# Verify the binary dependencies and compile FFmpeg/dav1d for this personal fork.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CACHE=${PERSONAL_DEPS_BUILD_DIR:-"$ROOT/build-personal-deps"}
export ARCHS="${ARCHS:-$(uname -m)}"
read -r -a build_archs <<< "$ARCHS"
JOBS=${BUILD_JOBS:-$(sysctl -n hw.ncpu)}
for arch in "${build_archs[@]}"; do
  case "$arch" in arm64|x86_64) ;; *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; esac
done
for tool in curl git codesign ditto shasum xcrun xcodebuild make meson ninja; do
  command -v "$tool" >/dev/null || { echo "Missing build tool: $tool" >&2; exit 1; }
done
mkdir -p "$CACHE/downloads" "$ROOT/xcframeworks"

# This mutable upstream release URL is accepted only for the reviewed archive.
VENDOR_URL=https://github.com/coofdy/moonlight-mobile-deps/releases/download/latest/moonlight-apple-xcframeworks.zip
VENDOR_SHA256=bfb271788a41664ae1764115907e544fb160a175412d5ebfb96bc29a5deb97cb
ARCHIVE="$CACHE/downloads/moonlight-apple-xcframeworks.zip"
if [[ ! -f "$ARCHIVE" ]]; then
  curl --proto '=https' --tlsv1.2 --fail --location --retry 2 "$VENDOR_URL" -o "$ARCHIVE.partial"
  printf '%s  %s\n' "$VENDOR_SHA256" "$ARCHIVE.partial" | shasum -a 256 -c -
  mv "$ARCHIVE.partial" "$ARCHIVE"
fi
printf '%s  %s\n' "$VENDOR_SHA256" "$ARCHIVE" | shasum -a 256 -c -
# ditto preserves the macOS framework's versioned symbolic links.
ditto -x -k "$ARCHIVE" "$CACHE/vendor-xcframeworks"
requirement='=anchor apple generic and certificate leaf[subject.OU] = "KHSNQKXT55"'
for name in Opus SDL2; do
  framework="$CACHE/vendor-xcframeworks/$name.xcframework"
  codesign --verify --strict -R "$requirement" "$framework"
  rm -rf "$ROOT/xcframeworks/$name.xcframework"
  ditto "$framework" "$ROOT/xcframeworks/$name.xcframework"
done

# Keep FFmpeg's AV1 CBS private headers and static library on the same revision.
# The reviewed archive's older FFmpeg is deliberately not copied into the project.
FFMPEG_TAG=n6.1.6
FFMPEG_COMMIT=f1e3a2bf7a2f2cde936d1ed97f09a26853d20125
FFMPEG_SOURCE="$CACHE/ffmpeg-source"
if [[ ! -d "$FFMPEG_SOURCE/.git" ]]; then
  git -c core.hooksPath=/dev/null clone --depth 1 --single-branch --branch "$FFMPEG_TAG" \
    https://github.com/FFmpeg/FFmpeg.git "$FFMPEG_SOURCE"
fi
[[ "$(git -C "$FFMPEG_SOURCE" rev-parse HEAD)" == "$FFMPEG_COMMIT" ]] || {
  echo "FFmpeg source does not match the reviewed commit" >&2; exit 1;
}
git -C "$FFMPEG_SOURCE" fsck --no-reflogs
git -C "$FFMPEG_SOURCE" diff --quiet HEAD --
ffmpeg_libraries=()
first_install=""
for arch in "${build_archs[@]}"; do
  build="$CACHE/ffmpeg-build-$arch"
  install="$CACHE/ffmpeg-install-$arch"
  mkdir -p "$build"
  (
    cd "$build"
    "$FFMPEG_SOURCE/configure" --prefix="$install" --arch="$arch" \
      --target-os=darwin --enable-cross-compile \
      --extra-cflags="-arch $arch -mmacosx-version-min=12.0" \
      --extra-ldflags="-arch $arch -mmacosx-version-min=12.0" \
      --disable-all --disable-autodetect --disable-x86asm --disable-network \
      --enable-avcodec --enable-avformat --enable-muxer=flv --enable-decoder=av1 \
      --disable-programs --disable-doc --disable-debug --enable-static --disable-shared --enable-pic
    make -j "$JOBS"
    make install
  )
  cp "$FFMPEG_SOURCE/libavcodec/cbs.h" "$FFMPEG_SOURCE/libavcodec/cbs_av1.h" \
    "$FFMPEG_SOURCE/libavcodec/av1.h" "$install/include/libavcodec/"
  xcrun libtool -static -o "$install/lib/libffmpeg.a" \
    "$install/lib/libavcodec.a" "$install/lib/libavformat.a" "$install/lib/libavutil.a"
  ffmpeg_libraries+=("$install/lib/libffmpeg.a")
  [[ -n "$first_install" ]] || first_install="$install"
done
mkdir -p "$CACHE/ffmpeg-combined"
xcrun lipo -create "${ffmpeg_libraries[@]}" -output "$CACHE/ffmpeg-combined/libffmpeg.a"
rm -rf "$CACHE/FFmpeg.xcframework"
xcodebuild -create-xcframework -library "$CACHE/ffmpeg-combined/libffmpeg.a" \
  -headers "$first_install/include" -output "$CACHE/FFmpeg.xcframework"
rm -rf "$ROOT/xcframeworks/FFmpeg.xcframework"
mv "$CACHE/FFmpeg.xcframework" "$ROOT/xcframeworks/FFmpeg.xcframework"

"$ROOT/scripts/build_dav1d.sh"
printf '%s\n' "Verified dependencies prepared for: $ARCHS"
