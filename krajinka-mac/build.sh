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
# témy Desklandu (GPL-3.0, pozri Resources/Deskland/PRECITAJ.md)
cp -R Resources/Deskland "$APP/Contents/Resources/Deskland"
# ikona aplikácie
iconutil -c icns Resources/AppIcon.iconset -o "$APP/Contents/Resources/AppIcon.icns" 2>/dev/null || true

# Ad-hoc podpis ("-") stačí na lokálne spustenie. Pozor: po každom novom
# ad-hoc builde si macOS pýta povolenie Accessibility nanovo (iný podpis).
codesign --force --sign "${SIGN_IDENTITY:--}" "$APP"

# Nový ad-hoc podpis = pre macOS iná aplikácia. Staré povolenie Prístupnosť
# by v nastaveniach svietilo, ale neplatilo – preto ho zrušíme a macOS sa
# pri štarte opýta nanovo (stačí zapnúť prepínač pri Krajinke).
if [ -z "${SIGN_IDENTITY:-}" ]; then
  pkill -x Krajinka >/dev/null 2>&1 || true
  tccutil reset Accessibility app.krajinka.mac >/dev/null 2>&1 || true
fi

# Inštalácia medzi aplikácie: /Applications (ak sa dá), inak ~/Applications
DEST="/Applications"
if [ ! -w "$DEST" ]; then DEST="$HOME/Applications"; mkdir -p "$DEST"; fi
rm -rf "$DEST/Krajinka.app"
ditto "$APP" "$DEST/Krajinka.app"
echo "Nainštalované: $DEST/Krajinka.app"
echo "Odteraz ju spúšťaj z Launchpadu, Spotlightu alebo z priečinka Aplikácie."
open "$DEST/Krajinka.app"
