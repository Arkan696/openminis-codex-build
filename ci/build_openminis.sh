#!/usr/bin/env bash
# Run only in an authorized disposable macOS runner. No user runtime is accepted.
set -euo pipefail
RUNTIME_PROBE=1
[[ "${1:-}" != "--no-codex-probe" ]] || RUNTIME_PROBE=0
ROOT="${GITHUB_WORKSPACE:?Set GITHUB_WORKSPACE to a NEW build workspace}"
UPSTREAM="$ROOT/build/OpenMinis"
DERIVED="$ROOT/build/DerivedData"
ARCHIVE="$ROOT/build/OpenMinisCodex.xcarchive"
mkdir -p "$ROOT/build"
exec > >(tee "$ROOT/build/mac-build.log") 2>&1
EXPECTED=1a71df0f381cefde80bf912b723de6f9831bc177
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]]
# Refuse user runtime/credentials before starting any public build.
python3 "$ROOT/scripts/check_public_build_inputs.py"
xcodebuild -version
xcrun --sdk iphoneos --show-sdk-version
# setup-go selects the exact version before this step. Homebrew must not
# upgrade it: the pinned go-json-experiment dependency breaks with Go 1.27.
brew install ninja llvm lld libarchive pkg-config meson
export PATH="$(brew --prefix llvm)/bin:$(brew --prefix lld)/bin:$PATH"
export GOTOOLCHAIN=local
export GOEXPERIMENT=nojsonv2
for cmd in meson ninja clang ld.lld pkg-config go; do command -v "$cmd"; done
python3 -c 'import subprocess;v=subprocess.check_output(["go","env","GOVERSION"],text=True).strip();assert v=="go1.25.5", "Expected go1.25.5, got "+v'
go version
if ! xcrun --sdk iphoneos metal --version; then
  xcodebuild -downloadComponent MetalToolchain
  xcrun --sdk iphoneos metal --version
fi
# Compile the real overlay first, independently of the full native dependencies.
python3 "$ROOT/scripts/verify_apple.py" --out "$ROOT/build/EngineCompile" --mode build --execute
# Fetch ONLY the pinned public revision, not another revision's submodules.
git init "$UPSTREAM"
git -C "$UPSTREAM" remote add origin https://github.com/OpenMinis/OpenMinis.git
git -C "$UPSTREAM" fetch --depth=1 origin "$EXPECTED"
git -C "$UPSTREAM" checkout --detach FETCH_HEAD
git -C "$UPSTREAM" submodule update --init --recursive deps/ish
# Apply while the checkout is clean. The next step creates customization locally.
python3 "$ROOT/scripts/apply_overlay.py" --repo "$UPSTREAM" --apply
PROBE_ARGS=()
if [ "$RUNTIME_PROBE" -eq 0 ]; then PROBE_ARGS+=(--disable-runtime-probe); fi
python3 "$ROOT/scripts/prepare_ipa_build.py" --repo "$UPSTREAM" --disposable-copy "${PROBE_ARGS[@]}"
cd "$UPSTREAM"
# Fail before rebuilding the large native libraries if the pinned JSON module
# cannot compile with the selected Go toolchain. This probe builds, never runs.
(
  cd deps/rclone-mobile
  echo '== Go JSON dependency compile preflight (no execution) =='
  go build -mod=readonly github.com/go-json-experiment/json
)
./deps/build_lame.sh
./deps/build_ffmpeg.sh
./deps/build_ish.sh release
./deps/prepare_alpine_rootfs.sh
./deps/build_rclone_ios.sh
xcodebuild -project src/ios/Minis.xcodeproj -scheme Minis -configuration Release \
  -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath "$DERIVED" -archivePath "$ARCHIVE" \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= DEVELOPMENT_TEAM= \
  IPHONEOS_DEPLOYMENT_TARGET=16.5 CURRENT_PROJECT_VERSION=2 ARCHS=arm64 ONLY_ACTIVE_ARCH=NO \
  archive | tee "$ROOT/build/xcode-archive.log"
APP="$ARCHIVE/Products/Applications/Minis.app"
test -d "$APP" && test -s "$APP/Minis"
python3 "$ROOT/scripts/public_build_artifact.py" --app "$APP" --prepared "$UPSTREAM/CODEX-IPA-BUILD-PREPARED.json" --out "$ROOT/build/public-artifact"
echo 'Public source compiled. No Codex core/credential was uploaded or executed.'
