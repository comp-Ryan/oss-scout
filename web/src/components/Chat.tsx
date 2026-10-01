import { useState } from 'react'
import type { Message } from '../types'

export function Chat({ initial, onSend }: { initial: Message[]; onSend: (content: string) => Promise<Message[]> }) {
  const [msgs, setMsgs] = useState<Message[]>(initial)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const send = async () => {
    const content = text.trim()
    if (!content || busy) return
    setBusy(true); setErr(null)
    try {
      const added = await onSend(content)
      setMsgs(m => [...m, ...added]); setText('')
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="chat">
      <div className="msgs">
        {msgs.length === 0 && <p className="muted">No messages yet. The first message starts a conversation that already knows the issue and the brief.</p>}
        {msgs.map(m => <div key={m.id} className={`msg ${m.role}`}>{m.content}</div>)}
      </div>
      {err && <p className="error">{err}</p>}
      <textarea rows={3} placeholder="Ask about the issue or the fix…" value={text} onChange={e => setText(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send() }} />
      <div><button onClick={send} disabled={busy || !text.trim()}>{busy ? 'Thinking…' : 'Send'}</button></div>
    </div>
  )
}
