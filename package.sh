#!/usr/bin/env bash
# ==============================================================================
# BMSanta Release Packager
# Packages distribution tarball and SHA256 checksum for easy offline installation
# ==============================================================================

set -euo pipefail

VERSION="0.1.0"
DIST_DIR="dist"
ARCHIVE_NAME="bmsanta-v${VERSION}.tar.gz"

echo "[*] Packaging BMSanta v${VERSION}..."
mkdir -p "${DIST_DIR}"

tar -czvf "${DIST_DIR}/${ARCHIVE_NAME}" \
    --exclude='.git' \
    --exclude='.gitignore' \
    --exclude='*.img' \
    --exclude='*.img.gz' \
    --exclude='*.iso' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='dist' \
    --exclude='.DS_Store' \
    --exclude='zero.tmp' \
    -C .. bmsanta

cd "${DIST_DIR}"
shasum -a 256 "${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256"
cd ..

echo "═════════════════════════════════════════════════════════════════"
echo "  Package created: ${DIST_DIR}/${ARCHIVE_NAME}"
echo "  Checksum:        ${DIST_DIR}/${ARCHIVE_NAME}.sha256"
echo "  Size:            $(du -h "${DIST_DIR}/${ARCHIVE_NAME}" | awk '{print $1}')"
echo "═════════════════════════════════════════════════════════════════"
