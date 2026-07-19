import { redirect } from "next/navigation";

// Retired (Task 11): the standalone "빌드 캔버스" tab is replaced by the
// unified 3-pane /workspace screen. This route stays only as a server-side
// redirect so old links/bookmarks keep working.
export default async function CanvasPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  redirect(`/projects/${projectId}/workspace`);
}
