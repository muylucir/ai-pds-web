#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: deploy.sh --asset-bucket BUCKET [options]

Options:
  --stack-name NAME       CloudFormation stack name (default: pathfinder)
  --region REGION         AWS region (default: AWS_REGION or ap-northeast-2)
  --profile PROFILE       AWS CLI profile
  --instance-type TYPE    EC2 instance type (default: m7i.2xlarge)
  --seed-password VALUE   Override the demo seed password
  -h, --help              Show this help
EOF
}

ASSET_BUCKET=""
STACK_NAME="pathfinder"
REGION="${AWS_REGION:-ap-northeast-2}"
PROFILE=""
INSTANCE_TYPE="m7i.2xlarge"
SEED_PASSWORD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --asset-bucket) ASSET_BUCKET="$2"; shift 2 ;;
    --stack-name) STACK_NAME="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --instance-type) INSTANCE_TYPE="$2"; shift 2 ;;
    --seed-password) SEED_PASSWORD="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$ASSET_BUCKET" ]]; then
  echo "--asset-bucket is required" >&2
  usage >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
ZIP_PATH="$TMP_DIR/pathfinder-app.zip"
DIGEST="$(python3 "$SCRIPT_DIR/package_app.py" "$ZIP_PATH")"
ASSET_KEY="pathfinder/app-assets/${DIGEST}.zip"

AWS_ARGS=(--region "$REGION")
if [[ -n "$PROFILE" ]]; then
  AWS_ARGS+=(--profile "$PROFILE")
fi

aws "${AWS_ARGS[@]}" s3 cp "$ZIP_PATH" "s3://$ASSET_BUCKET/$ASSET_KEY" --sse AES256 --only-show-errors

PARAMETERS=(
  "AppAssetBucket=$ASSET_BUCKET"
  "AppAssetKey=$ASSET_KEY"
  "InstanceType=$INSTANCE_TYPE"
)
if [[ -n "$SEED_PASSWORD" ]]; then
  PARAMETERS+=("SeedPassword=$SEED_PASSWORD")
fi

aws "${AWS_ARGS[@]}" cloudformation deploy \
  --template-file "$SCRIPT_DIR/pathfinder.yaml" \
  --stack-name "$STACK_NAME" \
  --s3-bucket "$ASSET_BUCKET" \
  --s3-prefix pathfinder/cloudformation \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides "${PARAMETERS[@]}" \
  --no-fail-on-empty-changeset

aws "${AWS_ARGS[@]}" cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs' \
  --output table
