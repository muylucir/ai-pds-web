#!/usr/bin/env bash
# Stage harness/ + rule/aiplc-rules/ into infra/build/harness/ with the
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

# Rules baked into the image. rule/aiplc-rules/ is tracked in the repo, so a
# plain checkout has it; the guard below catches a partial/damaged tree.
if [ ! -f "$REPO/rule/aiplc-rules/aws-aiplc-rules/core-workflow.md" ]; then
  echo "ERROR: rule/aiplc-rules/ missing — expected it in the repo checkout." >&2
  exit 1
fi
mkdir -p "$BUILD/aiplc-rules"
cp -R "$REPO/rule/aiplc-rules/." "$BUILD/aiplc-rules/"

echo "EXPECTED: $BUILD contains Dockerfile, *.py, aiplc-rules/"
ls -1 "$BUILD"
