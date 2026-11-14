import { useDemoRunner } from '../demo/useDemoRunner';
import type { DemoRun } from '../demo/model';
import type { Runner } from './Runner';

/** Timer-based data remains exclusively inside the explicitly selected demo. */
export function useDemoAdapter() {
  const demo = useDemoRunner();
  const controls: Runner<DemoRun, boolean> = {
    mode: 'demo',
    async start(fresh = false) { demo.start(fresh); },
    async stop() { demo.stop(); },
  };
  return { ...demo, mode: controls.mode, start: (fresh = false) => controls.start(fresh), stop: () => controls.stop(demo.run.id) };
}
