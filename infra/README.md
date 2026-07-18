# Pathfinder Drill Infra (CDK, ap-northeast-1)

Single stack `PathfinderDrillStack`: MicroVM image (harness + aiplc-rules baked),
build/execution IAM roles (execution role = Bedrock-only, no S3), artifacts bucket.

## Synth / deploy
```bash
npm ci
./package-harness.sh          # stages harness/ + files/aiplc-rules/ into build/harness/
npx cdk synth                 # validates the stack (no AWS creds needed)
npx cdk deploy                # creates the image (async CREATING->CREATED) + roles + bucket
```
Feed the CfnOutputs into the drill env: `ImageArn`→`PATHFINDER_VM_IMAGE_ID`,
`ExecutionRoleArn`→`PATHFINDER_VM_ROLE_ARN`, `ArtifactsBucketName`→`PATHFINDER_S3_BUCKET`.
Image versions cost storage — clean up old versions after drills (see `99-teardown.sh`).
