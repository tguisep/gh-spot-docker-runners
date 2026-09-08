import { useState } from 'react';

import { api } from '../api';
import { Bar, Panel, Status } from '../components/Chrome';
import { duration } from '../format';
import { usePoll } from '../usePoll';
import type { HostPressure, QueueEntry, WaitReason, WorkClass } from '../types';

/**
 * Which of the eight reasons is worth colouring, and how.
 *
 * `assignable` and `starting` are green because they are not problems: the fleet has done
 * its part. `no-pool` and `host-busy` are red because neither clears on its own — one is a
 * configuration answer, the other is a machine that needs room made on it.
 */
const REASON_CLASS: Record<WaitReason, string> = {
    assignable: 'ok',
    starting: 'ok',
    'pool-at-capacity': 'warn',
    'tick-limit': 'warn',
    'host-at-capacity': 'warn',
    'host-busy': 'bad',
    contended: 'warn',
    'no-pool': 'bad',
};

/**
 * Only the two ends of the scale are coloured.
 *
 * A colour per rung is a legend to memorise; what the reader needs at a glance is whether the
 * stuck work is the branch everybody shares or somebody's unfinished draft.
 */
const CLASS_CLASS: Record<WorkClass, string> = {
    'default-branch': 'ok',
    manual: 'ok',
    'pull-request': '',
    branch: '',
    draft: 'dim',
    scheduled: 'dim',
};

