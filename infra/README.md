# Pathfinder Drill Infra (CDK, ap-northeast-1)

Single stack `PathfinderDrillStack`: S3 artifacts bucket (`projects/*` +
`sessions/*`) and a backend execution role (Bedrock invoke + S3 access on
both prefixes). The Strands agent now runs in-process in the backend — there
is no MicroVM image, build role, harness asset, or log group anymore.

## Synth / deploy
```bash
npm ci
npx cdk synth                 # validates the stack (no AWS creds needed)
npx cdk deploy                # creates the bucket + backend role
```
Feed the CfnOutputs into the backend env: `BackendRoleArn`→ the role the
backend process assumes (or the instance-profile trust is narrowed to it in
production — `AccountPrincipal` here is drill/demo convenience only),
`ArtifactsBucketName`→`PATHFINDER_S3_BUCKET`, `Region`→`AWS_REGION`.
