// frontend/lib/useProjectModel.ts
//
// The two values the header badge shows: the display name of the model this project runs on,
// and the output language. They vary by project, so without them on screen you cannot tell
// what it is running on right now.
//
// Why two calls for the model: the project knows only model_id (the value copied into the
// manifest), and the human-readable name exists only in the catalog. A failed lookup is a
// normal path — a project may run on a model the admin deleted from the catalog, and then the
// raw id is shown (the consequence of the copy-the-value design should be honest on screen too).
//
// The language needs no such lookup — the value itself is the information to display.
//
// The permission (access) is read here too. The project screen already reads the project once
// through this hook, so we do not make another call for the value that decides whether to draw
// write buttons.
"use client";
import { useEffect, useState } from "react";

import { getProject } from "@/lib/api/client";
import { canWrite, type ProjectAccess } from "@/lib/api/types";
import { listModels } from "@/lib/api/models";
import { isLocale, type Locale } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";
import { modelLabel } from "@/lib/modelLabel";

export interface ProjectMeta {
  /** Model display name. null = unspecified (server env default) or lookup failed. */
  modelLabel: string | null;
  /** Output language. null = old backend response (no field) or lookup failed. */
  language: Locale | null;
  /** This requester's permission. null = not known yet (before lookup, or failed). */
  access: ProjectAccess | null;
  /** A viewing member — the screen does not draw write buttons. false while unknown: the server
   *  is the final gate, and the owner's buttons flickering away while awaiting the response is worse. */
  readOnly: boolean;
}

const EMPTY: ProjectMeta = { modelLabel: null, language: null, access: null, readOnly: false };

export function useProjectMeta(projectId: string | undefined): ProjectMeta {
  const [meta, setMeta] = useState<ProjectMeta>(EMPTY);
  const t = useT();

  useEffect(() => {
    if (!projectId) {
      setMeta(EMPTY);
      return;
    }
    let alive = true;
    // A failure just means the badge is missing — it does not block anything else on screen.
    void Promise.all([
      getProject(projectId),
      listModels().catch(() => []),
    ])
      .then(([project, models]) => {
        if (!alive) return;
        const id = project.model_id;
        setMeta({
          // Unspecified: the server runs on its env default, which the frontend cannot know.
          // effort is the value the project copied — it may differ from the catalog's current
          // value, and this is the one actually in effect.
          modelLabel: id
            ? modelLabel(models.find((m) => m.model_id === id)?.name ?? id, project.effort, t)
            : null,
          // Narrow with isLocale — old backends lack this field, and a corrupted response
          // could carry an arbitrary string. In that case no badge is drawn.
          language: isLocale(project.language) ? project.language : null,
          access: project.access ?? null,
          readOnly: !canWrite(project.access),
        });
      })
      .catch(() => {
        if (alive) setMeta(EMPTY);
      });
    return () => { alive = false; };
  }, [projectId, t]);

  return meta;
}
