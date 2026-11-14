import type { PreflightRequest, PreflightResult, ProfileDescriptor, RunEvent, RunPage, RunRecord, StartRun } from '../transport/contracts';
import type { Runner } from './Runner';

export type Health = { status: string; execution_available: boolean; active_run_id: string | null; cleanup_required: boolean };
export class ApiError extends Error {
  constructor(public status: number, public code: string) { super(code); }
}
export type StreamMessage = { type: 'progress'; event: RunEvent } | { type: 'resync' } | { type: 'end'; code: string };
export class LocalApi implements Runner<RunRecord, StartRun> {
  readonly mode = 'local';
  private csrf = '';
  private session?: Promise<void>;
  connect() {
    if (!this.session) this.session = this.request<{ csrf_token: string }>('/session', undefined, false).then(value => { this.csrf = value.csrf_token; }).finally(() => { this.session = undefined; });
    return this.session;
  }
  private async request<T>(path: string, body?: unknown, authenticated = true): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`/api${path}`, { credentials: 'same-origin', method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-AutoWeb-CSRF': this.csrf }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000) });
    } catch { throw new ApiError(0, 'connection_lost'); }
    // A rejected session has not executed a mutation. Refresh once, preserving its request ID.
    if (response.status === 401 && authenticated) { await this.connect(); return this.request<T>(path, body, false); }
    if (!response.ok) {
      const value = await response.json().catch(() => ({}));
      throw new ApiError(response.status, typeof value.code === 'string' ? value.code : 'request_failed');
    }
    return response.json() as Promise<T>;
  }
  health() { return this.request<Health>('/health'); }
  profiles() { return this.request<ProfileDescriptor[]>('/profiles'); }
  preflight(config: PreflightRequest) { return this.request<PreflightResult>('/preflight', config); }
  start(config: StartRun) { return this.request<RunRecord>('/runs', config); }
  stop(id: string) { return this.request<RunRecord>(`/runs/${encodeURIComponent(id)}/stop`, {}); }
  read(id: string) { return this.request<RunRecord>(`/runs/${encodeURIComponent(id)}`); }
  history(offset = 0) { return this.request<RunPage>(`/runs?limit=25&offset=${offset}`); }
  subscribe(id: string, after: number, receive: (message: StreamMessage) => void, lost: () => void) {
    const source = new EventSource(`/api/runs/${encodeURIComponent(id)}/events?after=${after}`, { withCredentials: true });
    source.addEventListener('progress', event => {
      try { receive({ type: 'progress', event: JSON.parse((event as MessageEvent).data) as RunEvent }); }
      catch { source.close(); lost(); }
    });
    source.addEventListener('resync_required', () => { source.close(); receive({ type: 'resync' }); });
    source.addEventListener('stream_end', event => {
      source.close();
      try { receive({ type: 'end', code: JSON.parse((event as MessageEvent).data).code }); }
      catch { lost(); }
    });
    source.onerror = () => { source.close(); lost(); };
    return () => source.close();
  }
}
export const localApi = new LocalApi();
