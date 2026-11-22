import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import { App } from './App';
import { readConfig } from './config';

const container = document.getElementById('root');
if (!container) {
    throw new Error('Root element #root not found');
}

createRoot(container).render(
    <StrictMode>
        <BrowserRouter>
            <App config={readConfig()} />
        </BrowserRouter>
    </StrictMode>,
);