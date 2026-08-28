/**
 * Read an endpoint, and keep reading it.
 *
 * The daemon has no push channel — it polls GitHub, and nothing pushes to a browser — so the
 * dashboard polls too. Three behaviours matter and are easy to get wrong:
 *
 *  - a failed refresh keeps the last good data on screen, so a blip does not blank the page;
 *  - polling stops while the tab is hidden, so a dashboard left open overnight is not a
 *    steady stream of requests against a home server;
 *  - a response that arrives after the component unmounted, or after a newer request was
 *    issued, is dropped rather than applied out of order.
 *
 * `enabled` turns the timer off without unmounting, which is what a paused log tail needs:
 * the lines already read stay on screen.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export interface Poll<T> {
    data: T | undefined;
    error: string | undefined;
    loading: boolean;
    refresh: () => void;
}

export function usePoll<T>(fetcher: () => Promise<T>, everyMs = 5000, enabled = true): Poll<T> {
    const [data, setData] = useState<T | undefined>(undefined);
    const [error, setError] = useState<string | undefined>(undefined);
    const [loading, setLoading] = useState(true);

    // Kept in a ref so a caller can pass an inline arrow function without restarting the timer
    // on every render, which would otherwise poll far faster than asked.
    const latest = useRef(fetcher);
    latest.current = fetcher;

    const generation = useRef(0);

    const read = useCallback(async () => {
        const mine = ++generation.current;
        try {
            const value = await latest.current();
            if (mine !== generation.current) return;
            setData(value);
            setError(undefined);
        } catch (caught) {
            if (mine !== generation.current) return;
            setError(caught instanceof Error ? caught.message : String(caught));
        } finally {
            if (mine === generation.current) setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (!enabled) return;

        let timer: ReturnType<typeof setInterval> | undefined;

        const start = () => {
            if (timer !== undefined) return;
            void read();
            timer = setInterval(() => void read(), everyMs);
        };
        const stop = () => {
            if (timer === undefined) return;
            clearInterval(timer);
            timer = undefined;
        };

        const onVisibility = () => (document.hidden ? stop() : start());
        document.addEventListener('visibilitychange', onVisibility);
        if (!document.hidden) start();

        return () => {
            document.removeEventListener('visibilitychange', onVisibility);
            stop();
            // Anything in flight belongs to a component that no longer exists.
            generation.current += 1;
        };
    }, [read, everyMs, enabled]);

    return { data, error, loading, refresh: () => void read() };
}
