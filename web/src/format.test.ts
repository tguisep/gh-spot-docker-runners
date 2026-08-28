import { describe, expect, it } from 'vitest';

import { bytes, duration, percent, shortId } from './format';

describe('duration', () => {
    it('reads as an operator would say it', () => {
        expect(duration(0)).toBe('0s');
        expect(duration(45)).toBe('45s');
        expect(duration(63)).toBe('1m03s');
        expect(duration(600)).toBe('10m00s');
        expect(duration(3600)).toBe('1h00m');
        expect(duration(5415)).toBe('1h30m');
    });

    it('never renders a negative time', () => {
        // Two clocks are involved — the daemon's and the browser's — and a skew must not put a
        // minus sign in a table.
        expect(duration(-5)).toBe('0s');
    });
});

describe('percent', () => {
    it('rounds to whole points', () => {
        expect(percent(0)).toBe('0%');
        expect(percent(0.5)).toBe('50%');
        expect(percent(0.916)).toBe('92%');
        expect(percent(1)).toBe('100%');
    });
});

describe('shortId', () => {
    it('takes the twelve characters Docker accepts', () => {
        expect(shortId('0123456789abcdef0123')).toBe('0123456789ab');
    });

    it('survives a runner that has no container yet', () => {
        expect(shortId(null)).toBe('');
    });
});

describe('bytes', () => {
    it('uses the binary units docker stats uses', () => {
        expect(bytes(512)).toBe('512B');
        expect(bytes(2048)).toBe('2.0KiB');
        expect(bytes(300_000_000)).toBe('286.1MiB');
        expect(bytes(4 * 1024 ** 3)).toBe('4.0GiB');
    });

    it('says nothing rather than zero when there is no sample', () => {
        // "not measured" and "using nothing" are different facts.
        expect(bytes(null)).toBe('—');
    });
});
