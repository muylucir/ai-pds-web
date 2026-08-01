// frontend/components/canvas/HistorySkeleton.tsx
//
// 히스토리 복원 중 채팅 자리를 채우는 자리표시자. 종전에는 GET /history가 도는
// 동안 이 영역이 빈 화면이어서, 대화가 많은 프로젝트를 다시 열면 "내 대화가
// 사라졌다"로 읽혔다.
//
// AiMessage와 같은 아바타·폭을 쓰는 것이 의도적이다 — 로드가 끝나 실제 항목이
// 들어올 때 레이아웃이 튀지 않는다.
export function HistorySkeleton() {
  // 폭을 다르게 둔다: 같은 길이 박스 세 개는 로딩 바처럼 보이고, 들쭉날쭉하면
  // 대화처럼 보인다.
  const widths = ["w-3/5", "w-4/5", "w-2/5"];
  return (
    <div role="status" aria-label="이전 대화를 불러오는 중" className="space-y-4">
      {widths.map((w, i) => (
        <div key={i} className="flex gap-3">
          <span
            className="shrink-0 w-8 h-8 rounded-lg bg-slate-200 animate-pulse"
            aria-hidden="true"
          />
          <div
            data-testid="skeleton-line"
            className={`h-16 ${w} max-w-[85%] rounded-2xl rounded-tl-md bg-slate-100 animate-pulse`}
          />
        </div>
      ))}
    </div>
  );
}
