// frontend/lib/useProjectModel.ts
//
// 헤더 배지가 보여줄 두 값: 이 프로젝트가 도는 모델의 표시 이름과, 생성물
// 언어. 프로젝트마다 다르므로 화면에 없으면 지금 무엇으로 도는지 알 수 없다.
//
// 모델을 두 번 부르는 이유: 프로젝트는 model_id만 알고(매니페스트에 복사된 값),
// 사람이 읽는 이름은 카탈로그에만 있다. 대조 실패는 정상 경로다 — 관리자가
// 카탈로그에서 지운 모델로 도는 프로젝트가 있을 수 있고, 그때는 id 원문을
// 보여준다(값을 복사해 두는 설계의 결과가 화면에서도 정직해야 한다).
//
// 언어는 그런 대조가 필요 없다 — 값 자체가 표시할 정보다.
"use client";
import { useEffect, useState } from "react";

import { getProject } from "@/lib/api/client";
import { listModels } from "@/lib/api/models";
import { isLocale, type Locale } from "@/lib/i18n";

export interface ProjectMeta {
  /** 모델 표시 이름. null = 미지정(서버 env 기본값) 또는 조회 실패. */
  modelLabel: string | null;
  /** 생성물 언어. null = 구 백엔드 응답(필드 없음) 또는 조회 실패. */
  language: Locale | null;
}

const EMPTY: ProjectMeta = { modelLabel: null, language: null };

export function useProjectMeta(projectId: string | undefined): ProjectMeta {
  const [meta, setMeta] = useState<ProjectMeta>(EMPTY);

  useEffect(() => {
    if (!projectId) {
      setMeta(EMPTY);
      return;
    }
    let alive = true;
    // 실패는 배지가 빠지는 것으로 끝난다 — 화면의 다른 것을 막지 않는다.
    void Promise.all([
      getProject(projectId),
      listModels().catch(() => []),
    ])
      .then(([project, models]) => {
        if (!alive) return;
        const id = project.model_id;
        setMeta({
          // 미지정: 서버가 env 기본값으로 도는데 그 값을 프론트는 알 수 없다.
          modelLabel: id ? models.find((m) => m.model_id === id)?.name ?? id : null,
          // isLocale로 좁힌다 — 구 백엔드는 이 필드가 없고, 손상된 응답이
          // 임의 문자열을 실어 올 수도 있다. 그때는 배지를 그리지 않는다.
          language: isLocale(project.language) ? project.language : null,
        });
      })
      .catch(() => {
        if (alive) setMeta(EMPTY);
      });
    return () => { alive = false; };
  }, [projectId]);

  return meta;
}
