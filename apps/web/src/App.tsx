import { useEffect, useRef, useState } from 'react';
import { Configuration } from './components/Configuration';
import { Icon } from './components/Icon';
import type { IconName } from './components/Icon';
import { Inspector } from './components/Inspector';
import { SecondaryPages } from './components/SecondaryPages';
import { formatClock, formatDuration, getProfile, isMailStep, stepStatus, terminal } from './demo/model';
import type { PageName, RunConfig } from './demo/model';
import { useDemoAdapter } from './runner/useDemoAdapter';
import { LocalApp } from './local/LocalApp';

const navigation: { id: PageName; label: string; icon: IconName }[] = [
  { id: 'workspace', label: 'Workspace', icon: 'workspace' },
  { id: 'history', label: 'Run history', icon: 'history' },
  { id: 'profiles', label: 'Site profiles', icon: 'profiles' },
  { id: 'connections', label: 'Connections', icon: 'connections' },
  { id: 'settings', label: 'Settings', icon: 'settings' },
];
function pageFromHash(): PageName {
  const candidate = window.location.hash.slice(1);
  return navigation.some(item => item.id === candidate) ? candidate as PageName : 'workspace';
}
export function App() {
  return new URLSearchParams(window.location.search).get('mode') === 'demo' ? <DemoApp /> : <LocalApp />;
}
export function DemoApp() {
  const runner = useDemoAdapter();
  const { run, config, history } = runner;
  const [page, setPage] = useState<PageName>(pageFromHash);
  const [selected, setSelected] = useState(3);
  const [follow, setFollow] = useState(true);
  const [showConfiguration, setShowConfiguration] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const configurationButton = useRef<HTMLButtonElement>(null);
  const inspectorRef = useRef<HTMLDivElement>(null);
  const profile = getProfile(run.profileId);
  const selectedIndex = Math.min(selected, profile.steps.length - 1);
  const completed = run.status === 'succeeded' ? profile.steps.length : run.current;
  useEffect(() => {
    function onHash() { if (window.location.hash === '#main-content') return; setPage(pageFromHash()); setShowConfiguration(false); }
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  useEffect(() => { if (follow) setSelected(run.current); }, [run.current, follow]);
  useEffect(() => {
    if (terminal(run.status)) setAnnouncement(run.status === 'succeeded' ? 'Simulated login completed successfully.' : run.status === 'failed' ? 'Simulated verification email timed out.' : 'Demo stopped.');
  }, [run.status]);
  useEffect(() => { document.title = `${navigation.find(item => item.id === page)?.label} · AutoWeb Studio`; }, [page]);
  function navigate(next: PageName) { window.location.hash = next; setPage(next); }
  function closeConfiguration() { setShowConfiguration(false); configurationButton.current?.focus(); }
  function configure(next: RunConfig) {
    runner.configure({ ...next, outcome: next.profileId === 'local-password' ? 'success' : next.outcome });
    setSelected(0); setFollow(true); setShowConfiguration(false); navigate('workspace');
    setAnnouncement('Configuration applied. Start the demo when you are ready.');
  }
  function restart() { setFollow(true); setSelected(0); runner.start(true); }
  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to main content</a>
    <aside className="sidebar" aria-label="Workspace navigation">
      <a className="brand" href="#workspace" aria-label="AutoWeb workspace"><span className="brand-mark"><Icon name="logo" /></span>AutoWeb</a>
      <nav className="main-nav" aria-label="Main navigation">{navigation.map(item => <a key={item.id} href={`#${item.id}`} className={`${page === item.id ? 'active' : ''} ${item.id === 'settings' ? 'settings-link' : ''}`} aria-current={page === item.id ? 'page' : undefined}><Icon name={item.icon} /><span>{item.label}</span>{item.id === 'history' && history.length > 0 && <span className="nav-count mono">{history.length}</span>}{item.id === 'profiles' && <span className="nav-count mono">03</span>}</a>)}</nav>
      <div className="sidebar-bottom"><div className="local-label"><span className="status-dot" />Local workspace</div><div className="workspace-account"><span className="avatar">AW</span><span>Personal workspace<small>UI demonstration</small></span></div></div>
    </aside>
    <main id="main-content" className="main" tabIndex={-1}>
      <header className="topbar"><span className="breadcrumb">Workspace</span><span className="slash">/</span><strong>{page === 'workspace' ? 'Login run' : navigation.find(item => item.id === page)?.label}</strong><span className="demo-badge">UI preview · demo data</span><a className="text-button" href="?mode=local#workspace">Use local runner</a><button className="icon-button help-button" aria-label="About this demo" aria-expanded={showHelp} onClick={() => setShowHelp(!showHelp)}><Icon name="info" /></button></header>
      {showHelp && <div className="help-note"><Icon name="info" /><div><h2>A working preview, powered by simulated events.</h2><p>Explore the steps, run a successful demo or simulate an email timeout. Nothing contacts a real website, mailbox, or AI service. The production browser will open in a separate window.</p></div><button className="icon-button" aria-label="Close demo information" onClick={() => setShowHelp(false)}><Icon name="close" /></button></div>}
      <div className="sr-only" role="status" aria-live="polite">{announcement}</div>
      {page === 'workspace' ? <>
        <section className="run-header" aria-labelledby="run-title"><div className="run-title-row"><div><h1 id="run-title">{profile.name}</h1><div className="run-subtitle"><span>{profile.method}</span><span className="slash">/</span><span className="mono">{run.id}</span>{!run.playing && run.status === 'waiting' && <span className="snapshot-label">Sample snapshot</span>}</div></div><div className="run-actions"><button className="button config-button" ref={configurationButton} aria-expanded={showConfiguration} aria-controls="run-configuration" disabled={run.playing} title={run.playing ? 'Stop the demo before changing configuration' : 'Configure the next demo'} onClick={() => setShowConfiguration(!showConfiguration)}><Icon name="settings" /><span>Run configuration</span></button><button className="button stop-button" disabled={terminal(run.status) || run.status === 'idle'} onClick={() => { runner.stop(); setAnnouncement('Demo stopped. Completed steps are preserved.'); }}><Icon name="stop" />Stop demo</button><button className="button primary" disabled={run.playing} onClick={() => { setFollow(true); runner.start(); setAnnouncement('Simulated run started.'); }}><Icon name="play" />{run.playing ? 'Demo running' : terminal(run.status) ? 'Run again' : run.status === 'waiting' ? 'Continue demo' : 'Start demo'}</button></div></div>
          <div className="run-metadata"><span><Icon name="monitor" /><b>{config.engine}</b> · Headed</span><span>Slow motion <b className="mono">{config.slowMo} ms</b></span><span>Elapsed <b className="mono">{formatClock(run.elapsed)}</b></span><span>AI calls <b className="mono">0</b></span><span className="metadata-state"><span className={`status-dot ${run.status}`} />{run.playing ? 'Simulation running' : terminal(run.status) ? 'Demo ended' : run.status === 'idle' ? 'Ready to start' : 'Snapshot · select Continue to run'}</span></div>
        </section>
        {showConfiguration && <div id="run-configuration"><Configuration key={run.id} config={config} onApply={configure} onClose={closeConfiguration} /></div>}
        <section className="run-workspace" aria-label="Run inspection"><aside className="steps-pane" aria-labelledby="steps-heading"><div className="section-line"><h2 id="steps-heading">Execution steps</h2><span>{completed} of {profile.steps.length} complete</span></div><ol className="step-list">{profile.steps.map((step, index) => {
          const status = stepStatus(run, index);
          const sub = status === 'succeeded' ? index === 1 ? 'Credential masked' : 'Completed' : status === 'waiting' ? 'Checking for a fresh message' : status === 'running' ? 'Action in progress' : status === 'failed' ? 'Email timeout' : status === 'cancelled' ? 'Stopped by you' : 'Pending';
          const duration = status === 'succeeded' ? isMailStep(profile, index) ? Math.min(12, config.timeout - 1) : step.duration : index === run.current && run.status !== 'idle' ? run.stepElapsed : undefined;
          return <li key={step.title}><button className={`step ${status} ${selectedIndex === index ? 'selected' : ''}`} aria-current={selectedIndex === index ? 'step' : undefined} onClick={() => { setSelected(index); setFollow(false); if (window.matchMedia('(max-width: 740px)').matches) inspectorRef.current?.scrollIntoView({ behavior: 'instant', block: 'start' }); }}><span className="step-icon">{status === 'succeeded' ? <Icon name="check" /> : status === 'failed' ? <Icon name="close" /> : index + 1}</span><span className="step-copy"><span className="step-title">{step.title}</span><span className="step-subtitle">{sub}</span></span>{duration !== undefined && <span className="step-duration mono">{formatDuration(duration)}</span>}</button></li>;
        })}</ol><div className="step-support"><label className="follow-toggle"><input type="checkbox" checked={follow} onChange={event => setFollow(event.target.checked)} />Follow active step</label><p>The headed browser opens in a separate window. This UI demo does not launch a browser.</p></div></aside><div className="inspector-container" ref={inspectorRef}><Inspector run={run} config={config} selected={selectedIndex} onConfigure={() => setShowConfiguration(true)} onRestart={restart} /></div></section>
      </> : <SecondaryPages page={page} history={history} config={config} playing={run.playing} onInspect={snapshot => { runner.inspect(snapshot); setSelected(snapshot.current); setFollow(false); navigate('workspace'); }} onChooseProfile={id => configure({ ...config, profileId: id })} onApply={configure} onWorkspace={() => navigate('workspace')} />}
      <footer className="app-footer"><span><span className="status-dot" />Local execution · illustrative run</span><span>Secrets masked · no live services connected</span></footer>
    </main>
  </div>;
}
