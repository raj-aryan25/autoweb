import { useCallback, useEffect, useRef, useState } from 'react';
import type { ProfileDescriptor, RunEvent, RunOverrides, RunPage, RunRecord, StartRun } from '../transport/contracts';
import { ApiError, localApi } from './localApi';
import type { Health } from './localApi';

export const defaultOverrides: RunOverrides = { headless: false, slow_mo_ms: 0, login_timeout_seconds: 120, verification_timeout_seconds: 60, ai_enabled: false };
export function saved(key: string) { try { return localStorage.getItem(`autoweb.${key}`); } catch { return null; } }
function save(key: string, value: string | null) { try { if (value === null) localStorage.removeItem(`autoweb.${key}`); else localStorage.setItem(`autoweb.${key}`, value); } catch { /* Storage can be disabled. The current session still works. */ } }
function pendingStart(): StartRun | null {
  try { const value = JSON.parse(saved('pending-start') ?? 'null'); return value && typeof value.request_id === 'string' && typeof value.profile_id === 'string' ? value : null; } catch { return null; }
}
export function errorMessage(error: unknown) {
  const code = error instanceof ApiError ? error.code : 'connection_lost';
  return ({ connection_lost: 'Cannot reach the local runner. Start autoweb-api and reconnect. Existing execution continues independently.', run_active: 'Another run is active. Inspect it before starting another.', cleanup_required: 'Browser cleanup is unverified. New runs remain blocked; inspect the active run and consult the runner logs.', request_id_conflict: 'This start request conflicts with saved server data. Reconnect to inspect the accepted run.', invalid_configuration: 'The configuration is invalid. Check the timeout limits and profile readiness.' } as Record<string, string>)[code] ?? 'The runner could not complete this request. Reconnect and inspect the saved run before trying again.';
}
export function useLocalRunner() {
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'reconnecting' | 'disconnected'>('connecting');
  const [profiles, setProfiles] = useState<ProfileDescriptor[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [history, setHistory] = useState<RunPage>({ runs: [], total: 0, offset: 0, limit: 25 });
  const [run, setRun] = useState<RunRecord | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [profileId, setProfileId] = useState('controlled-password');
  const [overrides, setOverrides] = useState(defaultOverrides);
  const [busy, setBusy] = useState<'preflight' | 'starting' | 'stopping' | null>(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(pendingStart);
  const [revision, setRevision] = useState(0);
  const [resynced, setResynced] = useState(false);
  const lock = useRef(false);
  const selection = useRef(0);
  const alive = useRef(true);
  const current = useRef<RunRecord | null>(null);
  const offset = useRef(0);
  const commit = useCallback((value: RunRecord) => {
    if (!alive.current) return;
    const previous = current.current;
    if (previous?.snapshot.run_id === value.snapshot.run_id && (value.snapshot.cursor < previous.snapshot.cursor || value.updated_at < previous.updated_at)) return;
    current.current = value; setRun(value); save('selected-run', value.snapshot.run_id);
  }, []);
  const refresh = useCallback(async () => {
    const [h, page] = await Promise.all([localApi.health(), localApi.history(offset.current)]);
    if (alive.current) { setHealth(h); setHistory(page); }
  }, []);
  const inspect = useCallback(async (id: string) => {
    const generation = ++selection.current;
    try {
      const value = await localApi.read(id);
      if (generation !== selection.current || !alive.current) return;
      setEvents([]); setResynced(false); setProfileId(value.snapshot.profile_id); setOverrides(value.overrides);
      commit(value); setRevision(n => n + 1);
    } catch (e) { if (alive.current) setError(errorMessage(e)); }
  }, [commit]);
  const reconnect = useCallback(async () => {
    setConnection('connecting'); setError('');
    try {
      await localApi.connect();
      const [p, h, page] = await Promise.all([localApi.profiles(), localApi.health(), localApi.history(offset.current)]);
      if (!alive.current) return;
      setProfiles(p); setHealth(h); setHistory(page);
      const id = current.current?.snapshot.run_id ?? saved('selected-run') ?? h.active_run_id;
      if (id) await inspect(id);
      if (alive.current) setConnection('connected');
    } catch (e) { if (alive.current) { setConnection('disconnected'); setError(errorMessage(e)); } }
  }, [inspect]);
  useEffect(() => { alive.current = true; void reconnect(); return () => { alive.current = false; selection.current++; }; }, [reconnect]);
  const runId = run?.snapshot.run_id;
  const streamEnabled = connection === 'connected' || connection === 'reconnecting';
  useEffect(() => {
    if (!runId || !streamEnabled) return;
    let disposed = false;
    let close = () => {};
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cursor = 0;
    let syncing = false;
    let dirty = false;
    const isCurrent = () => !disposed && current.current?.snapshot.run_id === runId;
    async function synchronize() {
      dirty = true;
      if (syncing) return;
      syncing = true;
      try {
        while (dirty && isCurrent()) {
          dirty = false;
          const value = await localApi.read(runId!);
          if (isCurrent()) commit(value);
        }
        if (isCurrent()) await refresh();
      } catch (e) { if (isCurrent()) setError(errorMessage(e)); }
      finally { syncing = false; if (dirty && isCurrent()) void synchronize(); }
    }
    function attach() {
      if (!isCurrent()) return;
      close = localApi.subscribe(runId!, cursor, message => {
        if (!isCurrent()) return;
        setConnection('connected');
        if (message.type === 'progress') {
          if (message.event.run_id !== runId || message.event.sequence <= cursor) return;
          cursor = message.event.sequence;
          setEvents(previous => [...previous.filter(event => event.sequence !== message.event.sequence), message.event].slice(-1024));
          if (cursor > 1024) setResynced(true);
          void synchronize();
        } else if (message.type === 'resync') {
          void localApi.read(runId!).then(value => {
            if (!isCurrent()) return;
            commit(value); cursor = value.snapshot.cursor; setEvents([]); setResynced(true);
            void refresh();
            if (value.active && !value.cleanup_required) attach();
          }).catch(lost);
        } else if (message.code === 'service_shutdown') { lost(); }
        else { void synchronize(); }
      }, lost);
    }
    function lost() {
      if (!isCurrent()) return;
      setConnection('reconnecting');
      timer = setTimeout(async () => {
        try {
          await localApi.connect();
          const value = await localApi.read(runId!);
          if (!isCurrent()) return;
          commit(value); await refresh(); setConnection('connected');
          if (value.snapshot.cursor > cursor || (value.active && !value.cleanup_required)) attach();
        } catch { if (isCurrent()) lost(); }
      }, 2000);
    }
    attach();
    return () => { disposed = true; close(); if (timer) clearTimeout(timer); };
  }, [runId, revision, streamEnabled, commit, refresh]);
  async function start() {
    if (lock.current || connection !== 'connected') return;
    lock.current = true; setError('');
    try {
      let request = pending;
      if (!request) {
        setBusy('preflight');
        const result = await localApi.preflight({ profile_id: profileId, overrides });
        if (result.status !== 'ready') { setError('This profile is unavailable or its configuration is invalid. Choose a configured profile. No browser was started.'); return; }
        request = { request_id: crypto.randomUUID(), profile_id: profileId, overrides: { ...overrides } };
        save('pending-start', JSON.stringify(request)); setPending(request);
      }
      setBusy('starting');
      const value = await localApi.start(request);
      save('pending-start', null); setPending(null);
      selection.current++; setEvents([]); setResynced(false); commit(value); setRevision(n => n + 1); await refresh();
    } catch (e) { setError(errorMessage(e)); if (e instanceof ApiError && e.status === 0) setConnection('disconnected'); }
    finally { lock.current = false; setBusy(null); }
  }
  async function stop() {
    if (lock.current || !run || !run.active) return;
    lock.current = true; setBusy('stopping'); setError('');
    try { commit(await localApi.stop(run.snapshot.run_id)); await refresh(); }
    catch (e) { setError(errorMessage(e)); }
    finally { lock.current = false; setBusy(null); }
  }
  function configure(id: string, config: RunOverrides) {
    selection.current++; current.current = null; setRun(null); setEvents([]); save('selected-run', null);
    setProfileId(id); setOverrides(config); setResynced(false);
  }
  async function page(next: number) {
    try { const value = await localApi.history(next); offset.current = next; setHistory(value); }
    catch (e) { setError(errorMessage(e)); }
  }
  return { connection, profiles, health, history, run, events, profileId, overrides, busy, pending, error, resynced, start, stop, inspect, reconnect, configure, page };
}
export type LocalRunnerState = ReturnType<typeof useLocalRunner>;
