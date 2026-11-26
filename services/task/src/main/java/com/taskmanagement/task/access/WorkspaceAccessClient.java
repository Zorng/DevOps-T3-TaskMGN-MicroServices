package com.taskmanagement.task.access;

import java.util.UUID;

public interface WorkspaceAccessClient {
  WorkspaceAccess check(UUID workspaceId, String authorization, String requestId);
}
