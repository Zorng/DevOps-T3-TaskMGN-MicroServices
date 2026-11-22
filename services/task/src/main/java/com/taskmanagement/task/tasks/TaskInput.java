package com.taskmanagement.task.tasks;
import java.time.LocalDate;
import java.util.UUID;
public record TaskInput(UUID board, String title, String description, UUID assignee, LocalDate start, LocalDate due) {}
