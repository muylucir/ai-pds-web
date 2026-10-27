# 브랜드 프로필 → antd 토큰

작업 디렉토리 `CLAUDE.md`의 브랜드 섹션이 있을 때만 해당한다. 섹션이 시키는 대로 `aipds-theme.css`를 프로토타입 안(`app/globals.css` 옆)으로 복사하고 루트 레이아웃에서 `globals.css` **다음**에 import한다 — 그 import는 그대로 둔다(AI-PDS가 그 사본을 매 빌드·매 호스팅에서 최신 프로필로 덮어쓴다).

**antd는 그 CSS 변수를 읽지 않는다.** antd의 색은 `ConfigProvider`의 seed 토큰에서 hover·active·배경색까지 파생되므로, 브랜드 값은 토큰으로 넘겨야 화면에 닿는다. 아래 `brandToken()`이 그 사본을 **서버에서 읽어** 토큰으로 바꾼다. 호스팅이 다시 빌드할 때 새 값이 다시 읽히므로, 프로필이 바뀌어도 빌드 세션 없이 따라간다.

## 변수 → 토큰 매핑

| `aipds-theme.css` 변수 | antd 토큰 | 비고 |
|---|---|---|
| `--primary` | `colorPrimary`, `colorLink` | 버튼·선택·링크·포커스가 모두 여기서 파생 |
| `--primary-foreground` | `colorTextLightSolid` | primary 배경 위 글자색 |
| `--destructive` | `colorError` | |
| `--background` | `colorBgContainer` | 카드·표·입력의 바탕 |
| `--muted` | `colorBgLayout` | 페이지 바탕(`Layout`) |
| `--foreground` | `colorText` | |
| `--muted-foreground` | `colorTextSecondary` | |
| `--border` | `colorBorder` | |
| `--radius` | `borderRadius` | 숫자(px) — `rem`은 16을 곱한다 |
| `--font-sans` | `fontFamily` | |
| `--font-mono` | `fontFamilyCode` | |

`--secondary`, `--accent`, `--ring`은 antd에 대응하는 seed 토큰이 없다 — 넘기지 않는다(포커스 링은 `colorPrimary`에서 파생된다). 파일에 없는 변수는 토큰에서 빠지고 antd 기본값이 쓰인다.

## 구현

```ts
// app/brand-token.ts — 서버 전용(fs). 'use client'를 붙이지 않는다.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { ThemeConfig } from 'antd';

// 복사한 위치와 같아야 한다. npm 스크립트는 프로토타입 루트(package.json 위치)에서 돈다.
const THEME_FILE = join(process.cwd(), 'app', 'aipds-theme.css');

const TOKEN_OF: Record<string, (keyof NonNullable<ThemeConfig['token']>)[]> = {
  'primary': ['colorPrimary', 'colorLink'],
  'primary-foreground': ['colorTextLightSolid'],
  'destructive': ['colorError'],
  'background': ['colorBgContainer'],
  'muted': ['colorBgLayout'],
  'foreground': ['colorText'],
  'muted-foreground': ['colorTextSecondary'],
  'border': ['colorBorder'],
  'font-sans': ['fontFamily'],
  'font-mono': ['fontFamilyCode'],
};

function px(length: string): number | undefined {
  const n = parseFloat(length);
  if (Number.isNaN(n)) return undefined;
  return length.trim().endsWith('rem') ? n * 16 : n;
}

export function brandToken(): ThemeConfig['token'] {
  let css = '';
  try {
    css = readFileSync(THEME_FILE, 'utf8');
  } catch {
    return undefined;            // 파일이 없으면 antd 기본 테마
  }
  const token: Record<string, string | number> = {};
  for (const [, name, raw] of css.matchAll(/--([a-z-]+)\s*:\s*([^;]+);/g)) {
    const value = raw.trim();
    if (name === 'radius') {
      const r = px(value);
      if (r !== undefined) token.borderRadius = r;
      continue;
    }
    for (const key of TOKEN_OF[name] ?? []) token[key] = value;
  }
  return Object.keys(token).length ? token : undefined;
}
```

루트 레이아웃은 `SKILL.md`의 "Root layout" 그대로다 — `<Providers token={brandToken()}>`.

- **`src/app/`을 쓰는 프로젝트**면 `THEME_FILE`을 `join(process.cwd(), 'src', 'app', 'aipds-theme.css')`로.
- **프로필이 지워지면** AI-PDS가 사본을 변수 없는 파일로 바꾼다 → `brandToken()`이 `undefined` → antd 기본값. 코드는 고칠 필요 없다.
- **토큰이 없고 `DESIGN.md` 산문만 있는 프로필**이면 `aipds-theme.css`는 비어 있다. 그때는 문서가 명시한 색·라운드·서체 값을 위 표의 토큰 이름으로 `Providers`의 `theme.token`에 직접 적는다(`brandToken()` 결과와 합친다). 문서가 말하지 않은 값은 만들어내지 않는다.
- **컴포넌트에서 raw 색을 쓰지 않는다** — `theme.useToken()`의 `token.colorPrimary` 등을 쓰면 브랜드가 바뀔 때 함께 바뀐다.
- `DESIGN.md`의 톤·여백·금기 지침은 시각 디자인 참고로만 읽는다.
