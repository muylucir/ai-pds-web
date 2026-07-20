// 공용 마크다운 렌더러 — AI 메시지·문서 뷰어·질문 preamble 전용.
// 사용자 입력은 여기로 보내지 않는다(plain text 유지 — spec §3).
// raw HTML은 react-markdown 기본 동작대로 렌더하지 않는다(XSS).
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

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
        {text}
      </ReactMarkdown>
    </div>
  );
}
