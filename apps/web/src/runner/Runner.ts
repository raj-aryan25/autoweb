/** Both adapters keep execution separate from the components displaying it. */
export interface Runner<Run, Config> {
  readonly mode: 'local' | 'demo';
  start(config: Config): Promise<Run | void>;
  stop(id: string): Promise<Run | void>;
}
