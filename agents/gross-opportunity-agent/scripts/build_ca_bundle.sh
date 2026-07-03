#!/usr/bin/env bash
# Build a CA bundle that trusts this machine's corporate TLS-interception proxy.
# The proxy root is in macOS Keychain but not in certifi/system PEM, so httpx/anthropic
# and the Google clients fail cert verification. This merges certifi + macOS roots.
# Usage: scripts/build_ca_bundle.sh [output_path]
set -euo pipefail
OUT="${1:-$HOME/.goa-ca-bundle.pem}"
VENV_PY="$(dirname "$0")/../.venv/bin/python"
CERTIFI="$("$VENV_PY" -c 'import certifi; print(certifi.where())')"
TMP="$(mktemp)"
security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain > "$TMP" 2>/dev/null || true
security find-certificate -a -p /Library/Keychains/System.keychain >> "$TMP" 2>/dev/null || true
security find-certificate -a -p "$HOME/Library/Keychains/login.keychain-db" >> "$TMP" 2>/dev/null || true
cat "$CERTIFI" "$TMP" > "$OUT"
rm -f "$TMP"
echo "Wrote $OUT ($(grep -c 'BEGIN CERT' "$OUT") certs). Point SSL_CERT_FILE + REQUESTS_CA_BUNDLE at it."
