# antd AI 스트리밍 레퍼런스 (@ant-design/x + MarkdownContent)

AI 기능이 있는 프로토타입은 아래를 갖춘다(디자인 시스템과 무관한 공통 요구):
1. `package.json`에 `react-markdown` + `remark-gfm`
2. `useAIStreaming` 훅을 쓰는 컴포넌트 ≥1
3. assistant 본문은 `MarkdownContent`(react-markdown)로 렌더

패턴 0·1(서버 라우트·스트리밍 훅)은 디자인 시스템과 무관하고, 패턴 2·3이 antd 렌더링이다.

## 패턴 0: 서버 라우트 — Strands 에이전트를 SSE로 흘린다

에이전트는 **서버측에서만** 돈다(`proto-config/CLAUDE.md`의 "Agentic prototypes" 절).
아래 매핑은 이 배포에서 실측한 이벤트 형태다(2026-08-17, `@strands-agents/sdk` 1.13.0):

| Strands 이벤트 | 꺼내는 곳 | wire 이벤트 |
|---|---|---|
| `modelStreamUpdateEvent` → `event.type === 'modelContentBlockDeltaEvent'` | `event.delta.text` (단, `event.delta.type === 'textDelta'`일 때만) | `text` (`content`) |
| `beforeToolCallEvent` | `toolUse.name` | `tool_start` (`name`) |
| `afterToolCallEvent` | `toolUse.name`, `result.status` | `tool_end` (`name`) |
| `agentResultEvent` | — | `done` |
| (throw) | `err.message` | `error` (`message`) |

`modelStreamUpdateEvent.event`의 나머지 변종(`modelMessageStartEvent`,
`modelContentBlockStopEvent`, `modelMessageStopEvent`, `modelMetadataEvent`)은
UI가 쓰지 않으므로 흘리지 않는다. `delta.type`을 확인하지 않으면 도구 입력
델타가 본문 텍스트로 새어 화면에 JSON 조각이 찍힌다.

**도구 호출 전후의 텍스트는 구분자 없이 이어진다.** 에이전트는 도구를 부르기
전에 한 마디("확인해 보겠습니다"), 결과를 받고 또 한 마디를 하는데, 두 텍스트가
서로 다른 content block이라 그대로 흘리면 `…확인해 보겠습니다주문 A-7은…`처럼
붙는다(실측). 도구 라운드가 끝난 뒤 첫 텍스트에 단락 구분을 한 번 넣어 준다 —
아래 `pendingBreak`가 그것이다.

```ts
// app/api/chat/route.ts
import { Agent, BedrockModel } from '@strands-agents/sdk';

export const runtime = 'nodejs';   // edge 아님 — AWS SDK가 Node API를 쓴다

export async function POST(req: Request) {
  const { message } = await req.json();
  const agent = new Agent({
    model: new BedrockModel({
      region: process.env.AWS_REGION,
      modelId: process.env.BEDROCK_MODEL_ID,
      maxTokens: 4096,             // temperature/topP/topK 금지
    }),
    tools: [/* … */],
  });

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const send = (type: string, payload: Record<string, unknown> = {}) =>
        controller.enqueue(encoder.encode(
          `data: ${JSON.stringify({ type, ...payload })}\n\n`));
      let sawText = false;
      let pendingBreak = false;
      try {
        for await (const ev of agent.stream(message)) {
          if (ev.type === 'modelStreamUpdateEvent') {
            const e = ev.event as any;
            if (e?.type === 'modelContentBlockDeltaEvent'
                && e.delta?.type === 'textDelta') {
              const prefix = pendingBreak && sawText ? '\n\n' : '';
              pendingBreak = false;
              sawText = true;
              send('text', { content: prefix + e.delta.text });
            }
          } else if (ev.type === 'beforeToolCallEvent') {
            send('tool_start', { name: ev.toolUse.name });
          } else if (ev.type === 'afterToolCallEvent') {
            send('tool_end', { name: ev.toolUse.name, status: ev.result?.status });
            pendingBreak = true;   // 다음 텍스트는 새 단락으로 시작한다
          } else if (ev.type === 'agentResultEvent') {
            send('done');
          }
        }
      } catch (err) {
        send('error', { message: err instanceof Error ? err.message : String(err) });
      } finally {
        controller.close();
      }
    },
  });
  return new Response(stream, {
    headers: { 'Content-Type': 'text/event-stream',
               'Cache-Control': 'no-cache, no-transform',
               Connection: 'keep-alive' },
  });
}
```

