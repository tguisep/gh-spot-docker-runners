import type { Health } from '../types';

/**
 * What a fresh install sees.
 *
 * The daemon is up and answering — it just has nobody's repository in it yet. Without this
 * the dashboard shows a correct and completely unhelpful picture: zero pools, zero runners,
 * no clue that anything is missing or what to do about it.
 *
 * It says the same things `ghspot setup` says, because there is one right answer and two
 * places somebody might be standing when they need it.
 */
export function Setup({ health }: { health: Health }) {
    return (
        <section className="panel setup">
            <header className="panel-head">
                <h2>not configured yet</h2>
            </header>

            <p className="notice">
                The daemon is running, and{' '}
                {health.setup_reason ?? 'the configuration is incomplete'}.
            </p>

            <ol className="steps">
                <li>
                    <strong>Answer a few questions.</strong> On the host, this writes the
                    configuration for you:
                    <pre>sudo ghspot setup</pre>
                    <span className="dim">
                        It asks which credential you have, which repository to serve, and what
                        the pool should be called. Nothing else has to be decided now.
                    </span>
                </li>
                <li>
                    <strong>Build a runner image.</strong> A pool cannot start anything without
                    one:
                    <pre>sudo ghspot image build ubuntu-24.04</pre>
                </li>
                <li>
                    <strong>Check it.</strong> Every failure here is one that would otherwise
                    show up as a pool quietly never starting a runner:
                    <pre>sudo ghspot doctor</pre>
                </li>
                <li>
                    <strong>Start it.</strong>
                    <pre>sudo systemctl restart ghspot</pre>
                    <span className="dim">This page then becomes the fleet.</span>
                </li>
            </ol>

            <p className="notice dim">
                Setting up a token or a GitHub App, with the exact permissions and why each one
                is needed:{' '}
                <a
                    href="https://github.com/tguisep/gh-spot-docker-runners/blob/main/docs/authentication.md"
                    target="_blank"
                    rel="noreferrer"
                >
                    docs/authentication.md
                </a>
            </p>
        </section>
    );
}
