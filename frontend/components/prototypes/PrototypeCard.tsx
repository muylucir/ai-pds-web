// frontend/components/prototypes/PrototypeCard.tsx — one prototype's status +
// action buttons, following ArtifactCard's visual idiom (rounded-xl border,
// icon tile, violet primary / slate neutral) with StageTimeline's badge
// pattern (rounded-full pill, per-status color, pulsing while active).
//
// State machine mirrors Task 7's list_prototypes contract exactly:
//   none -> building -> built -> running
//                          \-------> failed
"use client";
import { useEffect, useRef, useState } from "react";

import type { HostState, PrototypeInfo, PrototypeState } from "@/lib/api/prototypes";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

// Colour-free geometry, shared by every button below. Colour classes are
// each button's OWN set (never composed on top of another button's colour
// classes): Tailwind emits utility rules in a fixed internal order that
// ignores className string order, so appending e.g. `text-rose-600` after a
// constant that already carries `text-slate-700` does NOT override it — the
// slate rule wins the cascade regardless of which comes later in the string.
// Composing SECONDARY_BTN + rose overrides was tried and silently rendered
// as slate; this shape avoids ever having two same-property colour
// utilities on one element.
const BTN_BASE = "px-3.5 py-2 rounded-lg text-sm font-medium disabled:opacity-50";
const PRIMARY_BTN = `${BTN_BASE} bg-violet-600 hover:bg-violet-700 text-white disabled:hover:bg-violet-600`;
const SECONDARY_BTN = `${BTN_BASE} border border-slate-200 hover:bg-slate-50 text-slate-700 disabled:hover:bg-transparent`;
const DANGER_BTN = `${BTN_BASE} border border-slate-200 hover:bg-rose-50 text-rose-600 disabled:hover:bg-transparent`;

// 라벨은 딕셔너리 키다 — 모듈 상수는 훅을 부를 수 없으므로 렌더에서 푼다.
const BADGE: Record<PrototypeState, { labelKey: keyof Dict; cls: string }> = {
  none: { labelKey: "proto.statusNone", cls: "bg-slate-100 text-slate-500" },
  building: { labelKey: "proto.statusBuilding", cls: "bg-violet-100 text-violet-700 animate-pulse" },
  built: { labelKey: "proto.statusBuilt", cls: "bg-emerald-50 text-emerald-700" },
  running: { labelKey: "proto.statusRunning", cls: "bg-sky-50 text-sky-700" },
  failed: { labelKey: "proto.statusFailed", cls: "bg-rose-50 text-rose-700" },
};