`no-transform`을 빼면 프리뷰 프록시 경로에서 응답이 버퍼링돼 스트리밍이 죽는다.

## 패턴 1: useAIStreaming 훅 (SSE)

wire event_type SSOT: `text` / `tool_start` / `tool_end` / `error` / `done`
(필드 `content` / `name` / `message`). 훅 자체는 DS-무관이다.

```ts
// src/hooks/useAIStreaming.ts
'use client';
import { useCallback, useRef, useState } from 'react';

export type ChatMessage = { role: 'user' | 'assistant'; content: string };

export function useAIStreaming(endpoint: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [content, setContent] = useState('');       // 스트리밍 중인 본문
  const [isStreaming, setStreaming] = useState(false);
  const [activeTool, setActiveTool] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const stop = useCallback(() => abort.current?.abort(), []);

  const send = useCallback(async (text: string) => {
    setMessages((m) => [...m, { role: 'user', content: text }]);
    setContent(''); setError(null); setStreaming(true);
    const ac = new AbortController(); abort.current = ac;
    let acc = '';
    try {
      // 상대 경로 — basePath 하위에서 서빙되므로 절대 경로를 쓰면 404가 된다.
      const res = await fetch(endpoint, {
        method: 'POST', signal: ac.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // SSE 프레임 경계는 빈 줄이다. 마지막 조각은 다음 청크와 이어지므로 남긴다.
        const frames = buf.split('\n\n'); buf = frames.pop() ?? '';
        for (const frame of frames) {
          const line = frame.split('\n').find((l) => l.startsWith('data:'));
          if (!line) continue;
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'text') { acc += ev.content; setContent(acc); }
          else if (ev.type === 'tool_start') setActiveTool(ev.name);
          else if (ev.type === 'tool_end') setActiveTool(null);
          else if (ev.type === 'error') setError(ev.message);
        }
      }
      if (acc) setMessages((m) => [...m, { role: 'assistant', content: acc }]);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setStreaming(false); setActiveTool(null); setContent('');
      abort.current = null;
    }
  }, [endpoint]);

  return { messages, content, isStreaming, activeTool, error, send, stop };
}
```

## 패턴 2: MarkdownContent (antd 버전)

코드블록·링크·표만 antd 토큰으로 매핑한다. 코드 예제가 오가는 채팅이면 문법 강조는 필수다(`<pre><code className="language-ts">`로 끝내면 강조되지 않는다).

```tsx
// components/chat/code-block.tsx
'use client';
import { useState } from 'react';
import { Button, Tooltip, theme } from 'antd';
import { CheckOutlined, CopyOutlined } from '@ant-design/icons';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';

export function CodeBlock({ code, language }: { code: string; language?: string }) {
  const { token } = theme.useToken();
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 클립보드 접근 불가(비 HTTPS 등) — 복사 실패는 치명적이지 않다.
    }
  }

  return (
    <div style={{ position: 'relative', margin: `${token.marginXS}px 0` }}>
      <Tooltip title={copied ? '복사됨' : '복사'}>
        <Button size="small" type="text" aria-label="코드 복사" onClick={copy}
                icon={copied ? <CheckOutlined /> : <CopyOutlined />}
                style={{ position: 'absolute', top: 8, right: 8, zIndex: 1 }} />
      </Tooltip>
      <SyntaxHighlighter language={language ?? 'text'} style={oneLight} PreTag="div"
        customStyle={{ margin: 0, borderRadius: token.borderRadius, fontSize: token.fontSizeSM,
                       fontFamily: token.fontFamilyCode, paddingRight: 48 }}>
        {code}
      </SyntaxHighlighter>
    </div>
  );
}
```

