import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { EmptyState } from './EmptyState';
import { ErrorState } from './ErrorState';
import { LoadingState } from './LoadingState';

describe('LoadingState', () => {
    it('announces the default label as a status', () => {
        render(<LoadingState />);
        expect(screen.getByRole('status')).toHaveTextContent('Loading…');
    });

    it('accepts a custom label', () => {
        render(<LoadingState label="Loading boards…" />);
        expect(screen.getByRole('status')).toHaveTextContent('Loading boards…');
    });
});

describe('EmptyState', () => {
    it('shows the title and message without an action by default', () => {
        render(<EmptyState title="No workspaces yet" message="Create one to get started." />);
        expect(screen.getByRole('heading', { name: 'No workspaces yet' })).toBeInTheDocument();
        expect(screen.getByText('Create one to get started.')).toBeInTheDocument();
        expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('calls the action when the button is clicked', async () => {
        const onAction = vi.fn();
        const user = userEvent.setup();
        render(<EmptyState title="No tasks" actionLabel="Create task" onAction={onAction} />);
        await user.click(screen.getByRole('button', { name: 'Create task' }));
        expect(onAction).toHaveBeenCalledTimes(1);
    });

    it('can be operated with the keyboard', async () => {
        const onAction = vi.fn();
        const user = userEvent.setup();
        render(<EmptyState title="No tasks" actionLabel="Create task" onAction={onAction} />);
        await user.tab();
        expect(screen.getByRole('button', { name: 'Create task' })).toHaveFocus();
        await user.keyboard('{Enter}');
        expect(onAction).toHaveBeenCalledTimes(1);
    });
});

describe('ErrorState', () => {
    it('announces the message as an alert', () => {
        render(<ErrorState message="The service is unavailable." />);
        expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong');
        expect(screen.getByRole('alert')).toHaveTextContent('The service is unavailable.');
    });

    it('shows no retry button without a handler', () => {
        render(<ErrorState message="Not found." />);
        expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('retries from the keyboard', async () => {
        const onRetry = vi.fn();
        const user = userEvent.setup();
        render(<ErrorState message="Try later." onRetry={onRetry} />);
        await user.tab();
        expect(screen.getByRole('button', { name: 'Try again' })).toHaveFocus();
        await user.keyboard('{Enter}');
        expect(onRetry).toHaveBeenCalledTimes(1);
    });
});