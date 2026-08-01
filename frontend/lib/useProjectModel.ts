// frontend/lib/useProjectModel.ts
//
// 헤더 배지가 보여줄 모델 라벨. 프로젝트마다 모델이 다르면 지금 무엇으로
// 도는지 화면에 없으면 알 수 없다.
//
// 두 번 부르는 이유: 프로젝트는 model_id만 알고(매니페스트에 복사된 값),
// 사람이 읽는 이름은 카탈로그에만 있다. 대조 실패는 정상 경로다 — 관리자가
// 카탈로그에서 지운 모델로 도는 프로젝트가 있을 수 있고, 그때는 id 원문을
// 보여준다(값을 복사해 두는 설계의 결과가 화면에서도 정직해야 한다).
"use client";
import { useEffect, useState } from "react";

import { getProject } from "@/lib/api/client";
import { listModels } from "@/lib/api/models";

export function useProjectModel(projectId: string | undefined): string | null {
  const [label, setLabel] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) {
      setLabel(null);
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
        if (!id) {
          // 미지정: 서버가 env 기본값으로 도는데 그 값을 프론트는 알 수 없다.
          setLabel(null);
          return;
        }
        setLabel(models.find((m) => m.model_id === id)?.name ?? id);
      })
      .catch(() => {
        if (alive) setLabel(null);
      });
    return () => { alive = false; };
  }, [projectId]);

  return label;
}
