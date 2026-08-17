// 공용 마크다운 렌더러 — AI 메시지·문서 뷰어·질문 preamble 전용.
// 사용자 입력은 여기로 보내지 않는다(plain text 유지 — spec §3).
// raw HTML은 react-markdown 기본 동작대로 렌더하지 않는다(XSS).
//
// **`[Answer]:` 줄을 여기서 손본다(2026-08-16의 결함).** 백엔드가 답변을 질문
// 파일의 `[Answer]:` 칸에 심는데 화면에 아무것도 나타나지 않았다 — 그 줄이
// CommonMark 링크 참조 정의라서 렌더러가 출력을 만들지 않기 때문이다(자세한
// 근거는 lib/questionMarkdown.ts 헤더).
//
// 호출부 5곳에 각자 걸지 않고 렌더러에 한 번 거는 이유: 질문 파일은 워크스페이스
// 패널·문서 리뷰·캔버스·파싱 실패 폴백 어디서나 열린다. 흩어 두면 여섯 번째
// 호출부가 추가될 때 조용히 빠지고, 그 실패는 "답변이 기록되지 않았다"로 보인다 —
// 이번에 실제로 그렇게 보였다. 같은 이유로 보기 줄(`A) …`)에 하드 브레이크를
// 붙인다 — 그것 없이는 보기가 한 줄로 쭉 이어진다. 두 변환 모두 멱등이다.
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { prepareQuestionMarkdown } from "@/lib/questionMarkdown";

export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={"prose prose-sm prose-slate max-w-none [&_table]:text-xs " + (className ?? "")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
          ),
        }}
      >
        {prepareQuestionMarkdown(text)}
      </ReactMarkdown>
    </div>
  );
}

// 인라인 전용 렌더러 — 질문 문장과 보기 라벨.
//
// **왜 위 `Markdown`을 쓸 수 없는가.** 보기는 `<label>` 안 flex 레이아웃이고, 위
// 렌더러는 `<div className="prose …">`로 감싼 뒤 문단을 `<p>`로 그린다. 그러면
// 라벨 안에 블록 요소와 prose 여백이 들어와 선택 컨트롤과의 정렬이 깨진다.
//
// **왜 아예 렌더하지 않으면 안 되는가(2026-08-18 실측).** 질문 파일의 보기는 실제로
// 마크다운으로 쓰여 있다 — `A) **「조정 브리프」** — 조정 건 하나를 열면 …`. 평문으로
// 그리면 `**`가 화면에 그대로 남는다. 질문 문장도 같다
// (`제품명을 무엇으로 할까요? *(보도자료의 Heading)*`).
//
// 블록 요소는 **의도적으로 버린다**: 보기 라벨에 헤딩·목록·표가 오는 것은 애초에
// 잘못된 입력이고, 렌더하면 카드가 부서진다. `unwrapDisallowed`로 내용은 남긴다 —
// 태그만 벗기고 글자는 잃지 않는다.
//
// `prepareQuestionMarkdown`은 걸지 않는다. 그 변환은 **파일 전문**을 위한 것이다:
// 줄 맨 앞 `[Answer]:`를 링크 정의에서 구해내고, 보기 줄에 하드 브레이크를 붙인다.
// 여기 오는 것은 이미 파서가 잘라낸 한 조각이라 두 변환 모두 대상이 없고, 후자는
// 오히려 라벨 안에 줄바꿈을 만든다.
const BLOCK_ELEMENTS = ["h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
                        "table", "thead", "tbody", "tr", "th", "td",
                        "blockquote", "pre", "hr", "img"];

export function InlineMarkdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      // 문단을 감싸지 않는다 — 라벨/헤딩 안에서 인라인으로 흐르게 한다.
      components={{
        p: ({ children }) => <>{children}</>,
        a: ({ children, href }) => (
          <a href={href} target="_blank" rel="noopener noreferrer"
             className="underline">{children}</a>
        ),
      }}
      disallowedElements={BLOCK_ELEMENTS}
      unwrapDisallowed
    >
      {text}
    </ReactMarkdown>
  );
}
