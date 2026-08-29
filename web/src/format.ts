/**
 * Formatting shared by every table.
 *
 * These are pure and unit-tested, because "5m03s" appearing as "303s" is the kind of thing
 * nobody notices in review and everybody notices at 2am.
 */

/** Compact and approximate, matching `render.duration` in the CLI so the two agree. */
export function duration(seconds: number): string {
    const whole = Math.max(0, Math.floor(seconds));
    if (whole < 60) return `${whole}s`;
    if (whole < 3600)
        return `${Math.floor(whole / 60)}m${String(whole % 60).padStart(2, '0')}s`;
    const hours = Math.floor(whole / 3600);
    return `${hours}h${String(Math.floor((whole % 3600) / 60)).padStart(2, '0')}m`;
}

export function percent(fraction: number): string {
    return `${Math.round(fraction * 100)}%`;
}

/** Container ids are 64 characters; the first 12 are what every Docker command accepts. */
export function shortId(id: string | null): string {
    return (id ?? '').slice(0, 12);
}

export function timestamp(iso: string): string {
    const at = new Date(iso);
    return Number.isNaN(at.getTime()) ? iso : at.toLocaleString();
}

/** Binary units, matching `docker stats` and the CLI so the three can be compared. */
export function bytes(count: number | null): string {
    if (count === null) return '—';
    let size = count;
    for (const unit of ['B', 'KiB', 'MiB', 'GiB'] as const) {
        if (size < 1024 || unit === 'GiB') {
            return unit === 'B' ? `${Math.round(size)}B` : `${size.toFixed(1)}${unit}`;
        }
        size /= 1024;
    }
    return `${size.toFixed(1)}GiB`;
}
