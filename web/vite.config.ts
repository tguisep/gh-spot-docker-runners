import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dashboard is served from /ui, not /, and the base has to match or every asset URL in
// the built index.html points at the API instead. It is /ui because the SPA's own routes
// are named after the same things the API's are — /runners, /pools — and mounting it at the
// root would have the two shadow each other.
export default defineConfig({
    base: '/ui/',
    plugins: [react()],
    build: {
        outDir: 'dist',
        emptyOutDir: true,
        // No hashed chunk soup: the whole dashboard is a few hundred kilobytes and it ships
        // inside a .deb, where a predictable file list is what `verify.sh` can assert on.
        chunkSizeWarningLimit: 900,
    },
    test: {
        // The render test mounts real components; the pure ones need no DOM but sharing one
        // environment keeps the command a single `npm test`.
        environment: 'jsdom',
    },
    server: {
        port: 5173,
        // `npm run dev` talks to a daemon running the usual way, so the API is not on this
        // origin. Proxied rather than CORS-enabled, so development and production use the same
        // same-origin request paths and nothing behaves differently only in dev.
        proxy: Object.fromEntries(
            [
                '/health',
                '/pools',
                '/runners',
                '/reconcile',
                '/stats',
                // The nav links to the API's own docs page, which the daemon serves in
                // production. Without these two it opens the dashboard again in dev, which
                // looks like a broken link and is a missing proxy entry.
                '/docs',
                '/openapi.json',
            ].map((path) => [
                path,
                {
                    target: process.env.GHSPOT_API ?? 'http://localhost:8770',
                    changeOrigin: true,
                },
            ]),
        ),
    },
});
