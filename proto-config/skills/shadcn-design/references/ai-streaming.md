# shadcn AI 스트리밍 레퍼런스 (MarkdownContent + useAIStreaming)

AI FR이 있으면 `check-markdown-render.mjs`(sub-check [J])가 아래를 **정적으로 강제**한다(DS-무관):
1. `package.json`에 `react-markdown` + `remark-gfm`
2. `src/`에 `useAIStreaming` 훅 사용 컴포넌트 ≥1
3. `src/`에 `react-markdown` import + `<ReactMarkdown` 또는 `<MarkdownContent` JSX ≥1
4. `useAIStreaming` 사용 컴포넌트는 직접/간접으로 react-markdown 렌더

**마크다운 파싱은 DS-무관**(react-markdown 공통). shadcn 어댑터는 **코드블록/링크 렌더만 shadcn 스타일**로 매핑한다.

## 패턴 1: useAIStreaming 훅 (SSE)

wire event_type SSOT는 cloudscape와 동일: `text` / `tool_start` / `tool_end` / `error` / `done` (필드 `content`/`name`/`message`/`messageId`). SSE 파싱 훅 자체는 DS-무관이므로 cloudscape 스킬의 `references/ai-streaming.md` 패턴 1과 동일 구현을 쓴다(전송·상태관리는 DS와 무관). `src/hooks/useAIStreaming.ts`에 `{ messages, send, stop, ... }` 반환.

## 패턴 2: AI 채팅 — Markdown 스트리밍 렌더링 (shadcn 버전)

코드블록/링크만 shadcn/Tailwind로 매핑. **react-markdown+remark-gfm은 그대로**([J] 계약 충족).

> **코드 문법 강조는 코드-중심 chat에서 필수 (FR "코드 블록 문법 강조" AC).** shadcn은 Cloudscape의 `CodeView` 같은 내장 하이라이터가 없다 — 그래서 어댑터가 **명시적으로** 하이라이터를 채워야 한다. 죽은 `<pre><code className="language-ts">`(강조 안 됨)로 끝내지 말 것. 표준 = `react-syntax-highlighter`(Prism). API 문서/개발자 도구처럼 **코드 예제 가독성이 요구에 명시된 chat `ui_type`이면 하이라이터는 "선택"이 아니라 필수**다([J] 게이트가 chat일 때 하이라이터 의존성+배선을 정적 강제). 순수 대화/요약 등 코드가 없는 챗이면 생략 가능.

`react-syntax-highlighter`는 코드블록 렌더 컴포넌트로 분리한다(복사 버튼 공존 + 다크모드 테마 전환). 아래 `CodeBlock`이 그것:

```tsx
// src/components/chat/CodeBlock.tsx
'use client';

import { Check, Copy } from 'lucide-react';
import { useState } from 'react';
import { useTheme } from 'next-themes';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Button } from '@/components/ui/button';

/** 문법 강조 + 복사 버튼이 달린 코드 블록. */
export function CodeBlock({ code, language }: { code: string; language?: string }) {
  const { resolvedTheme } = useTheme();
  const [copied, setCopied] = useState(false);
  // 다크/라이트 테마에 맞춰 하이라이트 스타일을 전환한다(next-themes 연동, NFR 다크모드).
  const style = resolvedTheme === 'dark' ? oneDark : oneLight;

  async function handleCopy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 클립보드 접근 불가(비 HTTPS 등) 시 조용히 무시 — 복사 실패는 치명적이지 않음.
    }
  }

  return (
    <div className="relative my-2 w-full">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={handleCopy}
        aria-label="코드 복사"
        className="absolute right-2 top-2 z-10 text-muted-foreground"
      >
        {copied ? <Check className="text-green-500" /> : <Copy />}
      </Button>
      <SyntaxHighlighter
        language={language ?? 'text'}
        style={style}
        customStyle={{ margin: 0, borderRadius: '0.375rem', fontSize: '0.875rem', paddingRight: '3rem' }}
        PreTag="div"
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}
```

