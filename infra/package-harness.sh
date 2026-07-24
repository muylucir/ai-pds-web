#!/usr/bin/env bash
# Stage harness/ + files/aiplc-rules/ into infra/build/harness/ with the
# Dockerfile at the root, so the CDK aws_s3_assets.Asset zips exactly what
# MicrovmImage's CodeArtifact expects (Dockerfile at zip root).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
BUILD="$HERE/build/harness"

rm -rf "$BUILD"
mkdir -p "$BUILD"

# harness code (excluding tests / caches) at the build root
cp "$REPO/harness/Dockerfile" "$BUILD/Dockerfile"
cp "$REPO/harness/requirements.txt" "$BUILD/requirements.txt"
cp "$REPO/harness/"*.py "$BUILD/"

# rules baked into the image (repo files/ is gitignored reference material —
# the drill machine MUST have files/aiplc-rules/ present, else this fails).
if [ ! -f "$REPO/files/aiplc-rules/aws-aiplc-rules/core-workflow.md" ]; then
  echo "ERROR: files/aiplc-rules/ missing (gitignored reference material). Populate it on the drill machine." >&2
  exit 1
fi
mkdir -p "$BUILD/aiplc-rules"
cp -R "$REPO/files/aiplc-rules/." "$BUILD/aiplc-rules/"

echo "EXPECTED: $BUILD contains Dockerfile, *.py, aiplc-rules/"
ls -1 "$BUILD"
