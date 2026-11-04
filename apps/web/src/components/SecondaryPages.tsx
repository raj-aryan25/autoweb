import { useState } from 'react';
import { formatClock, getProfile, profiles } from '../demo/model';
import type { DemoRun, PageName, RunConfig } from '../demo/model';
import { Icon } from './Icon';
import { StatusBadge } from './Inspector';
import { Configuration } from './Configuration';

export function SecondaryPages({ page, history, config, onInspect, onChooseProfile, onApply, onWorkspace, playing }: {
  page: PageName; history: DemoRun[]; config: RunConfig; onInspect: (run: DemoRun) => void;
  onChooseProfile: (id: string) => void; onApply: (config: RunConfig) => void; onWorkspace: () => void; playing: boolean;
}) {
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  if (page === 'history') {
    const filtered = history.filter(run => (filter === 'all' || run.status === filter) && `${getProfile(run.profileId).name} ${run.id}`.toLowerCase().includes(query.toLowerCase()));
    return <section className="page-content"><div className="page-heading"><div><h1>Run history</h1><p>Revisit outcomes and inspect the steps behind them.</p></div><span className="subtle-badge">Demo session only</span></div>
      <div className="filters"><label className="search-field"><Icon name="search" /><span className="sr-only">Search runs</span><input placeholder="Search by profile or run ID" value={query} onChange={event => setQuery(event.target.value)} /></label><label className="filter-label">Outcome<select value={filter} onChange={event => setFilter(event.target.value)}><option value="all">All outcomes</option><option value="succeeded">Completed</option><option value="failed">Failed</option><option value="cancelled">Stopped</option></select></label></div>
      {!filtered.length ? <div className="empty-state page-empty"><Icon name="history" /><h2>{history.length ? 'No matching runs' : 'Your next run starts here'}</h2><p>{history.length ? 'Try another profile name or clear the outcome filter.' : 'Complete or stop a demo to keep its steps and events here. History stays in this browser tab until refresh.'}</p><button className="button primary" onClick={history.length ? () => { setFilter('all'); setQuery(''); } : onWorkspace}>{history.length ? 'Clear filters' : 'Open run workspace'}</button></div>
        : <div className="table-scroll"><table className="history-table"><caption className="sr-only">Runs completed during this demo session</caption><thead><tr><th>Run</th><th>Site profile</th><th>Outcome</th><th>Elapsed</th><th><span className="sr-only">Action</span></th></tr></thead><tbody>{filtered.map(run => <tr key={run.id}><td className="mono">{run.id}</td><td><strong>{getProfile(run.profileId).name}</strong><small>{getProfile(run.profileId).method}</small></td><td><StatusBadge status={run.status} /></td><td className="mono">{formatClock(run.elapsed)}</td><td><button className="text-button" onClick={() => onInspect(run)} disabled={playing} aria-label={`Inspect ${run.id}`}>Inspect<Icon name="arrow" /></button></td></tr>)}</tbody></table></div>}
    </section>;
  }
  if (page === 'profiles') return <section className="page-content"><div className="page-heading"><div><h1>Site profiles</h1><p>Reusable login workflows, with every action made visible.</p></div><span className="subtle-badge">{profiles.length} demo profiles</span></div>
    <div className="profile-list">{profiles.map(profile => <article className="profile-item" key={profile.id}><span className="profile-symbol"><Icon name={profile.id === 'local-password' ? 'monitor' : 'mail'} /></span><div><h2>{profile.name}</h2><p>{profile.method} <span className="separator">/</span> {profile.steps.length} steps</p><small>{profile.source}</small></div><button className="button" disabled={playing} onClick={() => onChooseProfile(profile.id)}>Use profile<Icon name="arrow" /></button></article>)}</div><p className="section-note">{playing ? 'Stop the active demo before switching profiles.' : 'Selecting a profile prepares a demo. No website is opened and no email is requested.'}</p>
  </section>;
  if (page === 'connections') return <section className="page-content"><div className="page-heading"><div><h1>Connections</h1><p>Know which services a run depends on.</p></div><span className="subtle-badge">Integration pending</span></div>
    <div className="connection-list">{[
      { name: 'Python runner', icon: 'monitor' as const, description: 'Executes the existing AutoWeb package and owns the browser session.', note: 'This UI currently receives simulated events. The local API is the next integration step.' },
      { name: 'Gmail', icon: 'mail' as const, description: 'Retrieves fresh verification emails for an approved login request.', note: 'Existing OAuth authorization is not read by this UI. Consent must always be initiated explicitly.' },
      { name: 'AI assistance', icon: 'connections' as const, description: 'Optional locator assistance and verification extraction.', note: 'No model requests are made. Deterministic actions remain the first choice.' },
    ].map(connection => <article className="connection-item" key={connection.name}><div className="profile-symbol"><Icon name={connection.icon} /></div><div><div className="connection-title"><h2>{connection.name}</h2><span className="subtle-badge">Not connected in this UI</span></div><p>{connection.description}</p><small>{connection.note}</small></div></article>)}</div>
    <div className="plain-note"><Icon name="info" /><p>Connection indicators will come from runner preflight checks after integration. This screen does not inspect credentials, start OAuth consent, or change your existing package setup.</p></div>
  </section>;
  return <section className="page-content"><div className="page-heading"><div><h1>Settings</h1><p>Prepare the next demo with your preferred execution settings.</p></div></div><p className="section-note">Settings apply in this tab. Refresh restores the original preview. Headed mode is the intended default; a real browser will be launched only after runner integration.</p>{playing ? <div className="plain-note"><Icon name="info" /><p>Stop the active demo before changing execution settings.</p><button className="button" onClick={onWorkspace}>Return to run</button></div> : <Configuration key={config.profileId + config.engine + config.timeout + config.outcome + config.slowMo} config={config} onApply={onApply} onClose={onWorkspace} />}</section>;
}
