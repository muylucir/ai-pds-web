// frontend/components/canvas/UserMessage.tsx
export function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] bg-violet-600 text-white rounded-2xl rounded-br-md px-4 py-2.5 text-sm whitespace-pre-wrap">
        {text}
      </div>
    </div>
  );
}
