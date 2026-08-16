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
