package com.taskmanagement.task.comments;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
public record CommentView(UUID id, UUID workspace_id, UUID task_id, String body, User author, List<Mention> mentions, Instant created_at) {
    public record User(UUID id, String display_name) {}
    public record Mention(User user, int start, int length) {}
}
