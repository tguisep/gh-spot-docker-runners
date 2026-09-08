/**
 * The app actually mounts and renders what the daemon told it.
 *
 * A React app that throws on mount serves a 200 with an empty body, which every check short
 * of opening it in a browser reports as healthy. This is the cheap version of opening it.
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';
import type { Health, Pool, Queue, Stats } from './types';

const HEALTH: Health = {
    status: 'ok',
    version: '0.4.0',
    host: 'runner-box-2',
    pools: 1,
    docker: true,
    configured: true,
    config_stale: false,
    setup_reason: null,
};

const POOL: Pool = {
    name: 'default',
    repository: 'tguisep/gh-spot-docker-runners',
    labels: ['self-hosted', 'linux'],
    min_idle: 1,
    max_runners: 4,
    idle: 1,
    busy: 2,
    starting: 0,
    active: 3,
    queued_jobs: 5,
    oldest_wait_seconds: 92,
    headroom: 1,
    runners: [],
};

const QUEUE: Queue = {
    taken_at: '2026-08-28T12:00:00Z',
    age_seconds: 3,
    stale: false,
    total: 1,
    delayed: 1,
    longest_wait_seconds: 92,
    entries: [
        {
            job_id: 991,
            run_id: 55,
            repository: 'tguisep/gh-spot-docker-runners',
            workflow: 'ci',
            job_name: 'test',
            title: 'ci / test',
            labels: ['self-hosted', 'linux'],
            pool: 'default',
            priority: 1,
            position: 1,
            reason: 'pool-at-capacity',
            detail: 'pool is at max_runners=4 with 4 up',
            waiting_seconds: 92,
            delayed: true,
        },
    ],
    pools: [
        {
            pool: 'default',
            repository: 'tguisep/gh-spot-docker-runners',
            priority: 1,
            queued: 1,
            available: 0,
            active: 4,
            max_runners: 4,
            launching: 0,
            wanted: 1,
            blocked_by: 'pool is at max_runners=4 with 4 up',
            oldest_wait_seconds: 92,
        },
    ],
    host: {
        cpu_percent: 42,
        memory_percent: 61,
        disk_percent: null,
        io_percent: 93,
        containers_running: 4,
        cpu_high_water: 85,
        memory_high_water: 90,
        disk_high_water: null,
        io_high_water: 90,
        max_containers: 6,
        max_cpus: null,
        max_memory_bytes: null,
        holding: '',
    },
    notes: [],
    unreadable: [],
};

const EMPTY_STATS: Stats = {
    host: 'runner-box-2',
    since: null,
    until: '2026-08-28T12:00:00Z',
    events_read: 0,
    total: {
        key: '',
        runners: 0,
        jobs: 0,
        failed: 0,
        completed: 0,
        idle_runners: 0,
        failure_rate: 0,
        busy_seconds: 0,
        alive_seconds: 0,
        mean_busy_seconds: 0,
        mean_wait_seconds: 0,
        utilisation: 0,
        live: 0,
    },
    by_repository: [],
    by_pool: [],
    failures: [],
};

function answer(body: unknown): Response {
    return { ok: true, status: 200, json: async () => body } as Response;
}

beforeEach(() => {
    vi.stubGlobal(
        'fetch',
        vi.fn(async (input: RequestInfo | URL) => {
            const path = String(input);
            if (path.startsWith('/health')) return answer(HEALTH);
            if (path.startsWith('/pools')) return answer([POOL]);
            if (path.startsWith('/queue')) return answer(QUEUE);
            if (path.startsWith('/runners')) return answer([]);
            if (path.startsWith('/stats')) return answer(EMPTY_STATS);
            throw new Error(`unexpected request to ${path}`);
        }),
    );
});

afterEach(() => {
    // Vitest is not running with globals, so testing-library does not register its own
    // cleanup. Without this the previous test's DOM is still mounted and every query that
    // should find one element finds two.
    cleanup();
    vi.unstubAllGlobals();
});

describe('the dashboard', () => {
    it('shows the setup screen instead of an empty fleet on a fresh install', async () => {
        // The alternative is a correct and completely useless picture: zero pools, zero
        // runners, and no clue that anything is missing.
        vi.stubGlobal(
            'fetch',
            vi.fn(async (input: RequestInfo | URL) => {
                const path = String(input);
                if (path.startsWith('/health'))
                    return answer({
                        ...HEALTH,
                        configured: false,
                        setup_reason:
                            "pool 'default' still points at the packaged OWNER/REPOSITORY",
                    });
                return answer([]);
            }),
        );

        render(
            <MemoryRouter initialEntries={['/']}>
                <App />
            </MemoryRouter>,
        );

        await waitFor(() => {
            expect(screen.getByText(/not configured yet/)).toBeTruthy();
        });
        expect(screen.getByText(/sudo ghspot setup/)).toBeTruthy();
        expect(screen.getByText(/OWNER\/REPOSITORY/)).toBeTruthy();
    });

    it('renders the daemon and its pools', async () => {
        render(
            <MemoryRouter initialEntries={['/']}>
                <App />
            </MemoryRouter>,
        );

        expect(screen.getByRole('heading', { name: 'ghspot' })).toBeTruthy();

        await waitFor(() => {
            expect(screen.getByText('tguisep/gh-spot-docker-runners')).toBeTruthy();
        });
        // The queue is the number that decides whether the fleet is keeping up.
        expect(screen.getByText('5')).toBeTruthy();
        expect(screen.getByText('3/4')).toBeTruthy();
    });

    it('says so when a page has nothing to show, rather than rendering an empty table', async () => {
        render(
            <MemoryRouter initialEntries={['/runners']}>
                <App />
            </MemoryRouter>,
        );

        await waitFor(() => {
            expect(screen.getByText('no runners')).toBeTruthy();
        });
    });

    it('says why a job is queued rather than only that it is', async () => {
        // The number alone was the whole problem: an operator could see five queued and had
        // no way to find out whether the fleet was full, the host was, or nothing served it.
        render(
            <MemoryRouter initialEntries={['/queue']}>
                <App />
            </MemoryRouter>,
        );

        await waitFor(() => {
            expect(screen.getByText('ci / test')).toBeTruthy();
        });
        expect(screen.getByText('pool-at-capacity')).toBeTruthy();
        expect(screen.getAllByText(/max_runners=4/).length).toBeGreaterThan(0);
    });

    it('shows a saturated disk beside the mark it is judged against', async () => {
        // A disk can be a tenth full and completely busy. Without its own gauge that host
        // looks healthy on every number the page shows.
        render(
            <MemoryRouter initialEntries={['/queue']}>
                <App />
            </MemoryRouter>,
        );

        await waitFor(() => {
            expect(screen.getByText('disk io')).toBeTruthy();
        });
        const reading = screen.getByText('disk io').parentElement;
        expect(reading?.textContent).toContain('93%');
        expect(reading?.textContent).toContain('90%');
    });

    it('warns that a stale reading is not the same as an empty queue', async () => {
        vi.stubGlobal(
            'fetch',
            vi.fn(async (input: RequestInfo | URL) => {
                const path = String(input);
                if (path.startsWith('/health')) return answer(HEALTH);
                if (path.startsWith('/queue'))
                    return answer({
                        ...QUEUE,
                        stale: true,
                        age_seconds: 240,
                        total: 0,
                        delayed: 0,
                        entries: [],
                    });
                return answer([]);
            }),
        );

        render(
            <MemoryRouter initialEntries={['/queue']}>
                <App />
            </MemoryRouter>,
        );

        await waitFor(() => {
            expect(screen.getByText(/This reading is 4m00s old/)).toBeTruthy();
        });
    });

    it('reports a daemon that is not answering', async () => {
        vi.stubGlobal(
            'fetch',
            vi.fn(async () => {
                throw new TypeError('Failed to fetch');
            }),
        );

        render(
            <MemoryRouter initialEntries={['/']}>
                <App />
            </MemoryRouter>,
        );

        // Both panels say it: each polls independently, and a panel silently showing nothing
        // would read as "no pools" rather than "cannot reach the daemon".
        await waitFor(() => {
            expect(screen.getAllByText(/the daemon is not answering/)).toHaveLength(2);
        });
    });
});

describe('the host', () => {
    it('is named in the header on every page', async () => {
        render(
            <MemoryRouter initialEntries={['/runners']}>
                <App />
            </MemoryRouter>,
        );

        // Not only on the overview: several hosts can serve one repository, and two tabs
        // open on two of them are indistinguishable without this.
        await waitFor(() => expect(screen.getByText('runner-box-2')).toBeTruthy());
    });
});
