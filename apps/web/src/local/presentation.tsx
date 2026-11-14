import type { DisplayStep, RunEvent, RunRecord } from '../transport/contracts';
export function words(value: string) { return value.replaceAll('_', ' ').replace(/^./, character => character.toUpperCase()); }
export function stepTitle(step: DisplayStep) {
  return step.kind === 'phase' ? ({ outcome: 'Final outcome', verification_baseline: 'Prepare verification', acknowledgment: 'Acknowledge verification' } as Record<string, string>)[step.id] ?? words(step.id) : words(step.action ?? 'Action');
}
export function eventText(event: RunEvent) {
  const payload = event.payload;
  if (payload.type === 'final_outcome') return `Run ${payload.state} · authentication ${words(payload.authentication).toLowerCase()}`;
  if (payload.type === 'run_state_changed') return `Run ${payload.state}`;
  if (payload.type === 'artifact_registered') return `Registered ${words(payload.kind).toLowerCase()}`;
  if (payload.type === 'step_started') return `${words(payload.action)} started`;
  if (payload.type === 'step_completed') return `${words(payload.action)} ${payload.status}`;
  if (payload.type === 'verification_wait') return `Verification ${payload.status} · ${words(payload.kind)}`;
  return `${words(payload.type)} ${payload.status}`;
}
export function duration(run: RunRecord | null) {
  return run ? Math.max(0, (Date.parse(run.updated_at) - Date.parse(run.created_at)) / 1000) : 0;
}
export function exportJson(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = `${name}.json`; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function StateBadge({ state }: { state: string }) {
  return <span className={`status-badge ${state}`}><span className="status-dot" />{words(state)}</span>;
}
