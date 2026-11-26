package com.taskmanagement.task.board;

import java.time.Instant;
import java.util.UUID;

public record Board(
    UUID id,
    UUID workspace_id,
    String name,
    long version,
    Instant created_at,
    Instant updated_at) {}
