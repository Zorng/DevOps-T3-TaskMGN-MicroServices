package com.taskmanagement.task.cleanup;

import static org.junit.jupiter.api.Assertions.*;

import com.rabbitmq.client.AMQP;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

class CleanupValidatorTest {
  final CleanupValidator validator = new CleanupValidator();
  final ObjectMapper mapper = new ObjectMapper();
  final UUID id = UUID.randomUUID(),
      workspace = UUID.randomUUID(),
      member = UUID.randomUUID(),
      actor = UUID.randomUUID(),
      request = UUID.randomUUID();

  String event() {
    return mapper.writeValueAsString(
        Map.of(
            "id",
            id,
            "type",
            "workspace.member_removed.v1",
            "schema_version",
            1,
            "producer",
            "identity-workspace",
            "occurred_at",
            "2026-11-14T10:00:00Z",
            "request_id",
            request,
            "aggregate_id",
            member,
            "aggregate_version",
            2,
            "data",
            Map.of(
                "workspace_id",
                workspace,
                "membership_id",
                member,
                "user_id",
                actor,
                "reason",
                "removed",
                "actor",
                Map.of("id", actor, "display_name", "User"))));
  }

  AMQP.BasicProperties props() {
    return new AMQP.BasicProperties.Builder()
        .contentType("application/json")
        .messageId(id.toString())
        .type("workspace.member_removed.v1")
        .correlationId(request.toString())
        .build();
  }

  @Test
  void validateAndBindEnvelopeAndRoute() {
    byte[] body = event().getBytes(StandardCharsets.UTF_8);
    var parsed = validator.validate(body, props(), "events", "workspace.member_removed.v1");
    assertEquals(member, parsed.membership());
    assertEquals(parsed, validator.validate(body, props(), "retry-return", "task-cleanup.v1"));
    assertThrows(
        CleanupValidator.InvalidEvent.class,
        () -> validator.validate(body, props(), "events", "workspace.deleted.v1"));
    assertThrows(
        CleanupValidator.InvalidEvent.class,
        () ->
            validator.validate(
                body,
                props().builder().messageId(UUID.randomUUID().toString()).build(),
                "events",
                "workspace.member_removed.v1"));
    assertThrows(
        CleanupValidator.InvalidEvent.class,
        () ->
            validator.validate(
                event().replace("identity-workspace", "task").getBytes(StandardCharsets.UTF_8),
                props(),
                "events",
                "workspace.member_removed.v1"));
    assertThrows(
        CleanupValidator.InvalidEvent.class,
        () ->
            validator.validate(
                event()
                    .replace("\"aggregate_version\":2", "\"aggregate_version\":3")
                    .getBytes(StandardCharsets.UTF_8),
                props(),
                "events",
                "workspace.member_removed.v1"));
    assertThrows(
        CleanupValidator.InvalidEvent.class,
        () ->
            validator.validate(new byte[65537], props(), "events", "workspace.member_removed.v1"));
  }

  @Test
  void runtimeSchemaMatchesCanonical() throws Exception {
    assertArrayEquals(
        Files.readAllBytes(Path.of("../../api_contracts/events/event.schema.json")),
        Files.readAllBytes(Path.of("src/main/resources/contracts/event.schema.json")));
  }
}
