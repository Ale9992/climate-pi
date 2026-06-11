function fmtTs(ts) {
  if (!ts) return ''
  // ts ISO locale dal backend; mostra data+ora compatte.
  return ts.replace('T', ' ').slice(0, 19)
}

export default function EventLog({ logs, onRefresh }) {
  return (
    <div className="event-log">
      <div className="log-head">
        <h2>Log automazioni</h2>
        <button className="btn ghost" onClick={onRefresh}>↻ Aggiorna</button>
      </div>
      {(!logs || logs.length === 0) && <p className="muted">Nessun evento registrato.</p>}
      <ul className="log-list">
        {logs && logs.map((l) => (
          <li key={l.id} className={l.action_taken?.startsWith('ERRORE') ? 'log-err' : ''}>
            <div className="log-line1">
              <span className="log-room">{l.room_name}</span>
              <span className="log-rule">{l.rule_matched}</span>
              <span className="log-ts">{fmtTs(l.timestamp)}</span>
            </div>
            <div className="log-action">{l.action_taken}</div>
            {(l.temp_at_trigger != null || l.humidity_at_trigger != null) && (
              <div className="log-vals">
                T={l.temp_at_trigger ?? '—'}°C · RH={l.humidity_at_trigger ?? '—'}%
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
