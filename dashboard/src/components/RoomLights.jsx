import { useRef, useState } from 'react'
import Icon from '@mdi/react'
import { mdiLightbulb, mdiLightbulbOutline, mdiCeilingLight, mdiLightbulbGroup } from '@mdi/js'
import { api } from '../api.js'

// Raggruppa le luci fisiche in "fixtures": una config `groups` puo' unire piu'
// luci (es. le 2 lampadine della specchiera) in un unico controllo. Le luci non
// elencate restano singole. `groups` = [{ name, members:[nome|id], icon?, ceiling? }].
function buildFixtures(lights, groups) {
  const used = new Set()
  const fixtures = []
  ;(groups || []).forEach((g) => {
    const members = (lights || []).filter((l) => g.members.includes(l.name) || g.members.includes(l.id))
    if (members.length) {
      members.forEach((m) => used.add(m.id))
      fixtures.push({ key: 'g:' + g.name, name: g.name, lights: members, icon: g.icon, ceiling: g.ceiling })
    }
  })
  ;(lights || []).forEach((l) => { if (!used.has(l.id)) fixtures.push({ key: l.id, name: l.name, lights: [l], ceiling: l.is_ceiling }) })
  return fixtures
}

// Controlli luce in stile iOS: tile con toggle a pillola + dimmer.
export default function RoomLights({ room, lights, onChange, groups }) {
  const timer = useRef({})
  // Override OTTIMISTICO: il toggle si muove subito al click; il comando al
  // Dirigera (~1s) parte in background e lo stato reale arriva col refresh.
  const [override, setOverride] = useState({})   // {lightId: is_on}
  const list = (lights || []).map((l) => (l.id in override ? { ...l, is_on: override[l.id] } : l))
  const fixtures = buildFixtures(list, groups)
  const onCount = fixtures.filter((f) => f.lights.some((l) => l.is_on)).length

  const toggleFixture = (fx) => {
    const next = !fx.lights.some((l) => l.is_on)   // se una e' accesa -> spegni tutte
    setOverride((o) => { const c = { ...o }; fx.lights.forEach((l) => { c[l.id] = next }); return c })
    const ids = fx.lights.map((l) => l.id)
    const finish = async () => {
      try { await onChange?.() } catch { /* ignore */ }
      setOverride((o) => { const c = { ...o }; ids.forEach((id) => delete c[id]); return c })
    }
    Promise.all(fx.lights.map((l) => api.setLight(l.id, { on: next }))).then(finish, finish)
  }
  const dimFixture = (fx, level) => {
    clearTimeout(timer.current[fx.key])
    timer.current[fx.key] = setTimeout(
      () => Promise.all(fx.lights.map((l) => api.setLight(l.id, { level }))).then(onChange).catch(onChange), 400)
  }

  if (fixtures.length === 0) {
    return (
      <div className="lights-card empty">
        <div className="lc-head"><Icon path={mdiLightbulbGroup} size={0.8} /><span>Luci</span></div>
        <p className="muted small">Nessuna luce in questa stanza.</p>
      </div>
    )
  }

  const iconOf = (fx) => fx.icon ? fx.icon
    : fx.ceiling ? mdiCeilingLight : (fx.lights.some((l) => l.is_on) ? mdiLightbulb : mdiLightbulbOutline)
  const fxState = (fx) => {
    const on = fx.lights.some((l) => l.is_on)
    const lvl = fx.lights.find((l) => l.is_on)?.level ?? fx.lights[0]?.level ?? 100
    const supportsLevel = fx.lights.some((l) => l.supports_level)
    return { on, lvl, supportsLevel }
  }

  // UNA sola fixture: controlli direttamente nella card, niente tile annidata.
  if (fixtures.length === 1) {
    const fx = fixtures[0]
    const { on, lvl, supportsLevel } = fxState(fx)
    return (
      <div className={`lights-card solo compact ${on ? 'on' : ''}`}>
        <div className="lc-solo-top">
          <span className="lt-ico"><Icon path={iconOf(fx)} size={1.1} /></span>
          <div className="lc-solo-info">
            <div className="lt-name">{fx.name}</div>
            <div className="muted small">{on ? `accesa · ${lvl}%` : 'spenta'}</div>
          </div>
          <button className={`ios-switch ${on ? 'on' : ''}`}
            onClick={() => toggleFixture(fx)} role="switch" aria-checked={on}>
            <span className="ios-knob" />
          </button>
        </div>
        {supportsLevel && (
          <input type="range" className="lc-solo-dim" min="1" max="100" defaultValue={lvl}
            disabled={!on} onChange={(e) => dimFixture(fx, Number(e.target.value))} />
        )}
      </div>
    )
  }

  // PIÙ fixtures: card con griglia di tile.
  return (
    <div className="lights-card">
      <div className="lc-head">
        <Icon path={mdiLightbulbGroup} size={0.8} />
        <span>Luci</span>
        <em>{onCount} accese</em>
      </div>
      <div className="lc-tiles">
        {fixtures.map((fx) => {
          const { on, lvl, supportsLevel } = fxState(fx)
          return (
            <div key={fx.key} className={`light-tile ${on ? 'on' : ''}`}>
              <div className="lt-top">
                <span className="lt-ico"><Icon path={iconOf(fx)} size={1} /></span>
                <button className={`ios-switch ${on ? 'on' : ''}`}
                  onClick={() => toggleFixture(fx)} role="switch" aria-checked={on}>
                  <span className="ios-knob" />
                </button>
              </div>
              <div className="lt-name">{fx.name}</div>
              {supportsLevel && (
                <div className="lt-dim">
                  <input type="range" min="1" max="100" defaultValue={lvl}
                    disabled={!on} onChange={(e) => dimFixture(fx, Number(e.target.value))} />
                  <span className="lt-pct">{on ? `${lvl}%` : 'off'}</span>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
