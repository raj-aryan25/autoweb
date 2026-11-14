import { useEffect, useRef, useState } from 'react';
import type { DisplayStep, RunEvent, RunRecord } from '../transport/contracts';
import { Icon } from '../components/Icon';
import { formatDuration } from '../demo/model';
import { eventText, exportJson, StateBadge, stepTitle, words } from './presentation';

export function LocalInspector({ run, events, step, resynced }: { run: RunRecord | null; events: RunEvent[]; step?: DisplayStep; resynced: boolean }) {
  const [tab, setTab] = useState('Details');
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const runId = run?.snapshot.run_id;
  useEffect(() => setTab('Details'), [runId]);
  const snapshot = run?.snapshot.steps.find(item => item.step_id === step?.id);
  const selectedEvents = events.filter(event => event.step_id === step?.id);
  const artifacts = [...new Map([
    ...(run?.artifacts ?? []).filter(item => item.step_id === step?.id),
    ...selectedEvents.flatMap(event => event.payload.type === 'artifact_registered' ? [event.payload] : []),
  ].map(item => [item.artifact_id, item])).values()];
  const tabs = ['Details', 'Evidence', 'Events'];
  return <section className="inspector" aria-labelledby="inspector-title"><div className="inspector-heading"><h2 id="inspector-title">{step ? stepTitle(step) : 'Ready to inspect'}</h2><StateBadge state={snapshot?.state ?? 'pending'} /></div><p className="inspector-description">{step ? `${step.kind === 'action' ? 'Configured action' : 'Execution phase'}${step.optional ? ' · optional' : ''}. Results come from the Python runner.` : 'Choose a configured profile and start a run to see its observed results.'}</p>
    <div className="tabs" role="tablist" aria-label="Step inspection">{tabs.map((name, index) => <button key={name} ref={element => { refs.current[index] = element; }} role="tab" id={`local-tab-${name}`} aria-selected={name === tab} aria-controls={`local-panel-${name}`} tabIndex={name === tab ? 0 : -1} className={name === tab ? 'active' : ''} onClick={() => setTab(name)} onKeyDown={event => {
      const next = event.key === 'ArrowRight' ? (index + 1) % 3 : event.key === 'ArrowLeft' ? (index + 2) % 3 : event.key === 'Home' ? 0 : event.key === 'End' ? 2 : -1;
      if (next < 0) return; event.preventDefault(); setTab(tabs[next]); refs.current[next]?.focus();
    }}>{name}{name !== 'Details' && <span className="count">{name === 'Events' ? selectedEvents.length : artifacts.length}</span>}</button>)}</div>
    <div className="tab-panel" role="tabpanel" id={`local-panel-${tab}`} aria-labelledby={`local-tab-${tab}`} tabIndex={0}>
      {tab === 'Details' && <><dl className="conditions"><div><dt>Expected condition</dt><dd>{step?.optional ? 'Complete the action, or report an absent optional target.' : 'Report the configured action or phase outcome.'}<small>No skipped or unexecuted step is counted as passed.</small></dd></div><div><dt>Observed condition</dt><dd>{snapshot ? words(snapshot.state) : 'This step has not started.'}<small>{snapshot?.diagnostic ? words(snapshot.diagnostic) : 'Only backend observations are shown.'}</small></dd></div>{snapshot?.elapsed_seconds != null && <div><dt>Observed duration</dt><dd className="mono">{formatDuration(snapshot.elapsed_seconds)}</dd></div>}</dl>
        {step?.phases?.includes('verification_wait') && <div className="plain-note"><Icon name="mail" /><p>Verification wait belongs to this action. Its actual start and receipt events appear in Events; the UI does not infer mailbox polling.</p></div>}
        {run && <dl className="record-list"><div><dt>Authentication</dt><dd>{words(run.authentication)}</dd></div><div><dt>Browser cleanup</dt><dd>{run.cleanup_required ? 'Uncertain · new starts blocked' : run.cleanup_verified ? 'Verified' : 'Awaiting verification'}</dd></div><div><dt>Diagnostic</dt><dd>{run.snapshot.outcome?.diagnostic ? words(run.snapshot.outcome.diagnostic) : 'None reported'}</dd></div><div><dt>Updated</dt><dd><time dateTime={run.updated_at}>{new Date(run.updated_at).toLocaleString()}</time></dd></div></dl>}
      </>}
      {tab === 'Evidence' && (artifacts.length && run ? <section className="evidence-record"><h3>Registered evidence</h3>{artifacts.map(item => <p key={item.artifact_id}><a className="text-button" href={`/api/runs/${encodeURIComponent(run.snapshot.run_id)}/artifacts/${encodeURIComponent(item.artifact_id)}`} target="_blank" rel="noreferrer">{words(item.kind)}<Icon name="arrow" /></a></p>)}<p className="muted">Only sanitized artifacts registered by the backend are available.</p></section> : <section className="empty-state"><Icon name="empty" /><h3>No evidence captured</h3><p>No sanitized artifact is registered for this step. Authentication results are shown separately in Details.</p></section>)}
      {tab === 'Events' && <><div className="events-toolbar"><p>Sanitized events for the selected step.</p><button className="button" disabled={!selectedEvents.length} onClick={() => exportJson(`${runId}-${step?.id}`, { schema_version: 1, run_id: runId, step_id: step?.id, events: selectedEvents })}>Export JSON</button></div>{resynced && <p className="section-note">Earlier events expired. The current snapshot was restored; this list contains subsequent events only.</p>}{selectedEvents.length ? <div className="table-scroll"><table className="events-table"><caption className="sr-only">Observed execution events, latest first</caption><thead><tr><th>Elapsed</th><th>Event</th><th>Time</th></tr></thead><tbody>{selectedEvents.slice().reverse().map(event => <tr key={event.sequence}><td className="mono">{formatDuration(event.elapsed_seconds)}</td><td>{eventText(event)}<small>{event.phase_id}</small></td><td><time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleTimeString()}</time></td></tr>)}</tbody></table></div> : <div className="inline-empty"><Icon name="empty" /><p>No retained events for this step.</p></div>}</>}
    </div>
  </section>;
}
