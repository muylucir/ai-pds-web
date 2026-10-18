"use client";
// frontend/lib/useScrollSpy.ts — reports which anchor is currently visible on screen.
//
// Used by the manual's table of contents to show "you are reading here". Why we do not measure
// offsetTop on scroll events: it forces a layout computation (reflow) on every frame,
// and scrolling stutters noticeably on long documents.
// IntersectionObserver hands that computation to the browser.
import { useEffect, useState } from "react";

/**
 * The id of the first anchor among `ids` that is visible on screen.
 *
 * - If `enabled` is false, it does not observe and returns null (while searching, the body is
 *   filtered, so "current position" loses its meaning).
 * - In environments without IntersectionObserver (jsdom, very old browsers), it
 *   quietly returns null — the table of contents works as-is, without highlighting. Throwing would
 *   keep the entire manual from rendering.
 */
export function useScrollSpy(ids: string[], enabled = true): string | null {
  const [active, setActive] = useState<string | null>(null);
  // An array literal is a new reference on every render, so depend on its value.
  const key = ids.join(",");

  useEffect(() => {
    if (!enabled) {
      setActive(null);
      return;
    }
    if (typeof IntersectionObserver === "undefined") return;

    const list = key ? key.split(",") : [];
    const seen = new Map<string, boolean>();

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          seen.set(entry.target.id, entry.isIntersecting);
        }
        // Pick the first visible one **in document order**, not in entries order —
        // the callback carries only what changed and does not guarantee order, so picking from entries
        // would light up a lower section when scrolling up.
        const first = list.find((id) => seen.get(id));
        if (first) setActive(first);
      },
      {
        // Top margin: the area hidden behind the sticky header (h-16) is not "visible".
        // Trim the bottom heavily so only sections near the top of the screen remain candidates.
        rootMargin: "-72px 0px -70% 0px",
      },
    );

    const elements = list
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    for (const el of elements) observer.observe(el);

    return () => observer.disconnect();
  }, [key, enabled]);

  return active;
}
