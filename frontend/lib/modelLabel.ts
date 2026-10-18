// frontend/lib/modelLabel.ts — appends effort to the model display name.
//
// Even for the same model, speed varies greatly with effort (Opus 5.5 medium → high: turn time
// +53%). So the places that show the model (the creation combo box, the header badge, the project list) show
// effort alongside it. It is kept in one function so that the notation in those three places does not diverge.
//
// If there is no effort (null — the CLI default, projects from before this field), only the name is used. What the
// default is differs per model and the frontend cannot know it, so we do not make it up.
import type { Dict } from "@/lib/i18n";

export function modelLabel(
  name: string, effort: string | null | undefined, t: (key: keyof Dict) => string,
): string {
  if (!effort) return name;
  return t("model.withEffort").replace("{name}", name).replace("{effort}", effort);
}