```tsx
// src/components/chat/MarkdownContent.tsx
'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CodeBlock } from '@/components/chat/CodeBlock';

/** 마크다운 텍스트를 shadcn/Tailwind 스타일로 렌더링한다 */
export function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // 펜스 코드 → CodeBlock(react-syntax-highlighter 문법 강조 + 복사). 인라인 코드 → 간단 스타일.
          code({ className, children }) {
            const match = /language-(\w+)/.exec(className ?? '');
            const text = String(children).replace(/\n$/, '');
            if (match || text.includes('\n')) {
              return <CodeBlock code={text} language={match?.[1]} />;
            }
            return <code className="rounded bg-muted px-1 py-0.5 text-sm">{text}</code>;
          },
          // 링크 → shadcn 스타일 앵커 (외부 링크)
          a({ href, children }) {
            return (
              <a
                href={href ?? '#'}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary underline underline-offset-4 hover:no-underline"
              >
                {children}
              </a>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
```

> `prose`(@tailwindcss/typography)는 마크다운 기본 요소(제목/목록/볼드) 스타일을 제공. chat `ui_type`이면 설치를 기본으로 한다(미설치 시 최소 Tailwind 클래스로 대체 가능). **하이라이터 의존성**: `npm install react-syntax-highlighter @types/react-syntax-highlighter`. shiki/`rehype-highlight`로 대체해도 무방하나(게이트는 DS-무관하게 하이라이터 존재+배선만 강제), Prism 방식이 복사 버튼·테마 전환과 가장 단순하게 맞물린다.

## 패턴 3: ChatPanel에서 사용

assistant/AI 응답 자리에 `<MarkdownContent content={msg.content} />`. user 메시지는 raw text 허용(마크다운 의도 없음). 메시지 목록은 스크롤 컨테이너 + 하단 입력(`Textarea` + `Button`). 스트리밍 중 `send`/`stop` 버튼 토글.

> **스크롤 컨테이너 (공식 채팅 프리미티브 — 선택)**: 공식 shadcn은 채팅에 `MessageScroller`(+`MessageScrollerProvider`/`Viewport`/`Content`/`Item`)와 `Message`/`Bubble`을 두어 자동 하단 고정·위치 복원·jump-to-latest를 내장 제공한다(raw overflow 컨테이너나 `ScrollArea` 수동 배선을 지양). 채팅이 길거나 스크롤 UX가 중요하면 `MessageScroller`를 채택할 수 있다. **단 데이터는 `useAIStreaming`, assistant 본문은 `MarkdownContent`(react-markdown)를 유지**해야 `[J]` 게이트를 통과한다 — 프리미티브는 프레젠테이션 쉘로만 쓰고 스트리밍/본문 계약을 대체하지 않는다(AI Elements 패턴 4와 동일한 경계).

상세 shadcn 채팅 블록: WebFetch `https://ui.shadcn.com/blocks` (chat 관련) 또는 커스텀.

## 패턴 4: AI Elements (선택 — chat/AI-native `ui_type`에서 프레젠테이션 컴포넌트만 채택)

**AI Elements**(Vercel)는 shadcn/ui 위에 얹은 **AI 특화 커스텀 레지스트리**다 — shadcn과 동일한 copy-in 모델(설치 시 `@/components/ai-elements/`에 소스가 들어와 소유). chat 계열 `ui_type`에서 손수 조립하는 대신 성숙한 AI UI 컴포넌트를 쓸 수 있다.

**설치** (shadcn CLI 경유 — 필요한 것만):
```bash
npx shadcn@latest add https://registry.ai-sdk.dev/conversation.json
npx shadcn@latest add https://registry.ai-sdk.dev/message.json
# 또는: npx ai-elements@latest add conversation message sources reasoning tool
```

