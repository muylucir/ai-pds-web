import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Shared markdown renderer for the Living Document and the parse_ok=false
// fallback. react-markdown does not emit raw HTML by default (no
// dangerouslySetInnerHTML), so authored markdown is rendered safely. `.doc-content`
// styles come from app/globals.css (ported from files/ui/03-document-review.html).
export function MarkdownView({ markdown }: { markdown: string }) {
  return (
    <div className="doc-content">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
    </div>
  );
}
