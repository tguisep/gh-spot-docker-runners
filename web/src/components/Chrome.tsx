import type { ReactNode } from 'react';

import type { RunnerState } from '../types';

/** A runner's state, coloured the way the CLI colours it. */
export function StateBadge({ state }: { state: RunnerState }) {
    return <span className={`badge state-${state}`}>{state}</span>;
}

export function Panel({
    title,
    actions,
    children,
}: {
    title: string;
    actions?: ReactNode;
    children: ReactNode;
}) {
    return (
        <section className="panel">
            <header className="panel-head">
                <h2>{title}</h2>
                {actions ? <div className="panel-actions">{actions}</div> : null}
            </header>
            {children}
        </section>
    );
}

/**
 * The three states every panel can be in, in one place so they cannot drift apart.
 *
 * `error` shows alongside stale data rather than replacing it: a failed poll is usually
 * transient, and blanking the fleet because one request timed out is worse than saying so.
 */
export function Status({
    loading,
    error,
    empty,
    emptyMessage = 'nothing here',
}: {
    loading: boolean;
    error?: string | undefined;
    empty?: boolean;
    emptyMessage?: string;
}) {
    if (error) return <p className="notice error">{error}</p>;
    if (loading) return <p className="notice">loading…</p>;
    if (empty) return <p className="notice dim">{emptyMessage}</p>;
    return null;
}

export function Bar({ value, of, label }: { value: number; of: number; label: string }) {
    const share = of > 0 ? Math.min(1, value / of) : 0;
    return (
        <div className="bar" title={label} aria-label={label}>
            <div className="bar-fill" style={{ width: `${share * 100}%` }} />
            <span className="bar-text">
                {value}/{of}
            </span>
        </div>
    );
}
