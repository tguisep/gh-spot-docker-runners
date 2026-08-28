import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { api } from '../api';
import { Panel, StateBadge, Status } from '../components/Chrome';
import { usePoll } from '../usePoll';

const TAILS = [200, 500, 2000] as const;

/**
 * Follow one runner's output.
 *
 * The daemon has no streaming endpoint and does not need one: a runner's log is small, and
 * re-reading the tail every two seconds is indistinguishable from a follow at this size
 * while costing nothing to hold open. What makes it feel live is the scroll behaviour below.
 */
export function Logs() {
    const [params, setParams] = useSearchParams();
    const selected = params.get('runner') ?? '';
    const [tail, setTail] = useState<number>(500);
    const [following, setFollowing] = useState(true);

    const runners = usePoll(() => api.runners({ includeTerminal: true }), 10_000);
    const logs = usePoll(() => api.logs(selected, tail), 2000, Boolean(selected) && following);

    const pane = useRef<HTMLPreElement>(null);
    const pinned = useRef(true);

    // Stay at the bottom while new lines arrive, but stop the moment the reader scrolls up:
    // yanking someone back to the end while they are reading is the one thing a log viewer
    // must not do.
    useEffect(() => {
        const element = pane.current;
        if (element && pinned.current) element.scrollTop = element.scrollHeight;
    }, [logs.data]);

    function onScroll() {
        const element = pane.current;
        if (!element) return;
        const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
        pinned.current = distance < 24;
    }

    const chosen = runners.data?.find((runner) => runner.id === selected);

    return (
        <Panel
            title="logs"
            actions={
                <>
                    <select
                        value={selected}
                        onChange={(event) => {
                            const next = event.target.value;
                            setParams(next ? { runner: next } : {}, { replace: true });
                            pinned.current = true;
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
                    <button onClick={logs.refresh} disabled={!selected}>
                        refresh
                    </button>
                </>
            }
        >
            {!selected ? (
                <p className="notice dim">pick a runner to follow its output</p>
            ) : (
                <>
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
                    <Status loading={logs.loading} error={logs.error} />
                    <pre className="logs" ref={pane} onScroll={onScroll}>
                        {logs.data?.lines ?? ''}
                    </pre>
                </>
            )}
        </Panel>
    );
}
