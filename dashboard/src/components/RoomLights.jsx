import { useRef } from 'react'
import Icon from '@mdi/react'
import { mdiLightbulb, mdiLightbulbOutline, mdiCeilingLight, mdiLightbulbGroup } from '@mdi/js'
import { api } from '../api.js'

// Controlli luce in stile iOS: tile con toggle a pillola + dimmer.
export default function RoomLights({ room, lights, onChange }) {
  const timer = useRef({})
  const list = lights || []
  const onCount = list.filter((l) => l.is_on).length

  const toggleOne = (lt) =>
    api.setLight(lt.id, { on: !lt.is_on }).then(onChange).catch(onChange)
  const dim = (lt, level) => {
    clearTimeout(timer.current[lt.id])
    timer.current[lt.id] = setTimeout(
      () => api.setLight(lt.id, { level }).then(onChange).catch(onChange), 400)
  }

  if (list.length === 0) {
    return (
      <div className="lights-card empty">
        <div className="lc-head"><Icon path={mdiLightbulbGroup} size={0.8} /><span>Luci</span></div>
        <p className="muted small">Nessuna luce in questa stanza.</p>
      </div>
    )
  }

  const iconOf = (lt) => lt.is_ceiling
    ? mdiCeilingLight : (lt.is_on ? mdiLightbulb : mdiLightbulbOutline)

  // UNA sola luce (plafoniera): controlli direttamente nella card, niente tile annidata.
  if (list.length === 1) {
    const lt = list[0]
    return (
      <div className={`lights-card solo compact ${lt.is_on ? 'on' : ''}`}>
        <div className="lc-solo-top">
          <span className="lt-ico"><Icon path={iconOf(lt)} size={1.1} /></span>
          <div className="lc-solo-info">
            <div className="lt-name">{lt.name}</div>
            <div className="muted small">{lt.is_on ? `accesa · ${lt.level ?? 100}%` : 'spenta'}</div>
          </div>
          <button className={`ios-switch ${lt.is_on ? 'on' : ''}`}
            onClick={() => toggleOne(lt)} role="switch" aria-checked={lt.is_on}>
            <span className="ios-knob" />
          </button>
        </div>
        {lt.supports_level && (
          <input type="range" className="lc-solo-dim" min="1" max="100" defaultValue={lt.level ?? 100}
            disabled={!lt.is_on} onChange={(e) => dim(lt, Number(e.target.value))} />
        )}
      </div>
    )
  }

  // PIÙ luci: card con griglia di tile.
  return (
    <div className="lights-card">
      <div className="lc-head">
        <Icon path={mdiLightbulbGroup} size={0.8} />
        <span>Luci</span>
        <em>{onCount} accese</em>
      </div>
      <div className="lc-tiles">
        {list.map((lt) => (
          <div key={lt.id} className={`light-tile ${lt.is_on ? 'on' : ''}`}>
            <div className="lt-top">
              <span className="lt-ico"><Icon path={iconOf(lt)} size={1} /></span>
              <button className={`ios-switch ${lt.is_on ? 'on' : ''}`}
                onClick={() => toggleOne(lt)} role="switch" aria-checked={lt.is_on}>
                <span className="ios-knob" />
              </button>
            </div>
            <div className="lt-name">{lt.name}</div>
            {lt.supports_level && (
              <div className="lt-dim">
                <input type="range" min="1" max="100" defaultValue={lt.level ?? 100}
                  disabled={!lt.is_on} onChange={(e) => dim(lt, Number(e.target.value))} />
                <span className="lt-pct">{lt.is_on ? `${lt.level ?? 100}%` : 'off'}</span>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
