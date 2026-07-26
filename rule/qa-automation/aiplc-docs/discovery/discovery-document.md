# Discovery Document — QA × AI (QA TestCase Copilot)

> Discovery 단계 메인 산출물. **개발 워크스페이스 핸드오프용 최종본.**

## 진행 상태 (Discovery 완료)
- [x] Workspace Detection
- [x] Discovery Mode Selection — Path A (페인포인트 → PR/FAQ)
- [x] Envision — PR/FAQ 승인
- [x] Solution Analysis — 단일 통합 솔루션
- [x] Prototype & Validation — 사양서 확정 (빌드 핸드오프)
- [x] Product Strategy
- [x] Go-to-Market
- ✅ **Discovery 완료**

---

## Executive Summary
QA 엔지니어를 위한 **QA TestCase Copilot** — 흩어진 정책/기획 문서를 분석해 고품질 TC 초안과 확인 문의사항을 생성하고, 명세화된 기준으로 각 TC를 Auto/Manual/휴먼체크로 근거와 함께 분류하는 Claude(AWS Bedrock) 기반 데스크톱 웹 도구. QA 팀 표준 프로세스에 내장해 TC 설계 시간을 줄이고 분류 일관성과 TC 품질을 높이는 것을 목표로 한다.

---

## Part 1. Envision
- **주 고객**: QA 엔지니어(테스트 설계·실행·자동화 담당).
- **핵심 문제**: ① 정책/기획서가 Figma·Confluence·기획서에 흩어져 TC 설계에 시간·휴먼에러가 큼, AI 초안 품질 낮음. ② TC Auto/Manual 판별이 주관적·비일관적, 구현 단계 오분류로 재작업.
- **성공 정의**: 설계 시간 단축 / 품질·일관성 향상 / 자동화 유지보수 효율.
- PR/FAQ 전문: `envision/prfaq.md` · 페인포인트: `envision/pain-points.md`

## Part 2. Solution Analysis
- **단일 통합 솔루션**: 정책 문서 → TC 생성(+문의사항) → Auto/Manual/휴먼체크 분류.
- 두 진입점: (A) 생성→분류 전체, (B) 기존 TC 업로드 → 분류만.
- 상세: `solution-analysis/identified-solutions.md`

## Part 3. Prototype & Validation
- **사양서(공유용)**: `prototypes/qa-testcase-copilot/PROTOTYPE-qa-testcase-copilot.md`
- **설계 컨텍스트**: `prototypes/qa-testcase-copilot/design-context.md`
- 결정: LLM=AWS Bedrock(Claude, `us.anthropic.claude-sonnet-4-20250514-v1:0`), 입력=정책 텍스트+TC 시트, 출력=팀 포맷 TC 표+문의사항+분류 근거, 프론트=데스크톱 웹앱.

## Part 4. Product Strategy
- 포지셔닝: QA 팀 표준 프로세스 내장 파이프라인.
- 차별점: 팀 정책·기준 내재화 / 문의사항 자동 도출 / 분류 근거 투명성 (+팀 포맷 출력).
- 도입: 파일럿 1개 → 검증 → 확산. KPI: 시간 단축·분류 일관성/재작업 감소·TC 품질.
- 최대 리스크: AI 신뢰성(환각/누락) → 초안+문의사항 구조·근거 명시·벤치마크·범위 축소.
- 상세: `product-strategy/strategy.md`

## Part 5. Go-to-Market
- 확산: 파일럿 온보딩 세션 + 사내 위키 가이드/기준 문서 공개.
- 이해관계자 1순위: QA 리드/매니저(프로세스 표준화 승인).
- 런칭 게이트: 시간 단축·분류 일관성/재작업 감소·TC 품질 목표 달성.
- 일정: 1~2개월 내 파일럿. 운영: QA 팀 공동 관리(기준 문서 버전관리).
- 상세: `go-to-market/gtm-plan.md`

---

## 개발 핸드오프 시 남은 입력 (PM 제공)
- [ ] 팀 고유 TC 포맷/컬럼 정의
- [ ] Auto/Manual/휴먼체크 명세화 분류 기준 문서
- [ ] (선택) 샘플 정책 텍스트 + 정답 TC 예시 (정확도 벤치마크용)
- [ ] AWS Bedrock 자격 증명 및 Claude Sonnet 4 모델 액세스 활성화

## 개발자 다음 단계 (별도 워크스페이스)
- Inception: 요구사항 분석, 워크플로우 계획, 애플리케이션 설계
- Construction: 코드 생성, 빌드/테스트
- Operations: 배포, 모니터링
- 시작점: `PROTOTYPE-qa-testcase-copilot.md` + 본 Discovery Document.
