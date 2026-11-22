package com.taskmanagement.task.tasks;
import java.time.Instant;
import java.time.LocalDate;
import java.util.UUID;
public record TaskView(UUID id, UUID workspace_id, UUID board_id, long number, String title, String description,
                       User assignee, LocalDate start_date, LocalDate due_date, User creator, long version,
                       Instant created_at, Instant updated_at) {
    public record User(UUID id, String display_name) {}
}
