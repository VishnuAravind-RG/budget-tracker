/* Inline 24px stroke icons — no icon library, no extra request. */

const base = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
}

export const HomeIcon = (p) => (
  <svg {...base} {...p}><path d="M3 10.5 12 3l9 7.5" /><path d="M5.5 9.5V21h13V9.5" /></svg>
)

export const ReviewIcon = (p) => (
  <svg {...base} {...p}><circle cx="12" cy="12" r="9" /><path d="M12 8v5" /><path d="M12 16.2v.01" /></svg>
)

export const PlusIcon = (p) => (
  <svg {...base} {...p}><path d="M12 5v14" /><path d="M5 12h14" /></svg>
)

export const ListIcon = (p) => (
  <svg {...base} {...p}><path d="M8 6h13" /><path d="M8 12h13" /><path d="M8 18h13" /><path d="M3.5 6h.01" /><path d="M3.5 12h.01" /><path d="M3.5 18h.01" /></svg>
)

export const TargetIcon = (p) => (
  <svg {...base} {...p}><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4" /><circle cx="12" cy="12" r="0.6" fill="currentColor" /></svg>
)

export const TrashIcon = (p) => (
  <svg {...base} width="18" height="18" {...p}><path d="M4 7h16" /><path d="M9.5 7V4.8h5V7" /><path d="M6.5 7l.8 12.2h9.4L17.5 7" /></svg>
)

export const AlertIcon = (p) => (
  <svg {...base} width="14" height="14" strokeWidth="2" {...p} aria-hidden="true"><path d="M12 3.5 22 20H2Z" /><path d="M12 10v4" /><path d="M12 17v.01" /></svg>
)

export const CheckIcon = (p) => (
  <svg {...base} width="14" height="14" strokeWidth="2.4" {...p} aria-hidden="true"><path d="M4 12.5 9.5 18 20 6.5" /></svg>
)
