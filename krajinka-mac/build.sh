#!/usr/bin/env bash
# Zostaví Krajinka.app zo Swift balíčka (stačia Xcode Command Line Tools).
# Použitie:  ./build.sh            -> release build do build/Krajinka.app
#            SIGN_IDENTITY="Apple Development: Meno (TEAMID)" ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

CONFIG="${CONFIG:-release}"
swift build -c "$CONFIG"
BIN_DIR="$(swift build -c "$CONFIG" --show-bin-path)"

APP="build/Krajinka.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/Krajinka" "$APP/Contents/MacOS/Krajinka"
cp Resources/Info.plist "$APP/Contents/Info.plist"

# Ad-hoc podpis ("-") stačí na lokálne spustenie. Pozor: po každom novom
# ad-hoc builde si macOS pýta povolenie Accessibility nanovo (iný podpis).
codesign --force --sign "${SIGN_IDENTITY:--}" "$APP"

echo "Hotovo: $APP"
echo "Spustenie: open \"$APP\""
