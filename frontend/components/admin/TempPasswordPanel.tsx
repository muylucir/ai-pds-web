"use client";
import { useState } from "react";

// 임시 비밀번호는 서버가 저장하지 않는다 — 이 화면이 그 값을 볼 수 있는 유일한
// 기회다. 그래서 경고와 복사 버튼이 함께 있다.
export function TempPasswordPanel({
  email, password, onClose,
}: {
  email: string;
  password: string;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(password);
      setCopied(true);
    } catch {
      // 클립보드 권한이 없는 브라우저 — 값은 화면에 보이므로 수동 복사가 가능하다.
      setCopied(false);
    }
  }

  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4">
      <p className="text-sm font-medium text-amber-900">
        <span>{email}</span> 의 임시 비밀번호
      </p>
      <div className="mt-2 flex items-center gap-2">
        <code className="flex-1 rounded bg-white px-3 py-2 font-mono text-sm border border-amber-200">
          {password}
        </code>
        <button
          type="button"
          onClick={copy}
          className="rounded-lg bg-amber-600 px-3 py-2 text-sm text-white hover:bg-amber-700"
        >
          복사
        </button>
      </div>
      {copied && <p className="mt-1 text-xs text-amber-700">복사했습니다.</p>}
      <p className="mt-2 text-xs text-amber-800">
        이 창을 닫으면 <strong>다시 볼 수 없습니다</strong>. 사용자에게 전달하세요.
        사용자는 첫 로그인에서 비밀번호를 변경합니다.
      </p>
      <button
        type="button"
        onClick={onClose}
        className="mt-3 rounded-lg border border-amber-300 px-3 py-1.5 text-sm text-amber-900 hover:bg-amber-100"
      >
        확인
      </button>
    </div>
  );
}
