import { useEffect, useRef, useState } from 'react';
import { defaultConfig, initialSnapshot, newRun, terminal, tickRun } from './model';
import type { DemoRun, RunConfig } from './model';

export function useDemoRunner() {
  const [config, setConfig] = useState<RunConfig>(defaultConfig);
  const [run, setRun] = useState<DemoRun>(initialSnapshot);
  const [history, setHistory] = useState<DemoRun[]>([]);
  const nextId = useRef(25);
  useEffect(() => {
    if (!run.playing) return;
    const timer = window.setInterval(() => setRun(current => tickRun(current, config)), 500 + config.slowMo);
    return () => window.clearInterval(timer);
  }, [run.playing, config]);
  useEffect(() => {
    if (terminal(run.status)) setHistory(previous => previous.some(item => item.id === run.id) ? previous : [run, ...previous]);
  }, [run]);
  function start(fresh = false) {
    if (!fresh && !terminal(run.status) && run.status !== 'idle') {
      setRun(current => ({ ...current, playing: true }));
    } else {
      setRun(newRun(config, `DEMO-${String(nextId.current++).padStart(3, '0')}`));
    }
  }
  function stop() {
    setRun(current => ({ ...current, status: 'cancelled', playing: false, completedAt: new Date().toISOString(), events: [...current.events, { elapsed: current.elapsed, message: 'Demo stopped by you. No live services contacted.', kind: 'runner', step: current.current }] }));
  }
  function configure(next: RunConfig) {
    setConfig(next);
    setRun(newRun(next, `DEMO-${String(nextId.current++).padStart(3, '0')}`, false));
  }
  function inspect(snapshot: DemoRun) {
    setConfig(current => snapshot.configSnapshot ?? ({ ...current, profileId: snapshot.profileId }));
    setRun({ ...snapshot, playing: false });
  }
  return { config, run, history, start, stop, configure, inspect };
}
