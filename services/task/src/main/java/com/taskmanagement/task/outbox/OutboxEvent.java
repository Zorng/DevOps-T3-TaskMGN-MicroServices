package com.taskmanagement.task.outbox;
import java.util.UUID;
public record OutboxEvent(UUID id,String type,String payload,String requestId,int attempts) {}
