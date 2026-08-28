/**
 * The app actually mounts and renders what the daemon told it.
 *
 * A React app that throws on mount serves a 200 with an empty body, which every check short
 * of opening it in a browser reports as healthy. This is the cheap version of opening it.
 */

import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';
import type { Health, Pool, Stats } from './types';

const HEALTH: Health = { status: 'ok', version: '0.4.0', pools: 1, docker: true };

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
    headroom: 1,
    runners: [],
};

const EMPTY_STATS: Stats = {
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
            if (path.startsWith('/runners')) return answer([]);
            if (path.startsWith('/stats')) return answer(EMPTY_STATS);
            throw new Error(`unexpected request to ${path}`);
        }),
    );
});

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('the dashboard', () => {
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
