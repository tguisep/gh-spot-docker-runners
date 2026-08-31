import { NavLink, Navigate, Route, Routes } from 'react-router-dom';

import { api } from './api';

import { Logs } from './pages/Logs';
import { Overview } from './pages/Overview';
import { Runners } from './pages/Runners';
import { Stats } from './pages/Stats';
import { usePoll } from './usePoll';

export function App() {
    // On every page, not just the overview: several hosts can serve one repository, and a
    // dashboard that does not say which one you have open is a dashboard you can misread in
    // a second tab.
    const health = usePoll(() => api.health(), 30_000);

    return (
        <>
            <header className="top">
                <h1>
                    ghspot
                    {health.data?.host ? (
                        <span className="dim host"> {health.data.host}</span>
                    ) : null}
                </h1>
                <nav>
                    <NavLink to="/" end>
                        overview
                    </NavLink>
                    <NavLink to="/runners">runners</NavLink>
                    <NavLink to="/logs">logs</NavLink>
                    <NavLink to="/stats">stats</NavLink>
                </nav>
                <a className="dim" href="/docs" target="_blank" rel="noreferrer">
                    API
                </a>
            </header>

            <main>
                <Routes>
                    <Route path="/" element={<Overview />} />
                    <Route path="/runners" element={<Runners />} />
                    <Route path="/logs" element={<Logs />} />
                    <Route path="/stats" element={<Stats />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
            </main>

            <footer className="dim">
                Read-mostly. The daemon decides how many runners exist; this shows what it did
                and offers the two interventions the CLI offers.
            </footer>
        </>
    );
}
