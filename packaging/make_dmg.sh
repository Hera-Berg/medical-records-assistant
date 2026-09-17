#!/bin/sh
# Wrap the built app in a disk image with a shortcut to Applications beside it.
#
#   packaging/make_dmg.sh "dist/Health Record.app" out/Health-Record-1.0.0-macos-arm64.dmg
#
# hdiutil ships with macOS, so this needs nothing installed. The app inside is
# signed ad hoc only — no certificate — which Apple Silicon requires before it
# will run any code at all. The check below fails the build if the signature
# is broken, because a broken one makes macOS say the app "is damaged", and the
# only fix for that is a terminal command.
set -eu
app="$1"
out="$2"

codesign --force --deep --sign - "$app"
codesign --verify --deep --strict --verbose=2 "$app"

stage="$(mktemp -d)"
cp -R "$app" "$stage/"
ln -s /Applications "$stage/Applications"
mkdir -p "$(dirname "$out")"
hdiutil create -volname "Health Record" -srcfolder "$stage" -fs HFS+ -format UDZO -ov "$out"
rm -rf "$stage"
