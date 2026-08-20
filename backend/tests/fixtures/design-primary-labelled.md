# Brand Guidelines — Northwind Logistics

`design-no-fence.md`와 **성질이 반대인** 픽스처다. 그 파일은 프롬프트의 판단 규칙이
유래한 문서이므로, 그것만으로는 규칙이 일반화하는지 알 수 없다. 여기서 뒤집는 것:

- 문서가 색 하나를 **명시적으로 `Primary`라 부르고**, 그 색을 버튼에도 쓴다
  (앞 픽스처는 헤딩 색과 CTA 색이 달랐다 — 프롬프트가 그 한 경우를 법칙으로 박아
  뒀던 자리다)
- radius가 **하나뿐**이다(앞 픽스처는 세 개)
- 고정폭 서체를 **언급하지 않는다**(앞 픽스처는 `JetBrains Mono`)
- 사내 전용 서체가 없다(앞 픽스처는 `SoDoSans` → `Inter` 대체)
- 본문 색이 hex로 적혀 있다(앞 픽스처는 `rgba()`)

## Colour

| Role | Name | Value |
|---|---|---|
| Primary | Harbour Blue | `#1b4f8a` |
| Primary text | White | `#ffffff` |
| Page background | Fog | `#f7f8fa` |
| Body text | Slate | `#1f2933` |
| Danger | Signal Red | `#b3261e` |

**Primary(Harbour Blue)는 브랜드의 단일 지배색이다.** 헤딩, 채워진 버튼, 활성 탭,
링크에 모두 같은 값을 쓴다 — 역할별로 색을 나누지 않는 것이 이 브랜드의 규칙이다.

## Type

본문과 헤딩 모두 `Source Sans 3`. 무게로만 위계를 만든다.

## Shape

모서리 반경은 전 컴포넌트 `4px` 하나로 통일한다. 원형은 쓰지 않는다.

## Motion

전환은 120ms ease-out. 색을 바꾸는 전환에는 opacity를 함께 쓰지 않는다.
