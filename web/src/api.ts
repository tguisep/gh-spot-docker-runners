/**
 * The one place that talks to the daemon.
 *
 * Same-origin by default: the dashboard is served by the daemon itself, and `npm run dev`
 * proxies these paths to it. There is nothing to configure and no base URL to get wrong.
 */

import type { Health, JobLogs, Logs, Pool, Runner, Stats, Tick } from './types';

/** A failure the UI can show a person, rather than a stack trace or a bare `TypeError`. */
export class ApiError extends Error {
    constructor(
        message: string,
        readonly status: number,
        options?: ErrorOptions,
    ) {
        super(message, options);
        this.name = 'ApiError';
    }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
        response = await fetch(path, {
            ...init,
            headers: { Accept: 'application/json', ...init?.headers },
        });
    } catch (cause) {
        // The daemon being down is the single likeliest reason to be looking at this page, so
        // it gets a sentence rather than "Failed to fetch".
        throw new ApiError('the daemon is not answering — is it running?', 0, { cause });
    }

    if (!response.ok) {
        throw new ApiError(await detail(response), response.status);
    }
    return (await response.json()) as T;
}

/** The API answers failures as `{detail}`; anything else falls back to the status line. */
async function detail(response: Response): Promise<string> {
    try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body.detail === 'string' && body.detail) return body.detail;
    } catch {
        // Not JSON. The status text below is all there is.
    }
    return `${response.status} ${response.statusText}`.trim();
}

export const api = {
    health: () => request<Health>('/health'),
    pools: () => request<Pool[]>('/pools'),

    runners: (options: { pool?: string; includeTerminal?: boolean; usage?: boolean } = {}) => {
        const query = new URLSearchParams();
        if (options.pool) query.set('pool', options.pool);
        if (options.includeTerminal) query.set('include_terminal', 'true');
        if (options.usage) query.set('usage', 'true');
        const suffix = query.size ? `?${query}` : '';
        return request<Runner[]>(`/runners${suffix}`);
    },

    logs: (reference: string, tail = 200) =>
        request<Logs>(`/runners/${encodeURIComponent(reference)}/logs?tail=${tail}`),

    jobLogs: (reference: string, tail = 500) =>
        request<JobLogs>(`/runners/${encodeURIComponent(reference)}/job-logs?tail=${tail}`),

    stop: (reference: string, force = false) =>
        request<Runner>(`/runners/${encodeURIComponent(reference)}?force=${force}`, {
            method: 'DELETE',
        }),

    reconcile: () => request<Tick>('/reconcile', { method: 'POST' }),

    stats: (sinceSeconds?: number) =>
        request<Stats>(
            sinceSeconds === undefined ? '/stats' : `/stats?since_seconds=${sinceSeconds}`,
        ),
};
