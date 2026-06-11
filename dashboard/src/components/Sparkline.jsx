import { useMemo } from 'react'

// Mini-grafico SVG dell'andamento temperatura (+ umidità sullo sfondo).
// Niente librerie: path SVG calcolato a mano, leggero anche sul Pi.
export default function Sparkline({ points, accent = '#7d97b8' }) {
  const W = 320, H = 92, PAD = 8

  const geo = useMemo(() => {
    const pts = (points || []).filter((p) => p.temperature != null)
    if (pts.length < 2) return null

    const temps = pts.map((p) => p.temperature)
    const hums = pts.map((p) => (p.humidity != null ? p.humidity : null))
    let lo = Math.min(...temps), hi = Math.max(...temps)
    if (hi - lo < 2) { const m = (hi + lo) / 2; lo = m - 1; hi = m + 1 } // banda minima
    const pad = (hi - lo) * 0.15
    lo -= pad; hi += pad

    const x = (i) => PAD + (i / (pts.length - 1)) * (W - 2 * PAD)
    const y = (t) => PAD + (1 - (t - lo) / (hi - lo)) * (H - 2 * PAD)

    const line = temps.map((t, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(t).toFixed(1)}`).join(' ')
    const area = `${line} L${x(pts.length - 1).toFixed(1)},${H - PAD} L${x(0).toFixed(1)},${H - PAD} Z`

    // umidità: linea tratteggiata tenue su scala propria (0–100)
    const yh = (h) => PAD + (1 - h / 100) * (H - 2 * PAD)
    const humLine = hums.every((h) => h == null) ? null
      : hums.map((h, i) => h == null ? '' : `${i ? 'L' : 'M'}${x(i).toFixed(1)},${yh(h).toFixed(1)}`).join(' ').replace(/^L/, 'M')

    const last = pts[pts.length - 1]
    return { line, area, humLine, lo, hi, lastX: x(pts.length - 1), lastY: y(last.temperature), min: Math.min(...temps), max: Math.max(...temps) }
  }, [points])

  if (!geo) return <div className="spark-empty">Storico non ancora disponibile</div>

  const gid = `g-${accent.replace('#', '')}`
  return (
    <div className="spark">
      <svg viewBox={`0 0 ${W} ${H}`} className="spark-svg" preserveAspectRatio="none">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={accent} stopOpacity="0.22" />
            <stop offset="100%" stopColor={accent} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={geo.area} fill={`url(#${gid})`} />
        {geo.humLine && <path d={geo.humLine} fill="none" stroke="#9fb4d0" strokeWidth="1" strokeDasharray="3 3" opacity="0.55" />}
        <path d={geo.line} fill="none" stroke={accent} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={geo.lastX} cy={geo.lastY} r="3.2" fill={accent} />
      </svg>
      <div className="spark-meta">
        <span>min <b>{geo.min.toFixed(1)}°</b></span>
        <span className="spark-mid">ultime 24h</span>
        <span>max <b>{geo.max.toFixed(1)}°</b></span>
      </div>
    </div>
  )
}
