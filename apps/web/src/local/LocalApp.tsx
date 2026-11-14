import { useEffect, useRef, useState } from 'react';
import { Icon } from '../components/Icon';
import type { IconName } from '../components/Icon';
import type { PageName } from '../demo/model';
import { formatDuration } from '../demo/model';
import type { RunOverrides } from '../transport/contracts';
import { useLocalRunner } from '../runner/useLocalRunner';
import { LocalConfiguration } from './LocalConfiguration';
import { LocalInspector } from './LocalInspector';
import { LocalPages } from './LocalPages';
import { duration, exportJson, StateBadge, stepTitle, words } from './presentation';

const navigation: { id: PageName; label: string; icon: IconName }[] = [
  { id: 'workspace', label: 'Workspace', icon: 'workspace' }, { id: 'history', label: 'Run history', icon: 'history' }, { id: 'profiles', label: 'Site profiles', icon: 'profiles' }, { id: 'connections', label: 'Connections', icon: 'connections' }, { id: 'settings', label: 'Settings', icon: 'settings' },
];
function fromHash(): PageName { const value = window.location.hash.slice(1); return navigation.some(item => item.id === value) ? value as PageName : 'workspace'; }
export function LocalApp() {
  const runner = useLocalRunner();
  const { run, events } = runner;
  const [page, setPage] = useState(fromHash);
  const [showConfig, setShowConfig] = useState(false);
  const [selected, setSelected] = useState('');
  const [follow, setFollow] = useState(true);
  const configButton = useRef<HTMLButtonElement>(null);
  const inspector = useRef<HTMLDivElement>(null);
  const profile = runner.profiles.find(item => item.id === (run?.snapshot.profile_id ?? runner.profileId));
  const steps = run?.manifest?.steps ?? profile?.steps ?? [];
  const activeStep = run?.snapshot.steps.find(item => item.state === 'running' || item.state === 'waiting')?.step_id ?? [...(run?.snapshot.steps ?? [])].reverse().find(item => item.state !== 'pending' && item.state !== 'not_executed')?.step_id;
  const selectedStep = steps.find(item => item.id === (follow ? activeStep : selected)) ?? steps[0];
  const locked = Boolean(runner.busy || runner.health?.active_run_id || runner.pending);
  const unavailable = runner.connection !== 'connected' || !runner.health?.execution_available || runner.health.cleanup_required;
  const canStart = !runner.busy && !unavailable && (!!runner.pending || (!runner.health?.active_run_id && profile?.readiness !== 'unavailable' && !!profile));
  useEffect(() => {
    function change() { if (location.hash !== '#main-content') { setPage(fromHash()); setShowConfig(false); } }
    addEventListener('hashchange', change); return () => removeEventListener('hashchange', change);
  }, []);
  useEffect(() => { document.title = `${navigation.find(item => item.id === page)?.label} · AutoWeb Studio`; }, [page]);
  function workspace() { location.hash = 'workspace'; setPage('workspace'); }
  function closeConfig() { setShowConfig(false); configButton.current?.focus(); }
  function configure(id: string, overrides: RunOverrides) { runner.configure(id, overrides); setSelected(''); setFollow(true); setShowConfig(false); workspace(); configButton.current?.focus(); }
  const effective = run?.manifest?.effective;
  const ai = run?.snapshot.outcome?.ai_usage;
  return <div className="app-shell local-app"><a className="skip-link" href="#main-content">Skip to main content</a><aside className="sidebar" aria-label="Workspace navigation"><a className="brand" href="#workspace" aria-label="AutoWeb workspace"><span className="brand-mark"><Icon name="logo" /></span>AutoWeb</a><nav className="main-nav" aria-label="Main navigation">{navigation.map(item => <a key={item.id} href={`#${item.id}`} className={`${page === item.id ? 'active' : ''} ${item.id === 'settings' ? 'settings-link' : ''}`} aria-current={page === item.id ? 'page' : undefined}><Icon name={item.icon} /><span>{item.label}</span>{item.id === 'history' && <span className="nav-count mono">{runner.history.total}</span>}{item.id === 'profiles' && <span className="nav-count mono">{runner.profiles.length}</span>}</a>)}</nav><div className="sidebar-bottom"><div className="local-label"><span className="status-dot" />Local workspace</div><div className="workspace-account"><span className="avatar">AW</span><span>Personal workspace<small>Python runner</small></span></div></div></aside>
    <main id="main-content" className="main" tabIndex={-1}><header className="topbar"><span className="breadcrumb">Workspace</span><span className="slash">/</span><strong>{page === 'workspace' ? 'Login run' : navigation.find(item => item.id === page)?.label}</strong><span className="demo-badge">Local runner · {runner.connection}</span><a className="text-button mode-switch" href="?mode=demo#workspace">Open demo</a></header>
      <div className="sr-only" role="status" aria-live="polite">{runner.busy ? words(runner.busy) : run ? `Run ${run.snapshot.state}. Authentication ${words(run.authentication)}.` : runner.connection}</div>
      {(runner.error || runner.connection !== 'connected' || runner.health?.cleanup_required || (runner.health && !runner.health.execution_available) || runner.pending) && <div className="help-note connection-notice" role="status"><Icon name="info" /><div><h2>{runner.health?.cleanup_required ? 'Browser cleanup needs attention' : runner.pending ? 'Start request awaiting confirmation' : runner.connection === 'connected' ? runner.health && !runner.health.execution_available ? 'Runner execution unavailable' : 'Runner request needs attention' : 'Connect to the local runner'}</h2><p>{runner.error || (runner.health?.cleanup_required ? 'Cleanup is unverified. New starts remain blocked. Inspect the active run; authentication results are preserved separately.' : runner.pending ? 'A start may already have been accepted. Resolve start safely reuses the saved request ID.' : runner.health && !runner.health.execution_available ? 'The runner cannot start execution. This implementation requires Linux containment; check the local runner logs, then reconnect.' : 'Start autoweb-api from this repository, then reconnect. No demo fallback is enabled.')}</p></div><button className="button" disabled={runner.connection === 'connecting'} onClick={() => void runner.reconnect()}>Reconnect</button></div>}
      {runner.health?.active_run_id && runner.health.active_run_id !== run?.snapshot.run_id && <div className="plain-note active-notice"><p>A run is active. New starts are blocked until its cleanup completes.</p><button className="button" onClick={() => { void runner.inspect(runner.health!.active_run_id!); workspace(); }}>Inspect active run</button></div>}
      {page === 'workspace' ? <><section className="run-header" aria-labelledby="run-title"><div className="run-title-row"><div><h1 id="run-title">{profile?.display_name ?? 'Local login workspace'}</h1><div className="run-subtitle"><span>{profile ? `${words(profile.login_mode)} · ${profile.classification}` : 'Connect to load profiles'}</span><span className="slash">/</span><span className="mono run-id">{run?.snapshot.run_id ?? 'New run'}</span></div></div><div className="run-actions"><button className="button config-button" ref={configButton} aria-expanded={showConfig} aria-controls="run-configuration" disabled={locked || !runner.profiles.length} onClick={() => setShowConfig(!showConfig)}><Icon name="settings" /><span>Run configuration</span></button><button className="button stop-button" disabled={!run?.active || !!runner.busy || run.cleanup_required || run.snapshot.state === 'stopping' || runner.connection !== 'connected'} onClick={() => void runner.stop()}><Icon name="stop" />{runner.busy === 'stopping' || run?.snapshot.state === 'stopping' ? 'Stopping…' : 'Stop run'}</button><button className="button primary" disabled={!canStart} onClick={() => { setFollow(true); void runner.start(); }}><Icon name="play" />{runner.busy === 'preflight' ? 'Checking configuration…' : runner.busy === 'starting' ? 'Starting…' : runner.pending ? 'Resolve start' : run?.active ? 'Run active' : run ? 'Run again' : 'Start run'}</button></div></div>
        <div className="run-metadata"><span><Icon name="monitor" /><b>Chromium</b> · {(effective?.browser.headless ?? runner.overrides.headless) ? 'Headless' : 'Headed'}</span><span>Slow motion <b className="mono">{effective?.browser.slow_mo_ms ?? runner.overrides.slow_mo_ms ?? 0} ms</b></span><span>Observed elapsed <b className="mono">{formatDuration(events.at(-1)?.elapsed_seconds ?? duration(run))}</b></span><span>AI calls <b className="mono">{ai?.calls ?? 'Unreported'}</b></span>{run && <StateBadge state={run.snapshot.state} />}</div>
        {profile?.classification === 'live' && !run && <p className="live-notice">Live profile selected. Start run explicitly contacts the approved website{profile.required_capabilities.includes('gmail') ? ' and Gmail mailbox' : ''}{runner.overrides.ai_enabled ? ' with AI assistance enabled' : ''}.</p>}
      </section>{showConfig && <div id="run-configuration"><LocalConfiguration profiles={runner.profiles} profileId={runner.profileId} overrides={runner.overrides} apply={configure} close={closeConfig} /></div>}
      <section className="run-workspace" aria-label="Run inspection"><aside className="steps-pane" aria-labelledby="steps-heading"><div className="section-line"><h2 id="steps-heading">Execution steps</h2><span>{run?.snapshot.steps.filter(step => step.state === 'succeeded').length ?? 0} of {steps.length} complete</span></div><ol className="step-list">{steps.map((step, index) => {
        const snapshot = run?.snapshot.steps.find(item => item.step_id === step.id);
        const state = snapshot?.state ?? 'pending';
        return <li key={step.id}><button className={`step ${state} ${selectedStep?.id === step.id ? 'selected' : ''}`} aria-current={selectedStep?.id === step.id ? 'step' : undefined} onClick={() => { setSelected(step.id); setFollow(false); if (matchMedia('(max-width: 740px)').matches) inspector.current?.scrollIntoView({ behavior: 'instant', block: 'start' }); }}><span className="step-icon">{state === 'succeeded' ? <Icon name="check" /> : state === 'failed' ? <Icon name="close" /> : index + 1}</span><span className="step-copy"><span className="step-title">{stepTitle(step)}</span><span className="step-subtitle">{snapshot?.diagnostic ? words(snapshot.diagnostic) : words(state)}{step.optional ? ' · optional' : ''}</span></span>{snapshot?.elapsed_seconds != null && <span className="step-duration mono">{formatDuration(snapshot.elapsed_seconds)}</span>}</button></li>;
      })}</ol>{!steps.length && <p className="section-note">No runnable steps available.</p>}<div className="step-support"><label className="follow-toggle"><input type="checkbox" checked={follow} onChange={event => setFollow(event.target.checked)} />Follow active step</label><p>The headed browser opens separately. Closing this tab does not stop execution.</p>{run && <button className="text-button" onClick={() => exportJson(run.snapshot.run_id, { record: run, events, events_partial: runner.resynced })}>Export run JSON<Icon name="arrow" /></button>}</div></aside><div className="inspector-container" ref={inspector}><LocalInspector run={run} events={events} step={selectedStep} resynced={runner.resynced} /></div></section></> : <LocalPages page={page} runner={runner} workspace={workspace} configure={configure} locked={locked} />}
      <footer className="app-footer"><span><span className="status-dot" />Runner connection: {runner.connection}</span><span>Sanitized observations · external services unverified</span></footer>
    </main></div>;
}
