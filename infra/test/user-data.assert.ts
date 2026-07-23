import * as assert from 'node:assert';
import { renderUserData } from '../lib/user-data';

const s = renderUserData({
  region: 'ap-northeast-2',
  bucketName: 'my-artifacts-bucket',
  model: 'global.anthropic.claude-opus-4-8',
  secretArn: 'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:hdr-AbCdEf',
  assetS3Uri: 's3://asset-bucket/abc123.zip',
});

// 1) 안전 옵션·로그
assert.match(s, /set -euxo pipefail/, 'must be strict bash');
// 2) 에셋 다운로드
assert.match(s, /aws s3 cp s3:\/\/asset-bucket\/abc123\.zip/, 'must download asset');
// 3) 시크릿 부팅 조회 (하드코딩 금지 — 런타임 조회)
assert.match(s, /aws secretsmanager get-secret-value --secret-id arn:aws:secretsmanager:[^ ]+ /, 'must fetch secret at boot');
// 4) nginx 헤더 검증 (403)
assert.match(s, /\$http_x_origin_verify/, 'nginx must check X-Origin-Verify');
assert.match(s, /return 403/, 'nginx must 403 on mismatch');
// 4b) 이스케이핑 고정: nginx 런타임 변수는 백슬래시 정확히 1개만 남아야 부팅 시
//     bash가 heredoc에서 하나를 소비하고 nginx에 리터럴 $var를 남긴다.
//     단순 s.includes('\\$foo')는 백슬래시 2개짜리 회귀도 그대로 통과한다
//     ('\\\\$foo'가 '\\$foo'를 부분 문자열로 포함하기 때문) — 그래서 부정
//     lookbehind로 "바로 앞에 백슬래시가 없는 백슬래시"만 매치하도록 고정한다.
assert.match(s, /(?<!\\)\\\$http_x_origin_verify\b/, 'nginx var must keep exactly ONE backslash (not 0, not 2+) so bash leaves a literal $ for nginx');
assert.match(s, /(?<!\\)\\\$host\b/, 'nginx var \\$host must keep exactly ONE backslash');
assert.match(s, /(?<!\\)\\\$proxy_add_x_forwarded_for\b/, 'nginx var \\$proxy_add_x_forwarded_for must keep exactly ONE backslash');
// 4c) 대조: 시크릿 변수는 백슬래시 없이 남아야 부팅 시 bash가 실제 값으로 치환한다.
assert.ok(s.includes('"${SECRET}"'), 'secret var must be unescaped so bash expands it');
assert.ok(!s.includes('\\${SECRET}'), 'secret var must NOT be backslash-escaped');
// 5) nginx 라우팅
assert.match(s, /proxy_pass http:\/\/127\.0\.0\.1:8000\//, 'api -> backend');
assert.match(s, /proxy_pass http:\/\/127\.0\.0\.1:3000/, 'root -> frontend');
assert.match(s, /proxy_buffering off/, 'SSE: buffering off');
// 6) 프론트 빌드 env (same-origin API)
assert.match(s, /NEXT_PUBLIC_API_BASE_URL=\/api/, 'front build uses /api');
// 7) 백엔드 env
assert.match(s, /PATHFINDER_S3_BUCKET=my-artifacts-bucket/, 'backend bucket env');
assert.match(s, /ANTHROPIC_MODEL=global\.anthropic\.claude-opus-4-8/, 'backend model env');
assert.match(s, /PATHFINDER_S3_REGION=ap-northeast-2/, 'backend s3 region env');
// 8) systemd 유닛
assert.match(s, /pathfinder-backend\.service/, 'backend unit');
assert.match(s, /pathfinder-frontend\.service/, 'frontend unit');
assert.match(s, /uvicorn pathfinder\.app:app --host 127\.0\.0\.1 --port 8000/, 'uvicorn cmd');
assert.match(s, /next start -H 127\.0\.0\.1 -p 3000/, 'next start cmd');
// 9) 시크릿 하드코딩되지 않음 (실제 값은 부팅 시점에만 존재)
assert.ok(!s.includes('AbCdEf-value'), 'secret value never inlined');

console.log('OK  user-data: all required elements present (incl. nginx-var vs secret-var escaping)');
