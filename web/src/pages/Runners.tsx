import { useState } from 'react';
import { Link } from 'react-router-dom';

import { api, ApiError } from '../api';
import { Panel, StateBadge, Status } from '../components/Chrome';
import { bytes, duration, percent, shortId } from '../format';
import { usePoll } from '../usePoll';
import type { Runner } from '../types';

export function Runners() {
    const [pool, setPool] = useState('');
    const [includeTerminal, setIncludeTerminal] = useState(false);
    const [usage, setUsage] = useState(false);
    const [busy, setBusy] = useState<string | undefined>(undefined);
    const [problem, setProblem] = useState<string | undefined>(undefined);

    const runners = usePoll(
        () => api.runners({ ...(pool ? { pool } : {}), includeTerminal, usage }),
        5000,
    );

    const pools = [...new Set((runners.data ?? []).map((runner) => runner.pool))].sort();

    async function stop(runner: Runner, force: boolean) {
        setBusy(runner.id);
        setProblem(undefined);
        try {
            await api.stop(runner.id, force);
            runners.refresh();
        } catch (caught) {
            // 409 is the API refusing to fail somebody's build without being told twice. Say what
            // it means and what the second click does, rather than showing the raw message alone.
            const conflict = caught instanceof ApiError && caught.status === 409;
            const message = caught instanceof Error ? caught.message : String(caught);
            setProblem(conflict ? `${message} Use "force" to stop it anyway.` : message);
        } finally {
            setBusy(undefined);
        }
    }

    return (
        <>
            <Panel
                title="runners"
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
                        <label className="check">
                            <input
                                type="checkbox"
                                checked={includeTerminal}
                                onChange={(event) => setIncludeTerminal(event.target.checked)}
                            />
                            retired and failed
                        </label>
                        <label
                            className="check"
                            title="Costs one call to the Docker Engine per running container"
                        >
                            <input
                                type="checkbox"
                                checked={usage}
                                onChange={(event) => setUsage(event.target.checked)}
                            />
                            cpu / memory
                        </label>
                    </>
                }
            >
                <Status
                    loading={runners.loading}
                    error={runners.error}
                    empty={runners.data?.length === 0}
                    emptyMessage="no runners"
                />
                {problem ? <p className="notice error">{problem}</p> : null}
                {runners.data?.length ? (
                    <table>
                        <thead>
                            <tr>
                                <th>runner</th>
                                <th>pool</th>
                                <th>state</th>
                                <th className="num">age</th>
                                <th className="num">in state</th>
                                {usage ? <th className="num">cpu</th> : null}
                                {usage ? <th className="num">memory</th> : null}
                                <th>container</th>
                                <th>job</th>
                                <th />
                            </tr>
                        </thead>
                        <tbody>
                            {runners.data.map((runner) => (
                                <tr key={runner.id}>
                                    <th scope="row">
                                        {runner.name}
                                        {runner.failure_reason ? (
                                            <span className="reason">
                                                {runner.failure_reason}
                                            </span>
                                        ) : null}
                                    </th>
                                    <td>{runner.pool}</td>
                                    <td>
                                        <StateBadge state={runner.state} />
                                    </td>
                                    <td className="num">{duration(runner.age_seconds)}</td>
                                    <td className="num">
                                        {duration(runner.time_in_state_seconds)}
                                    </td>
                                    {usage ? (
                                        <td className="num">
                                            {runner.cpu_percent === null
                                                ? '—'
                                                : `${Math.round(runner.cpu_percent)}%`}
                                        </td>
                                    ) : null}
                                    {usage ? (
                                        <td className="num">
                                            {bytes(runner.memory_bytes)}
                                            {runner.memory_percent === null ? null : (
                                                <span className="dim">
                                                    {' '}
                                                    ({percent(runner.memory_percent / 100)})
                                                </span>
                                            )}
                                        </td>
                                    ) : null}
                                    <td>
                                        <code className="dim">
                                            {shortId(runner.container_id) || '—'}
                                        </code>
                                    </td>
                                    <td className="num dim">{runner.current_job_id ?? '—'}</td>
                                    <td className="row-actions">
                                        <Link
                                            className="button"
                                            to={`/logs?runner=${runner.id}`}
                                        >
                                            logs
                                        </Link>
                                        <button
                                            onClick={() => void stop(runner, false)}
                                            disabled={
                                                busy === runner.id || runner.state === 'retired'
                                            }
                                        >
                                            stop
                                        </button>
                                        <button
                                            className="danger"
                                            onClick={() => void stop(runner, true)}
                                            disabled={
                                                busy === runner.id || runner.state === 'retired'
                                            }
                                        >
                                            force
                                        </button>
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
