#!/usr/bin/env bash
# setup.sh — Install Minutes meeting recorder
# Idempotent: safe to re-run
set -euo pipefail

echo "=== Minutes Setup ==="
echo ""
echo "⚠️  This will install software from github.com/silverstein/homebrew-tap"
echo "   Verify this is the expected source before proceeding."
echo ""

# Check macOS version (ScreenCaptureKit requires 15+)
MACOS_VERSION=$(sw_vers -productVersion 2>/dev/null || echo "0")
if [[ "$(echo "$MACOS_VERSION" | cut -d. -f1)" -lt 15 ]]; then
    echo "⚠️  macOS 15+ required for ScreenCaptureKit. Detected: $MACOS_VERSION"
    echo "   Recording may not capture system audio."
fi

# Install CLI
if command -v minutes &>/dev/null; then
    echo "✅ minutes CLI already installed ($(minutes --version 2>/dev/null || echo 'unknown'))"
else
    echo "📦 Installing minutes CLI..."
    if ! brew tap | grep -q silverstein/tap; then
        brew tap silverstein/tap
    fi
    brew install minutes
    echo "✅ minutes CLI installed"
fi

# Install desktop app
if [ -d "/Applications/Minutes.app" ]; then
    echo "✅ Minutes desktop app already installed"
else
    echo "📦 Installing Minutes desktop app..."
    brew install --cask silverstein/tap/minutes
    echo "✅ Minutes desktop app installed"
fi

# Download whisper model
if minutes health 2>/dev/null | grep -q "model.*ok"; then
    echo "✅ Whisper model already downloaded"
else
    echo "📦 Downloading whisper model (466MB)..."
    minutes setup --model small
    echo "✅ Whisper model downloaded"
fi

# Permissions reminder
echo ""
echo "=== Permissions Required ==="
echo "System Settings → Privacy & Security → Screen Recording → enable Minutes"
echo "System Settings → Privacy & Security → Microphone → enable Minutes"
echo ""
echo "After granting permissions, verify with: minutes health"
echo "=== Setup Complete ==="
