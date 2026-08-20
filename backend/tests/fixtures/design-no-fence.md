# Design System — Riverbank Coffee

밖에서 만들어진 DESIGN.md의 모양을 그대로 흉내낸 픽스처다. 이 파일의 존재 이유는
**```tokens 펜스가 없다는 것**이고, 그것이 2026-08-19 배포에서 브랜드가 화면에 닿지
않은 원인이다. 실제 입력(37KB 문서)의 성질을 다 갖췄다:

- 같은 계열 색이 여러 개이고 문서가 서로 다른 역할을 준다(브랜드 헤딩 vs CTA 채움)
- 본문 색이 `rgba()`라서 우리 `_HEX`가 거부한다
- radius가 세 개인데 우리 토큰은 하나다
- 사내 폰트가 있고, 대체 서체가 문장으로만 적혀 있다

## 1. Visual Theme & Atmosphere

따뜻하고 자신 있는 리테일 플래그십. 캔버스는 뉴트럴 웜 크림(`#f2f0eb`)과 세라믹
오프화이트(`#edebe9`)를 번갈아 쓰고, 시그니처 그린이 히어로 밴드와 CTA에 브랜드
순간을 만든다.

## 2. Color Palette & Roles

### Primary

- **Riverbank Green** (`#006241`): 역사적인 브랜드 그린. h1 헤딩과 주요 섹션 헤더에
  쓰고, 단일 지배색이 필요한 자리의 브랜드 신호다.
- **Green Accent** (`#00754a`): 조금 더 밝은 그린. 채워진 CTA의 색이다.
- **House Green** (`#1e3932`): 거의 검정에 가까운 딥 그린. 푸터 표면과 피처 밴드
  배경.
- **Green Light** (`#d4e9e2`): 옅은 민트. 폼 유효 상태 틴트와 밝은 유틸리티 표면.

### Neutrals & Text

- **Text Black** (`rgba(0,0,0,0.87)`): 밝은 표면 위의 헤딩.
- **Text Black Soft** (`rgba(0,0,0,0.58)`): 본문.

### Semantic

- **Red** (`#c82014`): 파괴적 동작에만.

## 3. Typography Rules

### Font Family

하우스 산세리프는 `SoDoSans`다. 사용할 수 없는 환경에서는 `Inter`로 대체한다.
고정폭은 `JetBrains Mono`.

## 4. Border Radius Scale

| Value | Use |
|-------|-----|
| `12px` | 카드, 모달, 메뉴 타일 |
| `50px` | 모든 버튼 — 풀 필 |
| `50%` | 원형 아이콘, 아바타 |

## 5. Quick Color Reference

- Primary CTA: "Green Accent (`#00754a`)"
- Primary CTA text: "White (`#ffffff`)"
- Brand heading: "Riverbank Green (`#006241`)"
- Page canvas: "Neutral Warm (`#f2f0eb`)"
- Card canvas: "White (`#ffffff`)"
- Destructive: "Red (`#c82014`)"

## 6. Do's and Don'ts

### Don't

- 임의의 그레이를 브랜드 그린 위에 얹지 않는다.
- 버튼에 각진 모서리를 쓰지 않는다.
