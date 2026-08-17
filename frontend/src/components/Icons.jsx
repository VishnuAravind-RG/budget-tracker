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

export const FuelIcon = (p) => (
  <svg {...base} {...p}><path d="M4 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16" /><path d="M4 11h10" /><path d="M16 8.5 18.5 11H19a2 2 0 0 1 2 2v4.5a1.5 1.5 0 0 1-3 0V15" /><path d="M2.5 21h13" /></svg>
)

export const TodoIcon = (p) => (
  <svg {...base} {...p}><rect x="3.5" y="3.5" width="17" height="17" rx="3" /><path d="M7.5 12l2.5 2.5 6-6" /></svg>
)

export const CarIcon = (p) => (
  <svg {...base} {...p}><path d="M4 16V11l2-4.5h12L20 11v5" /><path d="M4 16h16" /><circle cx="7.5" cy="16" r="1.6" /><circle cx="16.5" cy="16" r="1.6" /><path d="M5.5 11h13" /></svg>
)

export const BikeIcon = (p) => (
  <svg {...base} {...p}><circle cx="6" cy="17" r="3" /><circle cx="18" cy="17" r="3" /><path d="M6 17 10 9h5l3 8" /><path d="M10 9 8.5 6h-2" /><path d="M13 9l2 4h3" /></svg>
)

export const ShopIcon = (p) => (
  <svg {...base} width="18" height="18" {...p}><path d="M4 9.5 5 4h14l1 5.5" /><path d="M4 9.5a2.3 2.3 0 0 0 4.5.6 2.3 2.3 0 0 0 4.5 0 2.3 2.3 0 0 0 4.5 0 2.3 2.3 0 0 0 4.5-.6" /><path d="M5.5 10.5V20h13v-9.5" /></svg>
)

export const PersonIcon = (p) => (
  <svg {...base} width="18" height="18" {...p}><circle cx="12" cy="8" r="3.5" /><path d="M5 20c0-3.5 3-6 7-6s7 2.5 7 6" /></svg>
)

export const WalletIcon = (p) => (
  <svg {...base} width="18" height="18" {...p}><rect x="3.5" y="6" width="17" height="13" rx="2.5" /><path d="M16.5 6V5a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v1" /><circle cx="16.5" cy="12.7" r="1.1" fill="currentColor" stroke="none" /></svg>
)

export const AccountIcon = (p) => (
  <svg {...base} width="18" height="18" {...p}><path d="M4 10 12 4l8 6" /><path d="M5.5 9.5V19h13V9.5" /><path d="M10 19v-5h4v5" /></svg>
)

export const BellIcon = (p) => (
  <svg {...base} width="14" height="14" {...p}><path d="M6 9.5a6 6 0 0 1 12 0c0 4 1.5 5.5 1.5 5.5h-15S6 13.5 6 9.5Z" /><path d="M10 18a2 2 0 0 0 4 0" /></svg>
)

export const PinIcon = (p) => (
  <svg {...base} width="14" height="14" {...p}><path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21Z" /><circle cx="12" cy="9.5" r="2.3" /></svg>
)
