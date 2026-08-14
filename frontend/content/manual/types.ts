// frontend/content/manual/types.ts — 사용 매뉴얼의 콘텐츠 모델.
//
// 왜 lib/i18n/{ko,en}.ts 가 아닌가: 그 딕셔너리는 **화면 문구**의 평면 키 집합
// 이고, 매뉴얼은 문단·목록·표가 있는 산문이다. 문단마다 키를 만들면 UI 문구를
// 찾으려는 사람이 매뉴얼 본문 수백 줄을 헤쳐야 한다. 그래서 별도 콘텐츠
// 모듈로 두고, **같은 규율**을 타입으로 다시 세운다:
// `Record<ManualSectionId, ManualSection>`이므로 한쪽 언어에 절이 빠지면
// 컴파일이 실패한다(en.ts가 `Record<keyof typeof ko, string>`인 것과 같다).
//
// 산문은 마크다운 문자열이고 기존 components/Markdown.tsx(react-markdown +
// gfm)가 렌더한다 — 표·목록·강조를 새 블록 종류로 만들지 않기 위해서다.
// 블록 종류는 **마크다운으로 표현할 수 없는 것들만** 있다.
import type { EntryPointNode, MockupId, ValidationLoopNode } from "./visuals";

/** 도식의 상자 하나. */
export interface DiagramNode {
  /** 상자 안의 글자. **콘텐츠가 소유한다** — 컴포넌트가 문장을 갖지 않는다. */
  label: string;
  /**
   * 누르면 갈 앵커(절 id 또는 heading id). 없으면 눌리지 않는 상자다.
   * parity.test.ts가 이 값이 실제 앵커를 가리키는지 단정한다.
   */
  to?: string;
}

/**
 * 도식 블록. **id마다 노드 키가 다르므로 판별 유니온**이다 — 그래서 한쪽
 * 언어에서 상자 하나를 빼면 컴파일이 실패한다. 위치(배열 인덱스)로 두면
 * 순서가 어긋난 것을 아무도 잡지 못한다.
 */
export type ManualDiagramBlock =
  | {
      kind: "diagram";
      id: "entry-points";
      caption: string;
      nodes: Record<EntryPointNode, DiagramNode>;
    }
  | {
      kind: "diagram";
      id: "validation-loop";
      caption: string;
      nodes: Record<ValidationLoopNode, DiagramNode>;
    };

export type ManualBlock =
  /** 산문. 마크다운(GFM). */
  | { kind: "md"; md: string }
  /** 절 안의 소제목. `id`는 목차의 하위 항목이자 딥링크 앵커가 된다. */
  | { kind: "heading"; id: string; text: string }
  /** 눈에 띄어야 하는 한 문단. `warn`은 되돌릴 수 없는 동작에만 쓴다. */
  | { kind: "callout"; tone: "note" | "warn" | "tip"; md: string }
  /** 순서가 있는 조작 절차. 각 항목은 인라인 마크다운. */
  | { kind: "steps"; items: string[] }
  /** 명령어 블록. 복사 버튼이 붙는다. */
  | { kind: "cmd"; lines: string[]; caption?: string }
  /** 화면 목업. 라벨은 컴포넌트가 앱 딕셔너리에서 직접 읽는다. */
  | { kind: "mockup"; id: MockupId; caption: string }
  /** 흐름 도식. 노드를 누르면 해당 절로 이동한다. */
  | ManualDiagramBlock
  /** 접힌 심화 설명 — 처음 읽는 사람이 건너뛸 수 있어야 하는 것. */
  | { kind: "details"; summary: string; md: string };

export type ManualBlockKind = ManualBlock["kind"];

/** 매뉴얼의 절. `id`가 `/manual#{id}` 딥링크가 된다. */
export interface ManualSection {
  id: ManualSectionId;
  /** 목차와 본문 h2에 쓰인다. */
  title: string;
  /** 제목 아래 한 줄 요약. 검색 대상에도 들어간다. */
  lede: string;
  blocks: ManualBlock[];
}

// 절의 **순서**는 index.ts의 MANUAL_ORDER가 정한다. 여기서는 집합만 정의한다 —
// 순서를 타입에 넣으면 절을 하나 옮길 때마다 타입이 바뀐다.
export type ManualSectionId =
  | "intro"
  | "getting-started"
  | "create-project"
  | "workspace"
  | "questions"
  | "review"
  | "prototypes"
  | "survey"
  | "dashboard"
  | "admin"
  | "operations";

/** 한 언어의 매뉴얼 전체. 절이 하나라도 빠지면 컴파일 에러. */
export type ManualContent = Record<ManualSectionId, ManualSection>;