export function PrototypeCard({
  info,
  onBuild,
  onOpenPreview,
  onStartHost,
  onStopHost,
  onShowLogs,
  onOpenSurvey,
  onReset,
  startingPhase,
  archiveUrl,
  shareUrl,
  busy,
}: {
  info: PrototypeInfo;
  onBuild: () => void;
  onOpenPreview?: () => void;
  onStartHost: () => void;
  onStopHost: () => void;
  onShowLogs?: () => void;
  onOpenSurvey?: () => void;
  /** Wipe build + session + survey, keeping the spec. Rendered only when
   *  `info.state !== "none"` — there is nothing accumulated to clear. */
  onReset?: (slug: string) => void;
  /** 호스팅 시작이 진행 중일 때의 단계. `POST /host`가 npm install → build →
   *  포트 대기를 전부 await하므로(routes/prototypes.py), 그동안 카드에는 비활성
   *  버튼밖에 없어 "아무 반응 없음"으로 읽혔다. 호출부가 `GET /host`를 폴링해
   *  넘겨준다 — 서버가 이미 기록하는 상태다. */
  startingPhase?: HostState | null;
  archiveUrl?: string;
  /** 공유용 절대 URL(`absoluteShareUrl`이 서버의 `access_url`을 절대화한 값).
   *  주어지고 호스팅 중일 때만 복사 버튼이 뜬다 — 그 밖의 상태에서는 링크가
   *  502이므로 깨진 링크를 공유하게 된다(routes/proto_public.py).
   *
   *  이 URL에는 **접근 토큰이 들어 있다**. 참가자는 이것만으로 프로토타입에
   *  들어오므로, 공유 범위가 곧 접근 범위다. */
  shareUrl?: string;
  busy: boolean;
}) {
  const t = useT();
  const badge = BADGE[info.state];
  const label = t(badge.labelKey);
  const badgeLabel =
    info.state === "running" && info.port != null ? `${label} :${info.port}` : label;

  // 호스팅 시작 중이면 목록의 상태(`built`)보다 **더 정확한 것**을 안다.
  // `POST /host`가 npm install → build → 포트 대기를 전부 await하는 동안 카드가
  // "빌드 완료 + 비활성 버튼"으로 멈춰 있었다 — 실측 13분짜리 구간이고, 사용자가
  // "아무 반응 없음"으로 읽었다. 그 단계는 서버가 이미 기록한다.
  //
  // `failed`/`stopped`는 여기서 쓰지 않는다: 그때는 POST가 이미 반환했고
  // (실패는 502) 목록이 다시 읽히므로, 진행 표시가 남아 있으면 오히려 낡은 정보다.
  const PHASE_KEYS: Partial<Record<HostState, keyof Dict>> = {
    installing: "proto.hostInstalling",
    building: "proto.hostBuildingHost",
    running: "proto.hostStarting",
  };
  const phaseKey = startingPhase ? PHASE_KEYS[startingPhase] : undefined;

  return (
    <div className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 flex items-center gap-3">
      <span
        className="w-10 h-10 rounded-lg bg-violet-50 text-violet-600 flex items-center justify-center text-lg shrink-0"
        aria-hidden="true"
      >
        🧪
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          {/* 제목은 이름이고, 없을 때만 슬러그다. 단일 프로토타입 레이아웃의
              슬러그는 상수 `"prototype"`이라(백엔드 proto/layout.py) 이 폴백이
              모든 Path A.1 프로젝트에서 같은 제목을 만들었다. 슬러그 자체는
              식별자로 남아 리셋 aria-label과 아래 명세 경로에 계속 나온다. */}
          <span className="font-medium text-sm truncate">{info.name ?? info.slug}</span>
          <span
            className={`text-[11px] px-2 py-0.5 rounded-full shrink-0 ${
              phaseKey
                ? "bg-violet-100 text-violet-700 animate-pulse"
                : badge.cls
            }`}
            // 진행 중임을 스크린 리더에도 알린다 — 텍스트가 바뀌는 것만으로는
            // 읽어 주지 않는다.
            aria-live={phaseKey ? "polite" : undefined}
          >
            {phaseKey ? t(phaseKey) : badgeLabel}
          </span>
          {/* 설문 상태. **`state !== "none"`일 때만 건다** — 아직 빌드할 것도
              없는 단계에서 "설문 없음"은 할 일처럼 읽혀 잡음이 된다.

              `has_survey`가 필요한 이유는 `response_count`가 두 상태에서 같기
              때문이다: 설문이 없어도 0, 설문이 있고 응답이 아직 없어도 0. 실측
              test2222에서 프로토타입 3개 중 1개에만 설문이 있었는데 카드에 그
              사실이 없어 나머지 둘이 빠진 것을 알아차릴 방법이 없었다. */}
          {info.state !== "none" && (
            <span
              className={`text-[11px] shrink-0 ${
                info.has_survey ? "text-slate-500" : "text-amber-600"
              }`}
            >
              {info.has_survey
                ? t("proto.surveyResponses").replace("{n}", String(info.response_count))
                : t("proto.surveyNone")}
            </span>
          )}
          {/* 떠 있는 서버가 소스보다 오래된 구간. 참가자에게 나간 링크는 계속
              살아 있지만 보여 주는 것은 **이전 버전**이다 — 새 코드는 다시
              호스팅해야 반영된다.

              **`session_open`이 아니라 `preview_stale`로 판정한다.** 세션 기준이면
              세션이 닫히는 순간 경고가 사라지는데, 정작 그때가 사용자가 "반영됐다"고
              오해하기 가장 쉬운 시점이다(서버는 여전히 이전 버전을 서빙한다).

              고치는 방법의 **대가를 함께 말한다**: 다시 호스팅은 npm install →
              build → 서버 시작을 전부 기다리므로(실측 최대 13분) 그 동안 참가자
              링크까지 닫힌다. 확인 대화상자를 하나 더 만들지 않고 버튼 옆에 두는
              이유는, 누르기 전에 읽히는 자리가 여기이기 때문이다. */}
          {info.preview_stale && (
            <span className="text-[11px] shrink-0 text-amber-600">
              {t("proto.previewStale")} · {t("proto.rehostDowntime")}
            </span>
          )}
        </div>
        <span className="block text-[11px] text-slate-400 mt-0.5 truncate">{info.spec_path}</span>
      </div>
      {/* flex-wrap: `running`은 프리뷰·링크·중지·로그·다운로드·수정·초기화·설문로
          버튼이 여덟까지 간다. shrink-0인 버튼들이 감싸지 않으면 좁은 화면에서
          카드 밖으로 넘친다. */}
      <div className="flex flex-wrap justify-end items-center gap-2 shrink-0">
        {info.state === "none" && (
          <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onBuild}>
            {t("proto.startBuild")}
          </button>
        )}
        {info.state === "building" && (
          <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onBuild}>
            {t("proto.openSession")}
          </button>
        )}
        {info.state === "built" && (
          <>
            <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onStartHost}>
              {t("proto.startHosting")}
            </button>
            <BuildButton info={info} busy={busy} onBuild={onBuild} />
            {archiveUrl && <ArchiveLink href={archiveUrl} />}
          </>
        )}
        {info.state === "running" && (
          <>
            {onOpenPreview && (
              <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onOpenPreview}>
                {t("proto.openPreview")}
              </button>
            )}
            {shareUrl && <CopyLinkButton url={shareUrl} disabled={busy} />}
            {/* 낡았을 때만 나온다. 항상 두면 "다시 호스팅"이 무엇을 바꾸는지 알 수
                없는 버튼이 되고(이미 최신이면 몇 분간 프리뷰만 닫는다), 낡았을 때는
                반대로 그것이 유일하게 필요한 동작이다. 위 경고 줄이 그 대가를 말한다. */}
            {info.preview_stale && (
              <button type="button" className={SECONDARY_BTN} disabled={busy}
                      onClick={onStartHost}>
                {t("proto.rehost")}
              </button>
            )}
            {/* 실행 중에도 수정할 수 있다. **여기 있어야 하는 이유**는 이것이
                고치고 싶어지는 순간이기 때문이다 — 프리뷰를 방금 본 직후. 종전에는
                이 상태에 빌드 관련 버튼이 아예 없어서, 호스팅을 먼저 중지해야
                수정 버튼이 돌아왔다. */}
            <BuildButton info={info} busy={busy} onBuild={onBuild} />
            <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onStopHost}>
              {t("proto.stopHosting")}
            </button>
            {onShowLogs && (
              <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onShowLogs}>
                {t("proto.logs")}
              </button>
            )}
            {archiveUrl && <ArchiveLink href={archiveUrl} />}
          </>
        )}
        {info.state === "failed" && (
          <>
            <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onBuild}>
              {t("proto.continueBuild")}
            </button>
            {onShowLogs && (
              <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onShowLogs}>
                {t("proto.logs")}
              </button>
            )}
          </>
        )}
        {info.state !== "none" && onReset && (
          <button
            type="button"
            aria-label={`${info.slug} ${t("proto.resetAria")}`}
            disabled={busy}
            onClick={() => onReset(info.slug)}
            className={DANGER_BTN}
          >
            {t("proto.reset")}
          </button>
        )}
        {/* 설문은 빌드 상태와 무관하게 항상 열 수 있다: 빌드 전 단계에서
            문항을 미리 만들 수도 있고, 호스팅이 끝난 뒤 응답을 집계할 수도
            있다. 빌드 드로어와 별개의 상태로 열리므로 서로 가리지 않는다. */}
        {onOpenSurvey && (
          <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onOpenSurvey}>
            {t("proto.survey")}
          </button>
        )}
      </div>
    </div>
  );
}

