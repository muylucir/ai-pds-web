# AI-PLC Discovery Audit Log

## 2025-01-01T00:00:00Z — Initial User Request
**Raw Input (user):**
> qa 업무를 ai를 사용해 효과적으로 하고싶어요

**Attached file:** uploads/240d8f6c/gcc.xlsx.md — QA 페인포인트/아이디어 표 (약 17개 항목). 주요 주제:
- TC Auto/Manual 자동화 가능 여부 분류 (경험 의존 → 일관 기준화)
- API 명세(스웨거/컨플/docs) AI 분석·개선 및 검증 지원
- API 응답 ↔ DB 컬럼 매칭 기반 TC 기대결과 도출
- Figma/Confluence 흩어진 정책 문서 기반 고품질 TC 작성 + 히스토리 추적
- 정책/기획서 분석 프롬프트 개선으로 TC 초안 품질 향상 및 문의사항 자동 도출
- 자동화 코드 팀 공유(로컬→팀 배포)
- 자동화 실패 시 로케이터 자가 치유(스크린샷/로그/DOM → 대체 셀렉터 제안)
- 릴리즈 회귀 TC 세트 자동 생성/선별
- 실패 분석 룰(정규식) → LLM 보강(failure_analyzer.py)
- Slack 알림 자동 설정 / 스레드 요약 봇
- 개발용 멀티 에이전트 역할별 가드레일 설정

## 2025-01-01T00:00:00Z — Workspace Detection
- 기존 aiplc-state.md: 없음 (신규 프로젝트)
- PROTOTYPE-*.md 파일: 없음
- 결론: Discovery Mode Selection으로 진행 (Entry Point 2/3)

## 2025-01-01T00:05:00Z — Envision Q&A 응답
- Q1 주 고객: A (QA 엔지니어)
- Q2 문제 초점: A,B (정책/기획서 기반 TC 설계 + TC Auto/Manual 분류)
- Q3 성공 정의: A,B,C (설계 시간 단축 + 품질/일관성 + 자동화 유지보수)
- Q4 환경: A (Claude / Claude Code)
- 산출물: pain-points.md, prfaq.md, discovery-document.md(v1) 작성. PR/FAQ 승인 요청.

## 2025-01-01T00:10:00Z — Solution Analysis & Prototype Context
- Solution 결정: A (단일 통합 솔루션)
- Prototype Q&A: Q1=B(AWS Bedrock), Q2=A,D(텍스트 붙여넣기+기존 TC 업로드), Q3=B(팀 고유 포맷 제공 예정), Q4=A(웹앱 데스크톱), Q5=A(명세화 분류기준 제공 가능)
- 산출물: PROTOTYPE-qa-testcase-copilot.md, design-context.md, discovery-document.md(v2)
- 남은 입력: 팀 TC 포맷, 분류 기준 문서, (선택) 샘플 정책+정답 TC

## 2025-01-01T00:15:00Z — Product Strategy
- Q1 포지셔닝: B (QA 팀 표준 프로세스 내장)
- Q2 차별점: A,B,C (정책·기준 내재화 / 문의사항 도출 / 분류 근거 투명성)
- Q3 도입: A (파일럿 1개 프로젝트 → 확산)
- Q4 KPI: A,B,C (시간 단축 / 분류 일관성·재작업 감소 / TC 품질)
- Q5 리스크: B (AI 결과 신뢰성)
- 산출물: product-strategy/strategy.md, discovery-document.md(v3)

## 2025-01-01T00:20:00Z — Go-to-Market & Discovery 완료
- Q1 확산채널: A,B (파일럿 온보딩 + 사내 위키 가이드/기준 공개)
- Q2 이해관계자: A (QA 리드/매니저)
- Q3 런칭 게이트: A,B,D (시간 단축 / 분류 일관성·재작업 감소 / TC 품질)
- Q4 일정: B (1~2개월 내 파일럿)
- Q5 운영: B (QA 팀 공동 관리)
- 산출물: go-to-market/gtm-plan.md, discovery-document.md(v4, 최종)
- Discovery 전 단계 완료.

## 2025-01-01T00:25:00Z — 최종 승인
**Raw Input (user):**
> 승인

- Discovery Document v4 최종 승인 완료. 개발 워크스페이스 핸드오프 가능 상태.
- 미제공 입력 항목(팀 TC 포맷, 분류 기준 문서, 샘플 정책+정답 TC)은 선택적 보강 사항으로 유지.
