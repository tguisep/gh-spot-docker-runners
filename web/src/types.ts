/**
 * The API's shapes, mirrored.
 *
 * Hand-written rather than generated: the surface is eight endpoints, and a generator would
 * be a build step and a dependency to keep current for no gain at this size. The names match
 * `interfaces/api/schemas.py` exactly, so a field renamed there fails `npm run typecheck`
 * here as soon as anything reads it.
 */

export type RunnerState =
    'pending' | 'registered' | 'starting' | 'idle' | 'busy' | 'draining' | 'retired' | 'failed';

export interface Runner {
    id: string;
    name: string;
    pool: string;
    repository: string;
    state: RunnerState;
    labels: string[];
    created_at: string;
    github_runner_id: number | null;
    container_id: string | null;
    current_job_id: number | null;
    age_seconds: number;
    time_in_state_seconds: number;
    failure_reason: string | null;
    /** Null unless the request asked for usage: sampling costs a call per container. */
    cpu_percent: number | null;
    memory_bytes: number | null;
    memory_limit_bytes: number | null;
    memory_percent: number | null;
}

export interface Pool {
    name: string;
    repository: string;
    labels: string[];
    min_idle: number;
    max_runners: number;
    idle: number;
    busy: number;
    starting: number;
    active: number;
    queued_jobs: number;
    headroom: number;
    runners: Runner[];
}

export interface Health {
    status: string;
    version: string;
    /** The machine this daemon runs on. Several hosts can serve one repository. */
    host: string;
    pools: number;
    docker: boolean;
    /** False on a fresh install: the daemon is up and nobody has finished the configuration. */
    configured: boolean;
    setup_reason: string | null;
}

export interface Tick {
    started_at: string;
    duration_seconds: number;
    launched: number;
    retired: number;
    terminated: number;
    repaired: number;
    queued_jobs: number;
    errors: string[];
    notes: string[];
}

export interface Logs {
    runner_id: string;
    lines: string;
}

export interface JobLogs {
    runner_id: string;
    job_id: number | null;
    /** False while the job is still running: GitHub writes its log when the job finishes. */
    available: boolean;
    lines: string;
}

export interface Usage {
    key: string;
    runners: number;
    jobs: number;
    failed: number;
    completed: number;
    idle_runners: number;
    failure_rate: number;
    busy_seconds: number;
    alive_seconds: number;
    mean_busy_seconds: number;
    mean_wait_seconds: number;
    utilisation: number;
    live: number;
}

export interface Stats {
    /** The machine these numbers are about — each daemon counts only its own runners. */
    host: string;
    since: string | null;
    until: string;
    events_read: number;
    total: Usage;
    by_repository: Usage[];
    by_pool: Usage[];
    failures: { reason: string; count: number }[];
}

/** A state that still occupies a slot in its pool. Mirrors `OCCUPYING` in the domain. */
export const ACTIVE_STATES: ReadonlySet<RunnerState> = new Set<RunnerState>([
    'pending',
    'registered',
    'starting',
    'idle',
    'busy',
    'draining',
]);
