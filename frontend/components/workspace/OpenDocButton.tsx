// frontend/components/workspace/OpenDocButton.tsx
import { useT } from "@/lib/i18n/provider";

// 질문 폼 위의 "{이름} 보기". 문서를 읽고 답해야 하는 라운드(승인 게이트 —
// "Envision — Step 6 Approval Gate" 등)에서 문서 드로어를 그 자리에서 연다.
// 오른쪽 패널과 좁은 화면의 하단 시트가 같은 버튼을 쓴다.
//
// 이름을 적는 이유: 드로어는 대화가 지금 다루는 문서를 연다. 그것이 이 질문과
// 관련된 문서인지는 사용자가 판단해야 하므로, 무엇이 열릴지를 버튼에 쓴다.
// 문서 아이콘. 이모지(📄)가 아니라 SVG인 이유: 이모지는 OS 폰트에 따라 빠진다
// (headless Chromium 화면에서 실제로 빈칸이었다). 드로어 손잡이도 이것을 쓴다.
export function DocIcon({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
         strokeLinejoin="round" className={className} aria-hidden="true">
      <path d="M4 1.75h5.25L12.5 5v9.25H4z" />
      <path d="M9 1.75V5.25h3.5M6 8.25h4.5M6 11h4.5" />
    </svg>
  );
}

export function OpenDocButton({ name, onClick }: { name: string; onClick: () => void }) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={onClick}
      className="mb-4 inline-flex max-w-full items-center gap-1.5 rounded-lg border border-violet-200 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 hover:bg-violet-100"
    >
      <DocIcon />
      <span className="truncate">{t("ws.openDocNamed").replace("{name}", name)}</span>
    </button>
  );
}
