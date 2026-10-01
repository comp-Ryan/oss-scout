import { useEffect, useState } from 'react'

export function useIsWide(px: number) {
  const q = () => (typeof window !== 'undefined' && 'matchMedia' in window ? window.matchMedia(`(min-width: ${px}px)`).matches : true)
  const [wide, setWide] = useState(q)
  useEffect(() => {
    if (!('matchMedia' in window)) return
    const m = window.matchMedia(`(min-width: ${px}px)`)
    const on = () => setWide(m.matches)
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [px])
  return wide
}
