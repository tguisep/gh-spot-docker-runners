import { useState } from 'react';

import { api } from '../api';
import { Panel, Status } from '../components/Chrome';
import { duration, percent } from '../format';
import { usePoll } from '../usePoll';
import type { Usage } from '../types';

const WINDOWS = [
    { label: '24 hours', seconds: 86_400 },
    { label: '7 days', seconds: 604_800 },
    { label: '30 days', seconds: 2_592_000 },
    { label: 'everything', seconds: undefined },
] as const;

function UsageTable({
    heading,
    rows,
    total,
}: {
    heading: string;
    rows: Usage[];
    total: Usage;
}) {
    if (rows.length === 0) return null;
    const withTotal = rows.length > 1 ? [...rows, { ...total, key: 'all' }] : rows;

    return (
        <table>
            <thead>
                <tr>
                    <th>{heading}</th>
                    <th className="num">runners</th>
                    <th className="num">jobs</th>
                    <th className="num">fail</th>
                    <th className="num">fail%</th>
                    <th className="num">busy</th>
                    <th className="num">avg job</th>
                    <th className="num">avg wait</th>
                    <th className="num">used</th>
                    <th className="num">live</th>
                </tr>
            </thead>
            <tbody>
                {withTotal.map((row, index) => (
                    <tr
                        key={row.key || '(none)'}
                        className={index === rows.length ? 'total' : ''}
                    >
                        <th scope="row">{row.key || '(none)'}</th>
                        <td className="num">{row.runners}</td>
                        <td className="num">{row.jobs}</td>
                        <td className={`num ${row.failed ? 'bad' : 'dim'}`}>{row.failed}</td>
                        <td className="num">{row.runners ? percent(row.failure_rate) : '—'}</td>
                        <td className="num">{duration(row.busy_seconds)}</td>
                        <td className="num">
                            {row.jobs ? duration(row.mean_busy_seconds) : '—'}
                        </td>
                        <td className="num">
                            {row.jobs ? duration(row.mean_wait_seconds) : '—'}
                        </td>
                        <td className="num">
                            {row.alive_seconds ? percent(row.utilisation) : '—'}
                        </td>
                        <td className="num dim">{row.live || '—'}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

export function Stats() {
    const [seconds, setSeconds] = useState<number | undefined>(604_800);
    const stats = usePoll(() => api.stats(seconds), 30_000);

    return (
        <>
            <Panel
                title={stats.data?.host ? `usage on ${stats.data.host}` : 'usage'}
                actions={
                    <select
                        value={seconds ?? ''}
                        onChange={(event) =>
                            setSeconds(
                                event.target.value ? Number(event.target.value) : undefined,
                            )
                        }
                    >
                        {WINDOWS.map((window) => (
                            <option key={window.label} value={window.seconds ?? ''}>
                                {window.label}
                            </option>
                        ))}
                    </select>
                }
            >
                <Status
                    loading={stats.loading}
                    error={stats.error}
                    empty={stats.data?.events_read === 0}
                    emptyMessage="nothing recorded in this window"
                />
                {stats.data && stats.data.events_read > 0 ? (
                    <>
                        <p className="notice dim">
                            {stats.data.events_read} event(s) read
                            {stats.data.since
                                ? ` since ${new Date(stats.data.since).toLocaleString()}`
                                : ''}
                        </p>
                        <UsageTable
                            heading="repository"
                            rows={stats.data.by_repository}
                            total={stats.data.total}
                        />
                        <UsageTable
                            heading="pool"
                            rows={stats.data.by_pool}
                            total={stats.data.total}
                        />
                    </>
                ) : null}
            </Panel>

            {stats.data?.failures.length ? (
                <Panel title="failures">
                    <table>
                        <thead>
                            <tr>
                                <th>reason</th>
                                <th className="num">count</th>
                            </tr>
                        </thead>
                        <tbody>
                            {stats.data.failures.map((failure) => (
                                <tr key={failure.reason}>
                                    <th scope="row" className="bad">
                                        {failure.reason}
                                    </th>
                                    <td className="num">{failure.count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </Panel>
            ) : null}
        </>
    );
}
