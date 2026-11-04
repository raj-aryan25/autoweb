import { useEffect, useRef, useState } from 'react';
import { formatClock, formatDuration, getProfile, isMailStep, stepStatus } from '../demo/model';
import type { DemoRun, RunConfig, RunEvent, StepStatus } from '../demo/model';
import { Icon } from './Icon';

export const statusLabels: Record<StepStatus, string> = { pending: 'Pending', idle: 'Ready', running: 'Running', waiting: 'Waiting', succeeded: 'Completed', failed: 'Failed', cancelled: 'Stopped' };
export function StatusBadge({ status }: { status: StepStatus }) {
  return <span className={`status-badge ${status}`}><span className="status-dot" />{statusLabels[status]}</span>;
}
export function EventTable({ events, showHeading = true }: { events: RunEvent[]; showHeading?: boolean }) {
  return <section className="event-section">{showHeading && <div className="section-line"><h3>Recent events</h3><span>Sanitized execution details</span></div>}
    {events.length ? <div className="table-scroll"><table className="events-table"><caption className="sr-only">Simulated execution events, latest first</caption><thead><tr><th>Elapsed</th><th>Event</th><th>Source</th></tr></thead><tbody>{events.slice().reverse().map((event, index) => <tr key={`${event.elapsed}-${index}`}><td className="mono">{formatClock(event.elapsed)}</td><td>{event.message}</td><td><span className={`event-source ${event.kind}`}>{event.kind}</span></td></tr>)}</tbody></table></div>
      : <div className="inline-empty"><Icon name="empty" /><div><h3>No events for this step</h3><p>Events will appear when the demo reaches this action.</p></div></div>}
  </section>;
}
export function Inspector({ run, config, selected, onConfigure, onRestart }: { run: DemoRun; config: RunConfig; selected: number; onConfigure: () => void; onRestart: () => void }) {
  const [tab, setTab] = useState('Details');
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const profile = getProfile(run.profileId);
  const step = profile.steps[selected];
  const status = stepStatus(run, selected);
  const mail = isMailStep(profile, selected);
  const events = run.events.filter(event => event.step === selected);
  const hasEvidence = status === 'succeeded' && (mail || selected === profile.steps.length - 1);
  const tabs = ['Details', 'Evidence', 'Events'];
  useEffect(() => setTab('Details'), [run.id]);
  let observed = 'This action has not started.';
  if (status === 'succeeded') observed = step.result;
  if (status === 'waiting') observed = 'No matching message yet';
  if (status === 'running') observed = 'The simulated action is in progress.';
  if (status === 'failed') observed = 'No matching email arrived before the timeout.';
  if (status === 'cancelled') observed = 'You stopped the demo before this action completed.';
  function exportEvents() {
    const blob = new Blob([JSON.stringify({ demo: true, run: run.id, profile: profile.id, step: step.title, status, events }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = `${run.id}-step-${selected + 1}.json`; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section className="inspector" aria-labelledby="inspector-title">
    <div className="inspector-heading"><h2 id="inspector-title">{step.title}</h2><StatusBadge status={status} /></div>
    <p className="inspector-description">{step.description}</p>
    <div className="tabs" role="tablist" aria-label="Step inspection">{tabs.map((item, index) => <button key={item} ref={element => { tabRefs.current[index] = element; }} role="tab" id={`tab-${item}`} aria-selected={tab === item} aria-controls={`panel-${item}`} tabIndex={tab === item ? 0 : -1} className={tab === item ? 'active' : ''} onClick={() => setTab(item)} onKeyDown={event => {
      let next = index;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault(); setTab(tabs[next]); tabRefs.current[next]?.focus();
    }}>{item}{item !== 'Details' && <span className="count">{item === 'Events' ? events.length : Number(hasEvidence)}</span>}</button>)}</div>
    <div id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={0} className="tab-panel">
      {tab === 'Details' && <>
        {(status === 'failed' || status === 'cancelled') && <div className={`outcome-notice ${status}`} role="status"><Icon name={status === 'failed' ? 'info' : 'stop'} /><div><h3>{status === 'failed' ? 'Verification email timed out' : 'Demo stopped'}</h3><p>{status === 'failed' ? 'No new email request was sent. Change the demo outcome or start another simulated run.' : 'Completed steps are preserved below. Start a new demo when you are ready.'}</p><div className="notice-actions"><button className="text-button" onClick={onRestart}>Start a new demo<Icon name="arrow" /></button>{status === 'failed' && <button className="text-button" onClick={onConfigure}>Change configuration</button>}</div></div></div>}
        <dl className="conditions"><div><dt>Expected condition</dt><dd>{step.expected}<small>{mail ? 'Received after the code request was submitted' : 'Evaluated against the configured step target'}</small></dd></div><div><dt>Observed condition</dt><dd>{observed}<small>{status === 'waiting' ? 'Polling the simulated test mailbox' : 'Illustrative result · no live website contacted'}</small></dd></div></dl>
        {mail && status !== 'pending' && <div className="wait-meter"><div className="meter-copy"><span>{status === 'waiting' ? 'Waiting for Gmail' : status === 'succeeded' ? 'Matching message found' : 'Verification wait ended'}</span><span className="mono">{formatDuration(status === 'succeeded' ? Math.min(12, config.timeout - 1) : run.stepElapsed)} / {formatDuration(config.timeout)}</span></div><progress value={status === 'succeeded' ? Math.min(12, config.timeout - 1) : run.stepElapsed} max={config.timeout} aria-label="Elapsed verification wait" /><p>Illustrative timeout · checks every 2 seconds · no automatic resend</p></div>}
        <div className="provider-row"><span className="provider-icon"><Icon name={mail ? 'mail' : 'monitor'} /></span><div><h3>{mail ? 'Gmail verification provider' : 'Browser automation'}</h3><p>{mail ? 'Recipient masked · fresh messages only' : `${config.engine} · headed browser opens separately`}</p></div><span className="provider-state">{mail ? 'Demo mailbox state' : 'Simulated action'}</span></div>
        <EventTable events={events.slice(-3)} />
      </>}
      {tab === 'Evidence' && (hasEvidence ? <section className="evidence-record"><div className="section-line"><h3>{mail ? 'Message match record' : 'Authentication assertion'}</h3><span className="status-badge succeeded">Synthetic evidence</span></div><dl className="record-list"><div><dt>Source</dt><dd>{mail ? 'Simulated verification provider' : 'Controlled demo assertion'}</dd></div><div><dt>Condition</dt><dd>{mail ? 'Fresh message matched to this request' : 'Protected-page access confirmed'}</dd></div><div><dt>Verification value</dt><dd>Hidden</dd></div><div><dt>Live data</dt><dd>None · UI demonstration only</dd></div></dl><p className="muted">Screenshots and real authentication evidence will appear here after runner integration.</p></section> : <section className="empty-state"><Icon name="empty" /><h3>No evidence captured</h3><p>{status === 'pending' ? 'This step has not started.' : 'This simulated step has no evidence yet.'} After runner integration, this area will show sanitized evidence from the selected step.</p><button className="button" onClick={() => setTab('Details')}>Back to details</button></section>)}
      {tab === 'Events' && <><div className="events-toolbar"><p>Events for step {selected + 1} only. All values are simulated.</p><button className="button" onClick={exportEvents} disabled={!events.length}>Export JSON</button></div><EventTable events={events} showHeading={false} /></>}
    </div>
  </section>;
}
