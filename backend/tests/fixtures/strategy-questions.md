# Product Strategy Questions

**참고**: Prototype & Validation 단계에서 실사용자 검증이 스킵되어, 아래 추천 기본값은 Envision 단계(비즈니스 컨텍스트, 페인 포인트 분석, PR/FAQ)에서만 도출되었습니다. 실증 데이터가 아닌 가정에 기반하므로, 확정 시 유의해주세요.

## Positioning

### Question 1
이 제품을 시장(조직 내)에서 어떻게 포지셔닝하시겠습니까?

A) 사내 특화 전문 도구(Niche Specialist) — 면세 기획전 운영이라는 특정 업무에 특화된 도구로 포지셔닝 ← 추천 기본값 (사내 전용, 범용 도구가 아님)
B) 플랫폼(Platform) — 향후 다른 MD 업무(가격 정책, 재고 관리 등)까지 확장하는 기반 도구로 포지셔닝
C) 프리미엄(Premium) — 고급 데이터 분석 기반의 하이엔드 의사결정 지원 도구로 포지셔닝
X) Other (please describe after [Answer]: tag below)

[Answer]:A

### Question 2
한 문장으로 이 제품의 가치 제안(Value Proposition)을 정의한다면?

A) "기획전 컨셉을 입력하면, 분산된 데이터를 통합 분석해 표준화된 후보 상품과 카피를 30초 내(참고치) 제공하는 MD 전용 AI 어시스턴트" ← 추천 기본값 (PR/FAQ 기반)
B) "베테랑 MD의 노하우를 형식지화하여 신규 MD도 동일한 품질의 기획전을 만들 수 있게 하는 도구"
C) "매출·회전율 데이터를 실시간 반영해 상품 누락 없는 기획전을 보장하는 도구"
X) Other (please describe after [Answer]: tag below)

[Answer]:A

### Question 3
고객(MD)이 이 제품과 연관지어야 할 핵심 메시지는 무엇입니까?

A) "빠르고 일관된 기획전 준비" — 속도와 표준화 ← 추천 기본값
B) "누락 없는 정확한 추천" — 데이터 신뢰성
C) "숙련자 노하우의 접근성" — 조직 학습·온보딩
X) Other (please describe after [Answer]: tag below)

[Answer]:A

## Differentiation

### Question 4
기존 대안(수기 프로세스) 대비 가장 중요한 차별점 3가지 중 최우선 1가지는 무엇입니까?

A) 데이터 통합 — 분산된 검색/거래 데이터를 하나의 자연어 질의로 통합 조회 ← 추천 기본값 (pain-point-analysis.md Key Insight #1과 일치)
B) 응답 속도 — 수기 대비 압도적으로 빠른 처리 시간
C) 카피 자동 생성 — 별도 작업 없이 후보 선정과 동시에 카피 확보
X) Other (please describe after [Answer]: tag below)

[Answer]:A

### Question 5
가장 장기적으로 방어 가능한(defensible) 차별점은 무엇입니까?

A) 사내 데이터 인프라(OpenSearch, 복제 DB)에 대한 독점적 접근 — 외부 상용 툴이 복제할 수 없는 구조적 이점 ← 추천 기본값
B) 축적되는 MD 피드백을 통한 추천 로직의 지속적 개선 (데이터 축적 우위)
C) 조직 내 워크플로우에 깊이 통합되어 전환 비용(switching cost)이 높아짐
X) Other (please describe after [Answer]: tag below)

[Answer]:A

## Business Model

### Question 6
이 제품의 수익/비용 모델은 어떻게 정의하시겠습니까? (사내 도구)

A) 무료 사내 제공 + AWS Bedrock 사용량 기반 인프라 비용을 부서 전체 예산으로 흡수 (Chargeback 없음) ← 추천 기본값 (envision 단계 확정 사항)
B) 부서별 사용량 기반 내부 배분(Chargeback) 도입
C) 아직 결정하지 않음 — 파일럿 이후 결정
X) Other (please describe after [Answer]: tag below)

