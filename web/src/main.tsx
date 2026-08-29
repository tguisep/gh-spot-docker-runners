import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
import './styles.css';

const root = document.getElementById('root');
if (!root) throw new Error('index.html has no #root to mount into');

createRoot(root).render(
    <StrictMode>
        {/* Served under /ui, so the router has to agree or every link 404s on refresh. */}
        <BrowserRouter basename="/ui">
            <App />
        </BrowserRouter>
    </StrictMode>,
);
