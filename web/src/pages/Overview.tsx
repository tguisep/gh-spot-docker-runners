import { api } from '../api';
import { Bar, Panel, Status } from '../components/Chrome';
import { Setup } from '../components/Setup';
import { duration } from '../format';
import { usePoll } from '../usePoll';
import type { Tick } from '../types';
import { useState } from 'react';

export function Overview() {
    const health = usePoll(() => api.health(), 10_000);
    const pools = usePoll(() => api.pools(), 5000);
    const [tick, setTick] = useState<Tick | undefined>(undefined);
    const [ticking, setTicking] = useState(false);
    const [tickError, setTickError] = useState<string | undefined>(undefined);

    async function reconcileNow() {
        setTicking(true);
        setTickError(undefined);
        try {
            setTick(await api.reconcile());
            pools.refresh();
        } catch (caught) {
            setTickError(caught instanceof Error ? caught.message : String(caught));
        } finally {
            setTicking(false);
        }
    }

    // A fresh install would otherwise show a correct and useless picture: zero pools, zero
    // runners, and no clue that anything is missing.
    if (health.data && !health.data.configured) {
        return <Setup health={health.data} />;
    }

    return (
        <>
            {health.data?.config_stale ? (
                <p className="notice">
                    The configuration has been edited since the daemon read it. Settings —
                    pools, labels, limits — are read once, at startup, so nothing here reflects
                    the change yet. <code>sudo systemctl restart ghspot</code>
                </p>
            ) : null}

            <Panel title="daemon">
                <Status loading={health.loading} error={health.error} />
                {health.data ? (
                    <dl className="facts">
                        <div>
                            <dt>status</dt>
                            <dd className={health.data.docker ? 'ok' : 'bad'}>
                                {health.data.status}
                            </dd>
                        </div>
                        <div>
                            <dt>docker</dt>
                            <dd className={health.data.docker ? 'ok' : 'bad'}>
                                {health.data.docker ? 'reachable' : 'unreachable'}
                            </dd>
                        </div>
                        <div>
                            <dt>host</dt>
                            <dd>{health.data.host || '—'}</dd>
                        </div>
                        <div>
                            <dt>version</dt>
                            <dd>{health.data.version}</dd>
                        </div>
                        <div>
                            <dt>pools</dt>
                            <dd>{health.data.pools}</dd>
                        </div>
                    </dl>
                ) : null}
            </Panel>

            <Panel
                title="pools"
                actions={
                    <button onClick={() => void reconcileNow()} disabled={ticking}>
                        {ticking ? 'reconciling…' : 'reconcile now'}
                    </button>
                }
            >
                <Status
                    loading={pools.loading}
                    error={pools.error}
                    empty={pools.data?.length === 0}
                    emptyMessage="no pools configured"
                />
                {tickError ? <p className="notice error">{tickError}</p> : null}
                {tick ? (
                    <p className="notice">
                        tick in {duration(tick.duration_seconds)}: launched {tick.launched},
                        retired {tick.retired}, terminated {tick.terminated}, repaired{' '}
                        {tick.repaired}
                        {tick.errors.length ? ` — ${tick.errors.length} error(s)` : ''}
                    </p>
                ) : null}
                {pools.data?.length ? (
                    <table>
                        <thead>
                            <tr>
                                <th>pool</th>
                                <th>repository</th>
                                <th className="num">idle</th>
                                <th className="num">busy</th>
                                <th className="num">starting</th>
                                <th className="num">queued</th>
                                <th>capacity</th>
                                <th>labels</th>
                            </tr>
                        </thead>
                        <tbody>
                            {pools.data.map((pool) => (
                                <tr key={pool.name}>
                                    <th scope="row">{pool.name}</th>
                                    <td className="dim">{pool.repository}</td>
                                    <td className="num">{pool.idle}</td>
                                    <td className="num">{pool.busy}</td>
                                    <td className="num">{pool.starting}</td>
                                    <td className={`num ${pool.queued_jobs ? 'warn' : ''}`}>
                                        {pool.queued_jobs}
                                    </td>
                                    <td>
                                        <Bar
                                            value={pool.active}
                                            of={pool.max_runners}
                                            label={`${pool.active} of ${pool.max_runners} in use`}
                                        />
                                    </td>
                                    <td className="labels">
                                        {pool.labels.map((label) => (
                                            <code key={label}>{label}</code>
                                        ))}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                ) : null}
            </Panel>
        </>
    );
}
