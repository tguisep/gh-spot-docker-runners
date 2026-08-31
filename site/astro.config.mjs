// @ts-check
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';

const REPO = 'https://github.com/tguisep/gh-spot-docker-runners';

export default defineConfig({
    // A project page, so everything is served under the repository name. Links between pages
    // are written as relative paths to the .md file and resolved at build time, which is what
    // keeps them right under a base path rather than silently 404ing off the root.
    site: 'https://tguisep.github.io',
    base: '/gh-spot-docker-runners',
    integrations: [
        starlight({
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
                    label: 'Running it',
                    items: [
                        { slug: 'guides/day-to-day' },
                        { slug: 'guides/images' },
                        { slug: 'guides/pm' },
                        { slug: 'guides/capacity' },
                        { slug: 'guides/housekeeping' },
                        { slug: 'guides/gpus' },
                        { slug: 'guides/tuning' },
                        { slug: 'guides/own-ci' },
                    ],
                },
                {
                    label: 'Reference',
                    items: [
                        { slug: 'reference/troubleshooting' },
                        { slug: 'reference/backups' },
                        { slug: 'reference/architecture' },
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