[Answer]:A

### Question 7
핵심 비용 요인(Cost Driver)은 무엇이라고 예상하십니까?

A) AWS Bedrock LLM 호출 비용(추론 비용)이 가장 큰 비중을 차지할 것으로 예상 ← 추천 기본값
B) 초기 개발/구축 인력 투입 비용이 더 큰 비중
C) 사내 데이터 시스템(OpenSearch/DB) 조회 부하 관련 인프라 비용
X) Other (please describe after [Answer]: tag below)

[Answer]:A

### Question 8
"수익성" 대신 이 사내 도구의 투자 대비 효과(ROI)를 어떻게 측정하시겠습니까?

A) MD 1인당 기획전 준비 시간 절감률 ← 추천 기본값 (pain-point-analysis.md에서 확인된 심각도 기준과 일치)
B) 신규 MD 온보딩 기간 단축 정도
C) 파트장 검수 통과율(결과 편차 감소) 상승 정도
D) 위 지표들의 종합 (복수 지표 병행 추적)
X) Other (please describe after [Answer]: tag below)

[Answer]:A

## Target Market

### Question 9
초기 베타(Beachhead) 대상은 누구로 하시겠습니까?

A) 상품 영업본부 내 특정 파일럿 팀(예: 뷰티/패션 카테고리 담당 MD 소그룹)으로 우선 한정 ← 추천 기본값 (2개월 파일럿 목표와 일치, 리스크 관리에 유리)
B) 상품 영업본부 소속 MD 전원에게 동시 배포
C) 신규 MD만 우선 대상(온보딩 효과 검증 목적)
X) Other (please describe after [Answer]: tag below)

[Answer]:A

### Question 10
베타 이후 확장 경로는 어떻게 되나요?

A) 파일럿 팀 검증 후 상품 영업본부 전체 MD로 확장 ← 추천 기본값
B) 상품 영업본부를 넘어 타 브랜드/자회사 MD 조직까지 확장
C) 기획전 업무를 넘어 가격 정책, 재고 관리 등 인접 MD 업무로 기능 확장
X) Other (please describe after [Answer]: tag below)

[Answer]:A

### Question 11
핵심 도입 경로(채널)는 무엇입니까? (사내 도구이므로 "채널"은 조직 내 확산 방식)

A) 상품 영업본부 파트장을 통한 하향식(top-down) 도입 공지 및 필수 활용 권장 ← 추천 기본값
B) 파일럿 팀의 자발적 사용 후 성공 사례 공유를 통한 자연 확산(bottom-up)
C) 두 방식 병행
X) Other (please describe after [Answer]: tag below)

[Answer]:C

## Success Metrics

### Question 12
출시 후 6개월 내 핵심 KPI는 무엇입니까? (복수 선택 가능, 최우선 1개 우선 표시)

A) MD 업무 시간 절감률 (목표: 기획전 준비 시간 30% 이상 단축) ← 추천 기본값
B) 결과 채택률 (AI 추천 결과를 실제 진열안에 반영한 비율)
C) 신규 MD 온보딩 기간 단축률
D) 파트장 검수 재작업률 감소
X) Other (please describe after [Answer]: tag below)

[Answer]:A,B

### Question 13
이 제품의 Product-Market Fit(사내 도구이므로 "조직 내 채택")은 어떤 모습입니까?

A) MD들이 수기 방식 대신 이 도구를 기본 워크플로우 첫 단계로 자연스럽게 사용하게 됨 ← 추천 기본값
B) 파트장이 검수 기준에 AI 추천 결과 활용 여부를 포함시킴
C) 신규 MD 온보딩 프로세스에 이 도구 사용법이 필수 교육으로 포함됨
X) Other (please describe after [Answer]: tag below)

[Answer]:A
