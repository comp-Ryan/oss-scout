import { useEffect } from 'react'

/** Document-level single-key shortcuts that stay quiet while typing in a field. */
export function useKeys(map: Record<string, (e: KeyboardEvent) => void>, deps: unknown[] = []) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
      const combo = `${e.ctrlKey || e.metaKey ? 'mod+' : ''}${e.key.length === 1 ? e.key.toLowerCase() : e.key}`
      if (map[combo] && (!typing || combo.startsWith('mod+'))) {
        e.preventDefault()
        map[combo](e)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, deps)
}
