package com.taskmanagement.task.comments;
import java.util.List;
import java.util.UUID;
public record CommentInput(String body, List<Mention> mentions) {
    public CommentInput { mentions=List.copyOf(mentions); }
    public record Mention(UUID user_id, int start, int length) {}
}
