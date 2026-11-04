import { useState } from 'react';
import { profiles } from '../demo/model';
import type { RunConfig } from '../demo/model';
import { Icon } from './Icon';

export function Configuration({ config, onApply, onClose }: { config: RunConfig; onApply: (next: RunConfig) => void; onClose: () => void }) {
  const [draft, setDraft] = useState(config);
  return <section className="configuration" aria-labelledby="configuration-heading" onKeyDown={event => { if (event.key === 'Escape') onClose(); }}>
    <div className="section-heading"><div><h2 id="configuration-heading">Run configuration</h2><p>Choose a simulated workflow and outcome. No live services will be contacted.</p></div><button className="icon-button" aria-label="Close run configuration" onClick={onClose}><Icon name="close" /></button></div>
    <form onSubmit={event => { event.preventDefault(); onApply(draft); }}>
      <div className="form-grid">
        <label>Site profile<select autoFocus value={draft.profileId} onChange={event => setDraft({ ...draft, profileId: event.target.value, outcome: event.target.value === 'local-password' ? 'success' : draft.outcome })}>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
        <label>Demo outcome<select value={draft.outcome} onChange={event => setDraft({ ...draft, outcome: event.target.value as RunConfig['outcome'] })}><option value="success">Successful login</option><option value="timeout" disabled={draft.profileId === 'local-password'}>Verification email timeout</option></select></label>
        <label>Browser engine<select value={draft.engine} onChange={event => setDraft({ ...draft, engine: event.target.value as RunConfig['engine'] })}>{['Chromium', 'Firefox', 'WebKit'].map(engine => <option key={engine}>{engine}</option>)}</select></label>
        <label>Slow motion (ms)<input type="number" min="0" max="2000" step="50" value={draft.slowMo} onChange={event => setDraft({ ...draft, slowMo: Number(event.target.value) })} required /></label>
        <label>Email timeout (seconds)<input type="number" min="5" max="120" value={draft.timeout} onChange={event => setDraft({ ...draft, timeout: Number(event.target.value) })} required /></label>
      </div>
      <div className="form-actions"><span>Applies to the next demo. Runs start only when you choose.</span><button type="button" className="button" onClick={onClose}>Cancel</button><button className="button primary" type="submit">Apply configuration</button></div>
    </form>
  </section>;
}