**chat `ui_type`에 유용한 컴포넌트** (전체는 https://elements.ai-sdk.dev/overview):

| 컴포넌트 | 용도 | 하네스 매핑 |
|---|---|---|
| `conversation` | 스크롤 채팅 셸(자동 하단 고정) | 메시지 목록 컨테이너 |
| `message` | 개별 메시지(role별 정렬/아바타) | user/assistant 버블 |
| `prompt-input` | 입력 영역(전송/중단 버튼 내장) | `send`/`stop`에 배선 |
| `sources` / `inline-citation` | 출처 목록·본문 인용 | `citations[]` 렌더(DevDocs 시나리오 §citation) |
| `reasoning` / `chain-of-thought` | 추론 과정 표시(접기/펼치기) | (있으면) reasoning 이벤트 |
| `tool` | 도구 호출·파라미터·결과 표시 | `tool_start`/`tool_end` 이벤트(Ops Copilot §도구 투명성) |
| `code-block` | 문법 강조 코드 블록 | MarkdownContent의 code 렌더 대체 가능 |

### ★경계 (필수 준수) — AI SDK 훅 배제, `useAIStreaming`에 배선★

AI Elements는 **Vercel AI SDK(`ai` 패키지)에 깊게 결합**돼 있다(`useChat`/`useCompletion`/`streamText` 등 — 스트리밍·상태·타입세이프를 AI SDK가 제공). 이 하네스는 **AI를 Strands SDK + Bedrock으로 구현**하고(Rule 9), SSE 스트리밍은 `useAIStreaming` 계약(`text`/`tool_start`/`tool_end`/`error`/`done`)이 SSOT다. 따라서:

- ✅ **채택**: AI Elements의 **프레젠테이션 컴포넌트만**(`conversation`/`message`/`sources`/`reasoning`/`tool`/`prompt-input`의 UI 부분). 이들은 순수 렌더 컴포넌트라 데이터를 props로 받는다.
- ❌ **배제**: AI Elements/AI SDK의 **데이터 훅**(`useChat`, `useCompletion`, `@ai-sdk/react`), `transport`, `DefaultChatTransport` 등. 이걸 쓰면 하네스의 SSE 계약과 이중 전송이 생겨 `[J]` 게이트·strands SSOT와 충돌한다.
- **데이터 소스 = `useAIStreaming`**: 컴포넌트에 넘기는 messages/content/activeTool/citations는 전부 `useAIStreaming()` 반환값에서 나온다. `useChat()`을 호출하지 않는다.
- **마크다운 본문 = 여전히 `react-markdown`**: `message` 안의 assistant 텍스트는 `<MarkdownContent>`(react-markdown+remark-gfm)로 렌더한다 — `[J]` 게이트가 `useAIStreaming` + react-markdown/`<MarkdownContent>` 사용을 정적 강제하므로, AI Elements를 써도 이 두 요소는 **반드시 유지**한다(AI Elements의 자체 markdown 렌더러로 대체 금지 — 게이트 FAIL).

**배선 예시 (개념)**:
```tsx
'use client';
import { Conversation, ConversationContent } from '@/components/ai-elements/conversation';
import { Message, MessageContent } from '@/components/ai-elements/message';
import { PromptInput } from '@/components/ai-elements/prompt-input';
import { MarkdownContent } from '@/components/chat/MarkdownContent';
import { useAIStreaming } from '@/hooks/useAIStreaming';  // ★AI SDK useChat 아님★

export function ChatPanel() {
  const { messages, content, isStreaming, activeTool, send, stop } = useAIStreaming('/api/chat');
  return (
    <Conversation>
      <ConversationContent>
        {messages.map((m) => (
          <Message key={m.id} from={m.role}>
            <MessageContent>
              {/* assistant 본문은 [J] 계약대로 react-markdown 유지 (raw {content} 직접 노출 금지) */}
              {m.role === 'assistant'
                ? <MarkdownContent content={m.content} />
                : <span>{m.content}</span>}
            </MessageContent>
          </Message>
        ))}
      </ConversationContent>
      <PromptInput onSubmit={send} onStop={stop} disabled={isStreaming} />
    </Conversation>
  );
}
```

> **정직 경계**: AI Elements는 **선택 강화**다 — shadcn 어댑터 자체는 ready(2026-07-14 승격)이나, AI Elements 통합은 아직 실 앱 검증 이력이 없다. chat `ui_type`이 아니거나 단순 Q&A면 패턴 2(기본 MarkdownContent)로 충분하며 AI Elements를 강제하지 않는다. 채택 시에도 위 경계(AI SDK 훅 배제 + `useAIStreaming` + react-markdown 유지)를 벗어나면 `[J]` 게이트가 FAIL시킨다.
