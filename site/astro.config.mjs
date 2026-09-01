// @ts-check
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import mermaid from 'astro-mermaid';
import starlightVersions from 'starlight-versions';

const REPO = 'https://github.com/tguisep/gh-spot-docker-runners';

export default defineConfig({
    // A project page, so everything is served under the repository name. Links between pages
    // are relative URLs resolved from the linking page's own URL — not `.md` paths, which are
    // emitted verbatim, and not root-absolute ones, which 404 under the base path.
    // scripts/check-site-links.py is what catches either.
    site: 'https://tguisep.github.io',
    base: '/gh-spot-docker-runners',
    integrations: [
        // Before Starlight, which the integration requires. Without it the two ```mermaid
        // fences in reference/architecture render as plain code blocks — which they had
        // been doing since the site was created, because Starlight does not handle mermaid
        // and nothing had been added.
        mermaid({ theme: 'neutral', autoTheme: true }),
        starlight({
            // The unversioned docs at the root track `main`, so a page is written once. A
            // release is snapshotted into its own tree and then left alone — an archive, not
            // a second copy to keep in step.
            plugins: [
                starlightVersions({
                    current: { label: 'Unreleased' },
                    versions: [{ slug: '0.6' }],
                }),
            ],
            title: 'ghspot',
            description:
                'Self-hosted GitHub Actions runners as ephemeral Docker containers, on a machine you own.',
            social: [{ icon: 'github', label: 'GitHub', href: REPO }],
            editLink: { baseUrl: `${REPO}/edit/main/site/` },
            lastUpdated: true,
            sidebar: [
                {
                    label: 'Start here',
                    items: [
                        { slug: 'start/requirements' },
                        { slug: 'start/authentication' },
                        { slug: 'start/install' },
                        { slug: 'start/configure' },
                        { slug: 'start/run' },
                    ],
                },
                {
                    label: 'Pools',
                    items: [
                        { slug: 'guides/pools/labels' },
                        { slug: 'guides/pools/pm' },
                        { slug: 'guides/pools/priority' },
                        { slug: 'guides/pools/gpus' },
                    ],
                },
                {
                    label: 'The host',
                    items: [
                        { slug: 'guides/host/capacity' },
                        { slug: 'guides/host/images' },
                        { slug: 'guides/host/housekeeping' },
                        { slug: 'guides/host/tuning' },
                    ],
                },
                {
                    label: 'Operating it',
                    items: [
                        { slug: 'guides/operate/monitoring' },
                        { slug: 'guides/operate/dashboard' },
                        { slug: 'guides/operate/api' },
                        { slug: 'guides/operate/own-ci' },
                    ],
                },
                {
                    label: 'Reference',
                    items: [
                        { slug: 'reference/commands' },
                        {
                            label: 'Troubleshooting',
                            items: [
                                { slug: 'reference/troubleshooting', label: 'Start here' },
                                { slug: 'reference/troubleshooting/credentials' },
                                { slug: 'reference/troubleshooting/service' },
                                { slug: 'reference/troubleshooting/logs' },
                            ],
                        },
                        { slug: 'reference/backups' },
                        {
                            label: 'Architecture',
                            items: [
                                { slug: 'reference/architecture', label: 'Overview' },
                                { slug: 'reference/architecture/layers' },
                                { slug: 'reference/architecture/lifecycle' },
                                { slug: 'reference/architecture/scaling' },
                                { slug: 'reference/architecture/schema' },
                            ],
                        },
                        {
                            label: 'Decisions',
                            items: [{ autogenerate: { directory: 'reference/adr' } }],
                        },
                    ],
                },
            ],
        }),
    ],
});