/** 이미 만들어진 프로토타입에 손을 대는 버튼. **하나의 동작에 두 이름**이다.
 *
 *  세션이 이미 열려 있으면 할 일은 그 세션으로 돌아가는 것이므로 "세션 열기"다.
 *  아니면 새 세션을 여는 것이고, 그 세션이 무엇을 물을지는 백엔드가 이미 갈라
 *  놓았다 — 완료된 빌드에는 "무엇을 개선할지", 완료 없이 죽은 빌드에는 "무엇을
 *  이어갈지"(proto/session의 handoff·resume 분기). 사용자 쪽 의도로는 둘 다
 *  수정이므로 한 이름으로 부른다.
 *
 *  `failed`가 이 컴포넌트를 쓰지 않는 이유: 그 상태에서 필요한 것은 수정이 아니라
 *  마치는 것이고("이어서 하기"), 버튼이 primary여야 한다 — 그 카드에는 다른
 *  주된 동작이 없다. */
function BuildButton({
  info, busy, onBuild,
}: { info: PrototypeInfo; busy: boolean; onBuild: () => void }) {
  const t = useT();
  return (
    <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onBuild}>
      {t(info.session_open ? "proto.openSession" : "proto.modify")}
    </button>
  );
}

/** 프리뷰 링크를 클립보드로. 워크숍에서 참가자에게 링크를 나눠 주는 것이
 *  용도이므로 값은 **절대 URL**이어야 한다(호출부가 `absoluteShareUrl`로 만든다).
 *
 *  "복사됨"을 2초 후 되돌리는 이유: 두 번째 복사가 실제로 됐는지 화면에서
 *  구별되어야 한다. 라벨이 영구히 "복사됨"이면 눌렀는지 알 수 없다.
 *
 *  실패를 삼키지 않는다 — `navigator.clipboard`는 비-HTTPS 오리진이나 권한
 *  거부에서 없거나 던진다. 그때 "복사됨"을 띄우면 사용자가 빈 클립보드를
 *  붙여넣고 원인을 알 수 없다. */
function CopyLinkButton({ url, disabled }: { url: string; disabled: boolean }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 언마운트(호스팅 중지·리스트 갱신) 후 setState가 불리지 않게 정리한다.
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      return;   // 성공한 척하지 않는다
    }
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2000);
  }

  return (
    <button type="button" className={SECONDARY_BTN} disabled={disabled}
            onClick={() => void copy()}>
      {copied ? t("proto.copied") : t("proto.copyLink")}
    </button>
  );
}

/** An <a>, not a button: the dev-team handoff is a plain file download, so
 *  the browser handles Content-Disposition and the filename (same shape as
 *  the survey CSV link). */
function ArchiveLink({ href }: { href: string }) {
  const t = useT();
  return (
    <a href={href} className={SECONDARY_BTN}>
      {t("proto.download")}
    </a>
  );
}
