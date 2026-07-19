import { redirect } from "next/navigation";

// Retired (Task 11): the standalone "질문 답변" tab is replaced by the
// unified 3-pane /workspace screen, whose right panel (or mobile bottom
// sheet) shows QuestionForm whenever a question interrupt is pending. This
// route stays only as a server-side redirect so old links/bookmarks keep
// working.
export default async function QuestionsPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  redirect(`/projects/${projectId}/workspace`);
}
