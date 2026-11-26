package com.taskmanagement.task.cleanup;

import java.util.UUID;

public record CleanupEvent(UUID id, String type, UUID workspace, UUID membership, UUID requestId) {}
