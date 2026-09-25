import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';

// 런타임 기본 모델 — user-data가 ANTHROPIC_MODEL로 내보낸다. 권한과 별개로
// 유지한다: 이 값을 바꾸는 것은 배포 동작을 바꾸는 것이고, 아래 목록을 늘리는
// 것은 "바꿀 수 있게 두는 것"이다.
export const MODEL = 'global.anthropic.claude-opus-4-8';

// invoke를 허용하는 모델 — 명시 목록이 아니라 와일드카드다.
//
// 명시 목록이었을 때의 문제: 모델 카탈로그가 S3로 옮겨가면서
// (/admin/models, spec 2026-08-01) 관리자가 새 Claude 모델을 화면에서 등록할
// 수 있게 됐는데, IAM이 5개만 허용하면 등록해도 첫 대화 턴에 AccessDenied가
// 나고 그 실패는 백엔드 로그에만 남는다. "화면에서 모델을 추가할 수 있다"고
// 보여주면서 실제로는 cdk deploy가 필요한 상태가 최악이므로 와일드카드로
// 넓힌다 — 이것이 그 기능이 성립하는 유일한 조건이다.
//
// 허용 범위가 "모든 global Anthropic Claude 추론 프로파일"로 넓어지는 것은
// 의도된 교환이다. 이 롤은 Bedrock invoke 외에 하는 일이 없고(S3는 별도
// statement), 어떤 Claude 모델을 부르든 데이터 경계는 같다.
//
// inference-profile은 global.* 프리픽스, foundation-model은 없는 형태 —
// 둘 다 필요하다(프로파일 경유 호출이 내부적으로 후자를 참조한다).
// test/hosting-stack.assert.ts가 이 두 패턴과 MODEL의 포함 여부를 단정한다.
// 샌드박스 프로세스의 AgentRole(lib/aipds-agent-creds-stack.ts)도 같은 목록을 쓴다.
export const INVOKABLE_MODEL_ARNS = (account: string) => [
  `arn:aws:bedrock:*:${account}:inference-profile/global.anthropic.claude-*`,
  `arn:aws:bedrock:*::foundation-model/anthropic.claude-*`,
];

// 백엔드가 쓰는 아티팩트 버킷 프리픽스. 프로젝트 데이터는 projects/,
// strands 세션은 sessions/ 아래에 있고 — surveys/와 models/는 프로젝트
// 프리픽스 밖에 있어야 한다.
//
// surveys/by-token/{token}.json은 토큰 -> 프로토타입 단방향 인덱스다. 공개
// 설문 링크(/survey/{token})는 토큰이 어느 프로젝트 것인지 알기 전에 이걸
// 읽어야 하므로 projects/{pid}/ 안에 둘 수 없다
// (backend/aipds/app.py의 surveys_root_s3_factory).
//
// models/catalog.json은 프로젝트 생성 화면의 모델 목록이다. 프로젝트가 하나도
// 없는 상태에서 읽히므로 같은 이유로 프로젝트 프리픽스 밖에 있다
// (backend/aipds/model_catalog.py의 CATALOG_KEY).
//
// 실측 배포 버그: 이 목록에 surveys/*가 없어서 설문 생성이 전부 500이었고,
// 백엔드 로그에만 AccessDenied(PutObject on surveys/by-token/...)가 남았다.
// 설문 기능이 들어온 뒤 이 헬퍼가 함께 갱신되지 않은 것이 원인 —
// backend/aipds/survey/store.py의 TOKEN_INDEX_PREFIX와 짝이다.
// ListBucket에도 필요하다: purge()의 토큰 회수는 delete_prefix(=list 후
// delete_objects)를 타므로 목록 권한이 없으면 조용히 0건을 지운다.
//
// design/profile.json은 관리자가 올린 브랜드 디자인 프로필이다. 프로젝트가
// 없어도 관리되므로 models/catalog.json과 같은 이유로 프로젝트 프리픽스 밖에
// 있다 (backend/aipds/design_profile.py의 DESIGN_PROFILE_KEY).
// imports/*는 프로젝트 가져오기의 스테이징이다. 브라우저가 presigned PUT으로
// 번들을 **직접** 올리는 자리이고, 그 URL은 이 롤의 자격증명으로 서명되므로 여기에
// 없으면 서명은 성공하고 S3가 브라우저에게 403을 준다 — 백엔드 로그에는 아무것도
// 남지 않는다. 프로젝트 데이터와 섞이지 않게 projects/ 밖에 둔다:
// restore_projects의 스캔과 프로젝트 삭제의 delete_prefix가 그 안의 키를 자기
// 것으로 착각할 여지를 남기지 않는다(backend/aipds/import_staging.py).
const BACKEND_BUCKET_PREFIXES = [
  'projects/*', 'sessions/*', 'surveys/*', 'models/*', 'design/*', 'imports/*',
] as const;

// Anthropic 모델은 계정에서 **처음** 호출될 때 Bedrock이 AWS Marketplace 구독을 자동으로 만든다 —
// 호출한 롤에 이 권한이 없으면 403("not authorized to perform the required AWS Marketplace actions")이다.
// 새 계정의 첫 호출이, 또는 새 모델을 추가한 뒤의 첫 호출이 어느 롤에서 나올지 모르므로 Bedrock을
// 부르는 롤 전부(백엔드·인스턴스 롤, 샌드박스의 AgentRole)에 준다. 한 번 구독되면 계정의 모든 롤이
// 이 권한 없이 호출한다.
//
// CalledViaLast: 구독은 **Bedrock 호출을 거쳐서만** 일어난다 — Marketplace API를 직접 불러 다른
// 상품을 구독할 수 없고, 거쳐 가는 Bedrock 호출은 INVOKABLE_MODEL_ARNS로 묶여 있다. 제품 ID는 모델마다
// 달라 새 모델마다 고쳐야 하므로 걸지 않는다.
export function bedrockSubscribeStatement(): iam.PolicyStatement {
  return new iam.PolicyStatement({
    actions: [
      'aws-marketplace:Subscribe',
      'aws-marketplace:Unsubscribe',
      'aws-marketplace:ViewSubscriptions',
    ],
    resources: ['*'],
    conditions: { StringEquals: { 'aws:CalledViaLast': 'bedrock.amazonaws.com' } },
  });
}

// 백엔드(드릴 롤 또는 EC2 인스턴스 롤)가 필요로 하는 공통 권한:
// Bedrock invoke(+ 첫 호출의 Marketplace 자동 구독) + 아티팩트 버킷 projects/*·sessions/*·surveys/*·
// models/*·design/* 읽기/쓰기/목록.
export function backendPolicyStatements(
  bucket: s3.IBucket,
  account: string,
): iam.PolicyStatement[] {
  return [
    new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: INVOKABLE_MODEL_ARNS(account),
    }),
    bedrockSubscribeStatement(),
    new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
      resources: BACKEND_BUCKET_PREFIXES.map((p) => `${bucket.bucketArn}/${p}`),
    }),
    new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [bucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': [...BACKEND_BUCKET_PREFIXES] } },
    }),
  ];
}
