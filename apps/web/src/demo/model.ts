export type RunStatus = 'idle' | 'running' | 'waiting' | 'succeeded' | 'failed' | 'cancelled';
export type StepStatus = RunStatus | 'pending';
export type PageName = 'workspace' | 'history' | 'profiles' | 'connections' | 'settings';
export interface WorkflowStep { title: string; description: string; expected: string; result: string; duration: number; }
export interface Profile { id: string; name: string; method: string; source: string; steps: WorkflowStep[]; }
export interface RunConfig { profileId: string; outcome: 'success' | 'timeout'; engine: 'Chromium' | 'Firefox' | 'WebKit'; slowMo: number; timeout: number; }
export interface RunEvent { elapsed: number; message: string; kind: 'browser' | 'waiting' | 'complete' | 'error' | 'runner'; step: number; }
export interface DemoRun { id: string; profileId: string; status: RunStatus; current: number; stepElapsed: number; elapsed: number; playing: boolean; events: RunEvent[]; completedAt?: string; configSnapshot?: RunConfig; }
const step = (title: string, description: string, expected: string, result: string, duration: number): WorkflowStep => ({ title, description, expected, result, duration });
export const profiles: Profile[] = [
  { id: 'expandtesting-otp', name: 'Expand Testing', method: 'Email OTP login', source: 'Saved profile · simulated here', steps: [
    step('Open login page', 'Navigate to the configured login page.', 'The login form is visible and ready.', 'Login page loaded and ready.', 1.2),
    step('Fill email address', 'Fill the configured test identity.', 'The email field accepts the configured identity.', 'Test identity entered. Value masked.', 0.4),
    step('Request verification code', 'Submit the email request once.', 'The site accepts a single verification request.', 'Verification request submitted once.', 2.8),
    step('Wait for verification email', 'Find a fresh email that matches this login request.', 'A new matching verification email', 'A fresh matching message was found.', 12),
    step('Fill verification code', 'Fill the code from the matching message.', 'The verification fields accept the extracted code.', 'Verification code entered. Value masked.', 0.6),
    step('Confirm authentication', 'Check the configured authenticated state.', 'The protected page is accessible in this session.', 'Authenticated state confirmed.', 1.4),
  ]},
  { id: 'local-password', name: 'Local password demo', method: 'Password login', source: 'Controlled local fixture · simulated here', steps: [
    step('Open login page', 'Open the controlled login form.', 'The login form is visible and ready.', 'Local login page loaded.', 1.2),
    step('Fill username', 'Fill the configured test identity.', 'The username field accepts the test identity.', 'Username entered. Value masked.', 0.4),
    step('Fill password', 'Fill the configured test password.', 'The password field accepts the credential.', 'Password entered. Value masked.', 0.4),
    step('Submit login form', 'Submit the controlled form.', 'The form submits without a validation error.', 'Login form submitted.', 0.8),
    step('Confirm authentication', 'Check the authenticated state.', 'The protected page is accessible.', 'Authenticated state confirmed.', 1.4),
  ]},
  { id: 'local-magic-link', name: 'Local magic-link demo', method: 'Email magic link', source: 'Controlled local fixture · simulated here', steps: [
    step('Open login page', 'Open the controlled login form.', 'The email form is visible and ready.', 'Local login page loaded.', 1.2),
    step('Fill email address', 'Fill the configured test identity.', 'The email field accepts the test identity.', 'Email entered. Value masked.', 0.4),
    step('Request magic link', 'Submit the email request once.', 'The form accepts a single email request.', 'Magic-link request submitted once.', 2.8),
    step('Wait for verification email', 'Find the fresh matching link email.', 'A new matching verification email', 'A fresh matching message was found.', 12),
    step('Open verification link', 'Open the verified link in the same session.', 'The verified link is accepted.', 'Verification link opened. URL hidden.', 0.8),
    step('Confirm authentication', 'Check the authenticated state.', 'The protected page is accessible.', 'Authenticated state confirmed.', 1.4),
  ]},
];
export const defaultConfig: RunConfig = { profileId: profiles[0].id, outcome: 'success', engine: 'Chromium', slowMo: 250, timeout: 30 };
export const getProfile = (id: string) => profiles.find(p => p.id === id) ?? profiles[0];
export const isMailStep = (profile: Profile, index: number) => profile.steps[index]?.title === 'Wait for verification email';
export const terminal = (status: RunStatus) => ['succeeded', 'failed', 'cancelled'].includes(status);
export const formatDuration = (seconds: number) => `${seconds.toFixed(1)}s`;
export function formatClock(seconds: number) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, '0');
  return `${minutes}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;
}
export function initialSnapshot(): DemoRun {
  return { configSnapshot: { ...defaultConfig }, id: 'DEMO-024', profileId: profiles[0].id, status: 'waiting', current: 3, stepElapsed: 8, elapsed: 12.4, playing: false, events: [
    { elapsed: 0, message: 'Demo browser initialized. No real browser launched.', kind: 'runner', step: 0 },
    { elapsed: 1.2, message: 'Login page loaded and ready.', kind: 'browser', step: 0 },
    { elapsed: 1.6, message: 'Test identity entered. Value masked.', kind: 'browser', step: 1 },
    { elapsed: 4.4, message: 'Verification request submitted once.', kind: 'browser', step: 2 },
    { elapsed: 6.4, message: 'No matching email found.', kind: 'waiting', step: 3 },
    { elapsed: 10.4, message: 'Waiting for the next mailbox check.', kind: 'waiting', step: 3 },
  ]};
}
export function newRun(config: RunConfig, id: string, playing = true): DemoRun {
  return { configSnapshot: { ...config }, id, profileId: config.profileId, status: playing ? 'running' : 'idle', current: 0, stepElapsed: 0, elapsed: 0, playing, events: playing ? [{ elapsed: 0, message: 'Simulated run started. No live services contacted.', kind: 'runner', step: 0 }] : [] };
}
export function stepStatus(run: DemoRun, index: number): StepStatus {
  if (run.status === 'idle') return 'pending';
  if (run.status === 'succeeded' || index < run.current) return 'succeeded';
  if (index > run.current) return 'pending';
  return run.status;
}
/** A deterministic UI-only simulator. Production transport can replace this boundary. */
export function tickRun(run: DemoRun, config: RunConfig): DemoRun {
  if (!run.playing || terminal(run.status)) return run;
  const profile = getProfile(run.profileId);
  const mail = isMailStep(profile, run.current);
  const targetDuration = mail ? (config.outcome === 'timeout' ? config.timeout : Math.min(profile.steps[run.current].duration, Math.max(2, config.timeout - 1))) : profile.steps[run.current].duration;
  const increment = Math.min(mail ? 1 : 0.4, Math.max(0, targetDuration - run.stepElapsed));
  const stepElapsed = Math.round((run.stepElapsed + increment) * 10) / 10;
  const elapsed = Math.round((run.elapsed + increment) * 10) / 10;
  const updated = { ...run, stepElapsed, elapsed };
  if (mail && config.outcome === 'timeout' && stepElapsed >= config.timeout) {
    return { ...updated, status: 'failed', playing: false, completedAt: new Date().toISOString(), events: [...run.events, { elapsed, message: `Verification email timed out after ${config.timeout}s. No new request was sent.`, kind: 'error', step: run.current }] };
  }
  const duration = mail ? Math.min(profile.steps[run.current].duration, Math.max(2, config.timeout - 1)) : profile.steps[run.current].duration;
  if (stepElapsed >= duration && !(mail && config.outcome === 'timeout')) {
    const events: RunEvent[] = [...run.events, { elapsed, message: profile.steps[run.current].result, kind: 'complete', step: run.current }];
    if (run.current === profile.steps.length - 1) return { ...updated, status: 'succeeded', playing: false, events, completedAt: new Date().toISOString() };
    const current = run.current + 1;
    return { ...updated, current, stepElapsed: 0, status: isMailStep(profile, current) ? 'waiting' : 'running', events };
  }
  if (mail && stepElapsed % 2 === 0) return { ...updated, events: [...run.events, { elapsed, message: 'No matching email found. Polling continues.', kind: 'waiting', step: run.current }] };
  return updated;
}
