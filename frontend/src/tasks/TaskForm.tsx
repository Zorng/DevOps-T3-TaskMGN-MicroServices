import { useState, type FormEvent, type ReactNode } from 'react';
import type { Board } from '../boards/boardsApi';
import type { Member } from '../workspaces/workspacesApi';
import {
    checkTaskForm,
    TASK_DESCRIPTION_MAX,
    TASK_TITLE_MAX,
    type TaskField,
    type TaskFieldErrors,
    type TaskFormValues,
    type TaskInput,
    type UserSummary,
} from './tasksApi';

interface TaskFormProps {
    /** Prefix for element ids, so two forms on a page do not clash. */
    idPrefix: string;
    /** Accessible name of the form. */
    label: string;
    initial: TaskFormValues;
    /** Boards to choose a destination from. null hides the field (editing never moves a task). */
    boards: Board[] | null;
    members: Member[];
    /** The task's present assignee, so one who is no longer a member is shown rather than silently cleared. */
    currentAssignee?: UserSummary | null;
    submitLabel: string;
    busy: boolean;
    /** Reasons the server gave for individual fields. */
    serverErrors: TaskFieldErrors;
    /** Anything to show next to the button (a failure, or a success note). */
    children?: ReactNode;
    onSubmit: (input: TaskInput) => void;
}

export function TaskForm({
    idPrefix,
    label,
    initial,
    boards,
    members,
    currentAssignee,
    submitLabel,
    busy,
    serverErrors,
    children,
    onSubmit,
}: TaskFormProps) {
    const [values, setValues] = useState<TaskFormValues>(initial);
    const [clientErrors, setClientErrors] = useState<TaskFieldErrors>({});

    const set = (field: keyof TaskFormValues, value: string) => setValues((current) => ({ ...current, [field]: value }));
    const errors: TaskFieldErrors = { ...serverErrors, ...clientErrors };
    const id = (name: string) => `${idPrefix}-${name}`;
    const errorProps = (field: TaskField) => ({
        'aria-invalid': errors[field] !== undefined,
        'aria-describedby': errors[field] ? `${id(field)}-error` : undefined,
    });
    const fieldError = (field: TaskField) =>
        errors[field] ? (
            <p id={`${id(field)}-error`} role="alert">
                {errors[field]}
            </p>
        ) : null;

    function handleSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (busy) return;
        const check = checkTaskForm(values);
        if (!check.ok) {
            setClientErrors(check.errors);
            return;
        }
        setClientErrors({});
        onSubmit(check.input);
    }

    const assigneeIsMember = !currentAssignee || members.some((member) => member.user.id === currentAssignee.id);

    return (
        <form onSubmit={handleSubmit} noValidate aria-label={label}>
            <label htmlFor={id('title')}>Title</label>
            <input
                id={id('title')}
                value={values.title}
                autoComplete="off"
                onChange={(event) => set('title', event.target.value)}
                {...errorProps('title')}
            />
            <p>{`Required, up to ${TASK_TITLE_MAX} characters.`}</p>
            {fieldError('title')}

            {boards && (
                <>
                    <label htmlFor={id('board')}>Board</label>
                    <select
                        id={id('board')}
                        value={values.boardId}
                        onChange={(event) => set('boardId', event.target.value)}
                        {...errorProps('board_id')}
                    >
                        {boards.map((board) => (
                            <option key={board.id} value={board.id}>
                                {board.name}
                            </option>
                        ))}
                    </select>
                    {fieldError('board_id')}
                </>
            )}

            <label htmlFor={id('description')}>Description</label>
            <textarea
                id={id('description')}
                value={values.description}
                onChange={(event) => set('description', event.target.value)}
                {...errorProps('description')}
            />
            <p>{`Optional, plain text, up to ${TASK_DESCRIPTION_MAX} characters.`}</p>
            {fieldError('description')}

            <label htmlFor={id('assignee')}>Assignee</label>
            <select
                id={id('assignee')}
                value={values.assigneeId}
                onChange={(event) => set('assigneeId', event.target.value)}
                {...errorProps('assignee_id')}
            >
                <option value="">Unassigned</option>
                {!assigneeIsMember && currentAssignee && (
                    <option value={currentAssignee.id}>{`${currentAssignee.display_name} (no longer a member)`}</option>
                )}
                {members.map((member) => (
                    <option key={member.user.id} value={member.user.id}>
                        {member.user.display_name}
                    </option>
                ))}
            </select>
            {fieldError('assignee_id')}

            <label htmlFor={id('start')}>Start date</label>
            <input
                id={id('start')}
                type="date"
                value={values.startDate}
                onChange={(event) => set('startDate', event.target.value)}
                {...errorProps('start_date')}
            />
            {fieldError('start_date')}

            <label htmlFor={id('due')}>Due date</label>
            <input
                id={id('due')}
                type="date"
                value={values.dueDate}
                onChange={(event) => set('dueDate', event.target.value)}
                {...errorProps('due_date')}
            />
            {fieldError('due_date')}

            <button type="submit" disabled={busy}>
                {submitLabel}
            </button>
            {children}
        </form>
    );
}