export function Queue() {
    const [pool, setPool] = useState('');
    const queue = usePoll(() => api.queue(pool || undefined), 5000);
    const view = queue.data;

    const pools = (view?.pools ?? []).map((pressure) => pressure.pool);

    return (
        <>
            {view && view.taken_at === null ? (
                <p className="notice">
                    The daemon has not written a queue reading yet. It records one every tick,
                    so this means it is not running, or has not finished its first pass.
                </p>
            ) : null}

            {view?.stale ? (
                <p className="notice error">
                    This reading is {duration(view.age_seconds)} old. The daemon writes one
                    every poll interval, so nothing below can be trusted as current — check that
                    it is running: <code>systemctl status ghspot</code>
                </p>
            ) : null}

            {view?.unreadable.map((repository) => (
                <p className="notice error" key={repository}>
                    <code>{repository}</code> could not be read on the last tick. Anything
                    queued there is missing from this page.
                </p>
            ))}

            {view?.host.holding ? <p className="notice error">{view.host.holding}</p> : null}

            <Panel
                title="queue"
                actions={
                    <>
                        <select value={pool} onChange={(event) => setPool(event.target.value)}>
                            <option value="">every pool</option>
                            {pools.map((name) => (
                                <option key={name} value={name}>
                                    {name}
                                </option>
                            ))}
                        </select>
                        {view?.taken_at ? (
                            <span className="dim">read {duration(view.age_seconds)} ago</span>
                        ) : null}
                    </>
                }
            >
                <Status
                    loading={queue.loading}
                    error={queue.error}
                    empty={view?.entries.length === 0}
                    emptyMessage="nothing queued"
                />
                {view?.entries.length ? (
                    <>
                        <p className="notice dim">
                            {view.total} job(s) queued, {view.delayed} waiting on capacity —
                            longest {duration(view.longest_wait_seconds)}
                        </p>
                        <table>
                            <thead>
                                <tr>
                                    <th className="num">waiting</th>
                                    <th>job</th>
                                    <th>repository</th>
                                    <th>kind</th>
                                    <th className="num">prio</th>
                                    <th>pool</th>
                                    <th className="num">#</th>
                                    <th>status</th>
                                    <th>why</th>
                                </tr>
                            </thead>
                            <tbody>
                                {view.entries.map((entry) => (
                                    <tr key={entry.job_id}>
                                        <td className="num">
                                            {duration(entry.waiting_seconds)}
                                        </td>
                                        <th scope="row">
                                            <JobLink entry={entry} />
                                        </th>
                                        <td className="dim">{entry.repository}</td>
                                        <td className={CLASS_CLASS[entry.work_class]}>
                                            {entry.work_class}
                                        </td>
                                        <td className="num">{entry.urgency}</td>
                                        <td>
                                            {entry.pool || <span className="bad">none</span>}
                                        </td>
                                        <td className="num">{entry.position || '—'}</td>
                                        <td className={REASON_CLASS[entry.reason]}>
                                            {entry.reason}
                                        </td>
                                        <td className="dim">{entry.detail}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </>
                ) : null}
            </Panel>

            <Panel title="pressure">
                <Status
                    loading={queue.loading && !view}
                    empty={view?.pools.length === 0}
                    emptyMessage="no pools configured"
                />
                {view?.pools.length ? (
                    <table>
                        <thead>
                            <tr>
                                <th>pool</th>
                                <th className="num">prio</th>
                                <th className="num">queued</th>
                                <th className="num">free</th>
                                <th>capacity</th>
                                <th className="num">wanted</th>
                                <th className="num">starting</th>
                                <th>held by</th>
                            </tr>
                        </thead>
                        <tbody>
                            {view.pools.map((pressure) => (
                                <tr key={pressure.pool}>
                                    <th scope="row">{pressure.pool}</th>
                                    <td className="num">{pressure.priority}</td>
                                    <td className={`num ${pressure.queued ? 'warn' : ''}`}>
                                        {pressure.queued || '—'}
                                    </td>
                                    <td className="num">{pressure.available}</td>
                                    <td>
                                        <Bar
                                            value={pressure.active}
                                            of={pressure.max_runners}
                                            label={`${pressure.active} of ${pressure.max_runners} in use`}
                                        />
                                    </td>
                                    <td className="num">{pressure.wanted || '—'}</td>
                                    <td className="num">{pressure.launching || '—'}</td>
                                    <td className="warn">{pressure.blocked_by}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                ) : null}
            </Panel>

            <Panel title="host">
                <HostFacts host={view?.host} />
                {view?.notes.length ? (
                    <ul className="notes">
                        {view.notes.map((note) => (
                            <li key={note} className="dim">
                                {note}
                            </li>
                        ))}
                    </ul>
                ) : null}
            </Panel>
        </>
    );
}

/**
 * The job's name, linked to its page on the forge.
 *
 * A new tab, because the dashboard is a thing you keep open and watch while a burst drains —
 * navigating away from it to read one job's log is the wrong trade. `noreferrer` comes with
 * `noopener`, which is what stops the opened page reaching back through `window.opener`.
 *
 * The link is the forge's own `html_url`, so an Enterprise install points at its own host.
 * A job the forge gave no URL for renders as plain text rather than a dead link.
 */
function JobLink({ entry }: { entry: QueueEntry }) {
    if (!entry.url) return <>{entry.title}</>;
    return (
        <a
            href={entry.url}
            target="_blank"
            rel="noreferrer"
            title={`open ${entry.title} on the forge`}
        >
            {entry.title}
        </a>
    );
}

/**
 * Each reading beside the limit it is judged against.
 *
 * A percentage on its own says nothing about whether it is a problem, and the limit lives in
 * a file on the server — so showing `71%` alone sends the reader to go and look it up.
 */
function HostFacts({ host }: { host: HostPressure | undefined }) {
    if (!host) return <p className="notice dim">no reading</p>;

    const gauges: { name: string; value: number | null; limit: number | null }[] = [
        { name: 'cpu', value: host.cpu_percent, limit: host.cpu_high_water },
        { name: 'memory', value: host.memory_percent, limit: host.memory_high_water },
        { name: 'disk', value: host.disk_percent, limit: host.disk_high_water },
        // Named apart from "disk" on purpose: one is how full the filesystem is, the other
        // how busy the device under it is, and a host can be fine on one while the other is
        // the reason nothing finishes.
        { name: 'disk io', value: host.io_percent, limit: host.io_high_water },
    ];
    const measured = gauges.filter((gauge) => gauge.value !== null);

    if (!measured.length && host.containers_running === null) {
        return (
            <p className="notice dim">
                nothing measured — the host is only probed when a launch is wanted and a limit
                is configured
            </p>
        );
    }

    return (
        <dl className="facts">
            {measured.map((gauge) => (
                <div key={gauge.name}>
                    <dt>{gauge.name}</dt>
                    <dd
                        className={
                            gauge.limit !== null && (gauge.value ?? 0) >= gauge.limit
                                ? 'bad'
                                : ''
                        }
                    >
                        {Math.round(gauge.value ?? 0)}%
                        {gauge.limit !== null ? (
                            <span className="dim"> / {Math.round(gauge.limit)}%</span>
                        ) : null}
                    </dd>
                </div>
            ))}
            {host.containers_running !== null ? (
                <div>
                    <dt>containers</dt>
                    <dd>
                        {host.containers_running}
                        {host.max_containers !== null ? (
                            <span className="dim"> / {host.max_containers}</span>
                        ) : null}
                    </dd>
                </div>
            ) : null}
        </dl>
    );
}
