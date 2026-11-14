import { useState } from 'react';
import type { ProfileDescriptor, RunOverrides } from '../transport/contracts';
import { Icon } from '../components/Icon';

export function LocalConfiguration({ profiles, profileId, overrides, apply, close }: { profiles: ProfileDescriptor[]; profileId: string; overrides: RunOverrides; apply: (id: string, value: RunOverrides) => void; close: () => void }) {
  const [id, setId] = useState(profileId);
  const [draft, setDraft] = useState(overrides);
  const profile = profiles.find(item => item.id === id);
  const valid = (draft.verification_timeout_seconds ?? 60) <= (draft.login_timeout_seconds ?? 120);
  return <section className="configuration" aria-labelledby="configuration-heading" onKeyDown={event => { if (event.key === 'Escape') close(); }}>
    <div className="section-heading"><div><h2 id="configuration-heading">Run configuration</h2><p>Prepare the next execution. A profile starts only when you choose Start run.</p></div><button className="icon-button" aria-label="Close run configuration" onClick={close}><Icon name="close" /></button></div>
    <form onSubmit={event => { event.preventDefault(); if (valid) apply(id, draft); }}><div className="form-grid">
      <label>Site profile<select aria-label="Site profile" autoFocus value={id} onChange={event => setId(event.target.value)}>{profiles.map(item => <option key={item.id} value={item.id} disabled={item.readiness === 'unavailable'}>{item.display_name} · {item.classification}</option>)}</select></label>
      <label>Browser<select aria-label="Browser" value={String(draft.headless ?? false)} onChange={event => setDraft({ ...draft, headless: event.target.value === 'true' })}><option value="false">Chromium · headed</option><option value="true">Chromium · headless</option></select></label>
      <label>Slow motion (ms)<input type="number" min="0" max="2000" required value={draft.slow_mo_ms ?? 0} onChange={event => setDraft({ ...draft, slow_mo_ms: Number(event.target.value) })} /></label>
      <label>Login timeout (seconds)<input type="number" min="1" max="600" required value={draft.login_timeout_seconds ?? 120} onChange={event => setDraft({ ...draft, login_timeout_seconds: Number(event.target.value) })} /></label>
      <label>Verification timeout (seconds)<input type="number" min="1" max={draft.login_timeout_seconds ?? 120} required value={draft.verification_timeout_seconds ?? 60} onChange={event => setDraft({ ...draft, verification_timeout_seconds: Number(event.target.value) })} /></label>
    </div><label className="follow-toggle ai-setting"><input type="checkbox" checked={draft.ai_enabled ?? false} onChange={event => setDraft({ ...draft, ai_enabled: event.target.checked })} />Enable configured AI assistance</label>
      <p className="section-note">AI credentials and the selected model stay on the server. External service readiness is unverified.</p>
      {profile?.classification === 'live' && <p className="live-notice">Live profile selected. Starting can contact the approved website{profile.required_capabilities.includes('gmail') ? ' and Gmail mailbox' : ''}. Failed email requests are never automatically repeated.</p>}
      {!valid && <p role="alert">Verification timeout must be within the login timeout.</p>}
      <div className="form-actions"><span>Changes apply to the next run.</span><button type="button" className="button" onClick={close}>Cancel</button><button className="button primary" disabled={!valid || !profile} type="submit">Apply configuration</button></div>
    </form>
  </section>;
}
