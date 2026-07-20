// frontend/components/workspace/WelcomeCard.tsx
const PATH_A_MSG = "AI-PLC를 시작해줘. Path A(고객 페인 포인트에서 시작)로 진행하고 싶어.";
const PATH_B_MSG = "AI-PLC를 시작해줘. Path B(이미 정리된 유스케이스에서 시작)로 진행하고 싶어.";

export function WelcomeCard({ onStart }: { onStart: (text: string) => void }) {
  return (
    <div className="max-w-xl mx-auto mt-12 rounded-2xl border border-slate-200 bg-white p-6 text-center space-y-4">
      <p className="text-lg font-bold">어떻게 시작할까요?</p>
      <p className="text-sm text-slate-500">
        AI-PLC Discovery는 두 가지 경로로 시작할 수 있습니다.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 text-left">
        <button type="button" onClick={() => onStart(PATH_A_MSG)}
          className="rounded-xl border border-violet-200 bg-violet-50 hover:bg-violet-100 p-4">
          <p className="font-bold text-violet-700 text-sm">Path A — 페인 포인트에서 시작</p>
          <p className="mt-1 text-xs text-slate-600">
            고객 문제를 수집·분석해 PR/FAQ를 작성하고 솔루션을 도출합니다.
          </p>
        </button>
        <button type="button" onClick={() => onStart(PATH_B_MSG)}
          className="rounded-xl border border-sky-200 bg-sky-50 hover:bg-sky-100 p-4">
          <p className="font-bold text-sky-700 text-sm">Path B — 유스케이스에서 시작</p>
          <p className="mt-1 text-xs text-slate-600">
            이미 정리된 유스케이스가 있다면 우선순위화부터 진행합니다.
          </p>
        </button>
      </div>
      <p className="text-xs text-slate-400">직접 입력해도 됩니다 — 아래 입력창에 자유롭게 시작하세요.</p>
    </div>
  );
}
