---
title: Aurora Design Tokens
version: 3.2
---

# Aurora — Token Reference

세 번째 모양이다. 앞의 두 픽스처가 **산문으로 역할을 설명하는** 문서라면, 이것은
디자인 시스템이 실제로 배포하는 형태 — **CSS 커스텀 프로퍼티 표**다. 뒤집는 것:

- 프런트매터(`---`)로 시작한다. 마크다운 헤딩이 첫 줄이 아니다 —
  `inject_fence`가 "첫 `#` 줄"을 찾으므로 이 모양에서 블록이 어디로 가는지가
  검증 대상이다
- 색이 `oklch()`다. `rgba`·`hsl`이 아니므로, 거부 목록을 열거한 프롬프트는 이
  표기를 통과시킬 수 있다 — 허용 형식 밖은 전부 omit이어야 잡힌다
- `--radius-*`가 여러 개인데 컴포넌트 언급이 없다(카드/버튼 구분이 아예 없음)
- 산문 설명이 거의 없다. 역할은 변수 **이름**에만 있다

## Colour tokens

| Token | Value |
|---|---|
| `--color-brand` | `oklch(0.55 0.18 258)` |
| `--color-brand-contrast` | `#ffffff` |
| `--color-surface` | `oklch(0.98 0.004 258)` |
| `--color-text` | `oklch(0.24 0.01 258)` |
| `--color-danger` | `oklch(0.52 0.19 27)` |

`--color-brand`는 채워진 액션과 선택 상태에 쓴다. `--color-text`는 모든 본문이다.

## Radius tokens

| Token | Value |
|---|---|
| `--radius-xs` | `2px` |
| `--radius-sm` | `6px` |
| `--radius-md` | `10px` |
| `--radius-pill` | `999px` |

## Type tokens

| Token | Value |
|---|---|
| `--font-body` | `"Public Sans", system-ui, sans-serif` |
| `--font-code` | `"IBM Plex Mono", ui-monospace, monospace` |
