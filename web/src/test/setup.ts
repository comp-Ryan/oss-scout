import '@testing-library/jest-dom/vitest'

// jsdom lacks these; cmdk and Radix use them.
class RO { observe() {} unobserve() {} disconnect() {} }
;(globalThis as any).ResizeObserver = (globalThis as any).ResizeObserver || RO
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
if (!(window as any).matchMedia) (window as any).matchMedia = (q: string) => ({ matches: true, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })
