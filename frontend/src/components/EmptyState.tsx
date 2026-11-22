interface EmptyStateProps {
    title: string;
    message?: string;
    actionLabel?: string;
    onAction?: () => void;
}

export function EmptyState({ title, message, actionLabel, onAction }: EmptyStateProps) {
    return (
        <div>
            <h2>{title}</h2>
            {message && <p>{message}</p>}
            {actionLabel && onAction && (
                <button type="button" onClick={onAction}>
                    {actionLabel}
                </button>
            )}
        </div>
    );
}