```tsx
// components/chat/markdown-content.tsx
'use client';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Typography, theme } from 'antd';
import { CodeBlock } from './code-block';

export function MarkdownContent({ content }: { content: string }) {
  const { token } = theme.useToken();
  return (
    <Typography>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children }) {
            const match = /language-(\w+)/.exec(className ?? '');
            const text = String(children).replace(/\n$/, '');
            if (match || text.includes('\n')) return <CodeBlock code={text} language={match?.[1]} />;
            return <Typography.Text code>{text}</Typography.Text>;
          },
          a({ href, children }) {
            return <Typography.Link href={href ?? '#'} target="_blank" rel="noopener noreferrer">{children}</Typography.Link>;
          },
          table({ children }) {
            return (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', width: '100%' }}>{children}</table>
              </div>
            );
          },
          th({ children }) {
            return <th style={{ border: `1px solid ${token.colorBorderSecondary}`, padding: '4px 8px',
                                background: token.colorFillAlter, textAlign: 'left' }}>{children}</th>;
          },
          td({ children }) {
            return <td style={{ border: `1px solid ${token.colorBorderSecondary}`, padding: '4px 8px' }}>{children}</td>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </Typography>
  );
}
```

`<Typography>`로 감싸면 제목·목록·인용·`code`에 antd 타이포그래피가 적용된다.

## 패턴 3: ChatPanel — Bubble.List + Sender

`@ant-design/x`(2.x, antd 6 전용)의 **프레젠테이션 컴포넌트만** 쓴다. 데이터는 `useAIStreaming`이 SSOT다 — `@ant-design/x-sdk`의 `useXChat`/`XRequest`/provider 같은 데이터 계층은 쓰지 않는다(쓰면 SSE 계약과 이중 전송이 생겨 서버 라우트와 충돌한다).

```tsx
// components/chat/chat-panel.tsx
'use client';
import { useState } from 'react';
import { Alert, Card, Flex, Tag } from 'antd';
import { Bubble, Sender } from '@ant-design/x';
import type { BubbleListProps } from '@ant-design/x';
import { LoadingOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons';
import { MarkdownContent } from './markdown-content';
import { useAIStreaming } from '@/hooks/useAIStreaming';

export function ChatPanel() {
  // fetch는 basePath를 붙여 주지 않는다 — 직접 붙인다.
  const endpoint = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ''}/api/chat/`;
  const { messages, content, isStreaming, activeTool, error, send, stop } = useAIStreaming(endpoint);
  const [input, setInput] = useState('');

  const role: BubbleListProps['role'] = {
    ai: { placement: 'start', avatar: <RobotOutlined />,
          contentRender: (c) => <MarkdownContent content={String(c)} /> },
    user: { placement: 'end', avatar: <UserOutlined />, variant: 'outlined' },
  };

  const items: BubbleListProps['items'] = [
    ...messages.map((m, i) => ({
      key: i, role: m.role === 'assistant' ? 'ai' : 'user', content: m.content, status: 'success' as const,
    })),
    ...(isStreaming ? [{
      key: 'streaming', role: 'ai', content, streaming: true,
      loading: !content, status: 'updating' as const,
    }] : []),
  ];

  return (
    <Card title="AI 어시스턴트" styles={{ body: { display: 'flex', flexDirection: 'column', gap: 12, height: 640 } }}>
      <div style={{ flex: 1, minHeight: 0 }}>
        <Bubble.List items={items} role={role} autoScroll style={{ height: '100%' }} />
      </div>
      {activeTool && (
        <Flex><Tag icon={<LoadingOutlined />} color="processing">{activeTool} 실행 중</Tag></Flex>
      )}
      {error && <Alert type="error" showIcon title={error} />}
      <Sender
        value={input}
        onChange={(v) => setInput(v)}
        onSubmit={(text) => { if (text.trim()) { send(text); setInput(''); } }}
        loading={isStreaming}
        onCancel={stop}
        placeholder="질문을 입력하세요"
      />
    </Card>
  );
}
```

- assistant 본문은 반드시 `contentRender` → `MarkdownContent`. 원문 문자열을 그대로 보이게 두지 않는다.
- 도구 호출 표시는 `tool_start`/`tool_end`로 바뀌는 `activeTool`을 `Tag`로. 도구 이력이 중요하면 메시지 사이에 `Bubble`의 `header` 슬롯으로 남긴다.
- 상세 API: `https://x.ant.design/components/bubble`, `https://x.ant.design/components/sender`.
