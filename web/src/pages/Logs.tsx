import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { api } from '../api';
import { Panel, StateBadge, Status } from '../components/Chrome';
import { usePoll } from '../usePoll';

const TAILS = [200, 500, 2000] as const;

/**
 * Both logs for one runner, side by side.
 *
 * They are not the same thing on the same schedule, which is the whole reason for showing
 * two panes rather than one:
 *
 *   container   the job as it happens. The runner prints its work to stdout, so this is
 *               live — and it disappears with the container, seconds after the job ends.
 *   GitHub      written when the job *finishes*. Nothing to fetch before then, and it
 *               outlives the container, which is the half the left pane cannot give.
 *
 * So the right pane says what it is waiting for rather than showing an empty box, and fills
 * itself the moment the job completes.
 */

/** Follow the bottom while lines arrive, and stop the moment the reader scrolls up. */
function useTailPane(data: unknown) {
    const pane = useRef<HTMLPreElement>(null);
    const pinned = useRef(true);

    useEffect(() => {
        const element = pane.current;
        if (element && pinned.current) element.scrollTop = element.scrollHeight;
    }, [data]);

    function onScroll() {
        const element = pane.current;
        if (!element) return;
        pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
    }

    return { pane, onScroll, repin: () => (pinned.current = true) };
}

export function Logs() {
    const [params, setParams] = useSearchParams();
    const selected = params.get('runner') ?? '';
    const [tail, setTail] = useState<number>(500);
    const [following, setFollowing] = useState(true);

    const runners = usePoll(() => api.runners({ includeTerminal: true }), 10_000);
    const live = Boolean(selected) && following;

    const container = usePoll(() => api.logs(selected, tail), 2000, live);
    // Slower: this endpoint is a GitHub request, and until the job ends every one of them
    // is a 404 that costs rate limit to learn nothing.
    const forge = usePoll(() => api.jobLogs(selected, tail), 10_000, live);

    const left = useTailPane(container.data);
    const right = useTailPane(forge.data);

    const chosen = runners.data?.find((runner) => runner.id === selected);

    return (
        <>
            <Panel
                title="logs"
                actions={
                    <>
                        <select
                            value={selected}
                            onChange={(event) => {
                                const next = event.target.value;
                                setParams(next ? { runner: next } : {}, { replace: true });
                                left.repin();
                                right.repin();
                            }}
                        >
                            <option value="">choose a runner…</option>
                            {(runners.data ?? []).map((runner) => (
                                <option key={runner.id} value={runner.id}>
                                    {runner.name} · {runner.state}
                                </option>
                            ))}
                        </select>
                        <select
                            value={tail}
                            onChange={(event) => setTail(Number(event.target.value))}
                        >
                            {TAILS.map((lines) => (
                                <option key={lines} value={lines}>
                                    last {lines}
                                </option>
                            ))}
                        </select>
                        <button onClick={() => setFollowing((on) => !on)} disabled={!selected}>
                            {following ? 'pause' : 'follow'}
                        </button>
                        <button
                            onClick={() => {
                                container.refresh();
                                forge.refresh();
                            }}
                            disabled={!selected}
                        >
                            refresh
                        </button>
                    </>
                }
            >
                {!selected ? (
                    <p className="notice dim">pick a runner to follow its output</p>
                ) : (
                    <p className="notice dim">
                        {chosen ? (
                            <>
                                <StateBadge state={chosen.state} /> {chosen.name} ·{' '}
                                {chosen.repository}
                            </>
                        ) : (
                            selected
                        )}
                        {following ? ' · following' : ' · paused'}
                    </p>
                )}
            </Panel>

            {selected ? (
                <div className="split">
                    <Panel title="container — the job as it happens">
                        <Status loading={container.loading} error={container.error} />
                        <pre className="logs" ref={left.pane} onScroll={left.onScroll}>
                            {container.data?.lines ?? ''}
                        </pre>
                    </Panel>

                    <Panel title="github — written when the job finishes">
                        <Status loading={forge.loading} error={forge.error} />
                        {forge.data && !forge.data.available ? (
                            <p className="notice dim">
                                {forge.data.job_id === null
                                    ? 'this runner is not running a job'
                                    : `job ${forge.data.job_id} has not finished, so GitHub has no log for it yet — it appears here when it does. The container pane on the left is the live view.`}
                            </p>
                        ) : null}
                        <pre className="logs" ref={right.pane} onScroll={right.onScroll}>
                            {forge.data?.lines ?? ''}
                        </pre>
                    </Panel>
                </div>
            ) : null}
        </>
    );
}
