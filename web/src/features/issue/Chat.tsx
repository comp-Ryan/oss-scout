import { useEffect, useState } from 'react'
import type { Message } from '@/lib/types'
import { useSendMessage } from '@/lib/queries'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/input'
import { cn } from '@/lib/utils'

export function Chat({ id, messages }: { id: string; messages: Message[] }) {
  const [text, setText] = useState('')
  const [local, setLocal] = useState<Message[]>([])  // replies shown at once; the refetch then owns them
  const [err, setErr] = useState<string | null>(null)
  const send = useSendMessage()
  useEffect(() => { setLocal([]) }, [messages.length])
  const shown = [...messages, ...local.filter(m => !messages.some(x => x.id === m.id))]
  const submit = () => {
    const content = text.trim()
    if (!content || send.isPending) return
    setErr(null)
    send.mutate([id, content], { onSuccess: r => { setLocal(l => [...l, r.user, r.assistant]); setText('') }, onError: e => setErr(String(e)) })
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="flex max-h-[55vh] flex-col gap-2 overflow-auto">
        {shown.length === 0 && <p className="max-w-[48ch] text-[13px] text-muted">No messages yet. The first one starts a conversation that already knows the issue and the brief, and can read the repo.</p>}
        {shown.map(m => <div key={m.id} className={cn('max-w-[72ch] whitespace-pre-wrap rounded-md px-3 py-2 text-[13.5px]', m.role === 'user' ? 'self-end bg-[#263047]' : 'border border-line bg-surface')}>{m.content}</div>)}
        {send.isPending && <p className="text-[12.5px] text-muted">Thinking.</p>}
      </div>
      {err && <p className="text-[12.5px] text-bad">{err}</p>}
      <Textarea rows={3} placeholder="Ask about the issue or the fix. Ctrl+Enter sends." value={text} onChange={e => setText(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) submit() }} />
      <div><Button variant="primary" onClick={submit} disabled={send.isPending || !text.trim()}>Send</Button></div>
    </div>
  )
}
