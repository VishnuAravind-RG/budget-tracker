import { useEffect, useRef, useState } from 'react'

/** Measured container width, so SVG charts can render at true pixel size
 *  (a scaled viewBox would blow up the label text along with the geometry). */
export function useElementWidth(fallback = 320) {
  const ref = useRef(null)
  const [width, setWidth] = useState(fallback)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => setWidth(el.clientWidth || fallback)
    measure()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure)
      return () => window.removeEventListener('resize', measure)
    }
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [fallback])

  return [ref, width]
}
