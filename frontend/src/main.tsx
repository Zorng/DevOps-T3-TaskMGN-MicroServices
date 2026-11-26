import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import { App } from './App';
import { AuthProvider, createAuthServices } from './auth/AuthProvider';
import { readConfig } from './config';

const container = document.getElementById('root');
if (!container) {
    throw new Error('Root element #root not found');
}

const services = createAuthServices(readConfig());

createRoot(container).render(
    <StrictMode>
        <BrowserRouter>
            <AuthProvider services={services}>
                <App />
            </AuthProvider>
        </BrowserRouter>
    </StrictMode>,
);