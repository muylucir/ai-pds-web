# Strands 전환 실 VM 드릴 (수동, 도쿄)

전제: Task 5 커밋 반영 후 `./package-harness.sh && npx cdk deploy`.

1. **이미지 빌드 확인**: CfnOutputs 갱신 → CloudWatch `/pathfinder/microvm/harness`에서
   ready hook 로그의 `strands import ok` 확인.
2. **스모크 턴**: microvm 모드 백엔드 기동(README B-2, PATHFINDER_S3_BUCKET는 CDK
   Artifacts 버킷) → 캔버스 아님 **워크스페이스**에서 "AI-PLC를 시작해줘" 전송 →
   welcome message 스트림 + report_stage 이벤트로 좌측 사이드바 갱신 확인.
3. **질문 왕복**: 우측 패널 질문 폼 → 답변 제출 → 다음 스테이지 진행 확인.
   S3 콘솔에서 `sessions/p*/` 오브젝트 생성 확인.
4. **컨텍스트 복구 리허설 (핵심)**: 질문 대기 상태에서 콘솔로 MicroVM terminate →
   같은 프로젝트에서 새 메시지/새로고침 → `GET /pending`이 같은 질문을 복원하고,
   답변 제출이 정상 재개되는지 확인. (S3SessionManager interrupt 복원 검증)
5. **IAM 경계 확인**: VM 롤 자격으로 `aws s3 cp s3://<bucket>/projects/... -` 시도
   → AccessDenied (sessions/*만 허용) 확인.
6. **롤백 리허설 (선택)**: `PATHFINDER_DRIVER=claude`만으로는 되돌아가지 않는다 —
   Strands 전환(Task 5) 시점에 `harness/Dockerfile`에서 Node·Claude Code CLI 설치
   레이어가 제거되었으므로, 현재 이미지에는 `claude` 실행 파일이 없다. 구 경로를
   되살리려면: (a) `harness/Dockerfile`에 Claude Code CLI 설치 라인을 복원, (b)
   `./package-harness.sh && npx cdk deploy`로 이미지를 다시 빌드·배포, (c) 그 새
   이미지가 뜬 MicroVM에서만 `PATHFINDER_DRIVER=claude` 환경변수로 1턴 확인. 즉
   env 플래그 단독 전환이 아니라 **이미지 재빌드+재배포가 선행 조건**이다.
7. 완료 후 비용 정리: `npx cdk destroy` 또는 VM terminate.
