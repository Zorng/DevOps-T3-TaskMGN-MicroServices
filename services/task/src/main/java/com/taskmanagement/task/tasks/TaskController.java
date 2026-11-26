package com.taskmanagement.task.tasks;

import com.taskmanagement.task.access.*;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.util.*;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.databind.*;
import tools.jackson.databind.json.JsonMapper;

@RestController
@RequestMapping("/api/v1/workspaces/{workspace_id}/tasks")
public class TaskController {
  private final TaskService service;
  private final ObjectMapper mapper =
      JsonMapper.builder()
          .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
          .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
          .build();

  public TaskController(TaskService service) {
    this.service = service;
  }

  private void request(HttpServletRequest r, byte[] body, boolean withBody, boolean list) {
    if (body.length > 65536) throw new AccessFailure(413, "PAYLOAD_TOO_LARGE");
    if (!withBody && body.length > 0) throw new AccessFailure(400, "MALFORMED_REQUEST");
    if (withBody
        && body.length > 0
        && (r.getContentType() == null
            || !r.getContentType().split(";", 2)[0].trim().equalsIgnoreCase("application/json")))
      throw new AccessFailure(415, "UNSUPPORTED_MEDIA_TYPE");
    for (var e : r.getParameterMap().entrySet())
      if (!list
          || !Set.of("limit", "cursor", "board_id").contains(e.getKey())
          || e.getValue().length != 1) throw new AccessFailure(400, "MALFORMED_REQUEST");
  }

  private byte[] bytes(byte[] b) {
    return b == null ? new byte[0] : b;
  }

  private JsonNode json(byte[] body) {
    try {
      return mapper.readTree(body);
    } catch (RuntimeException ex) {
      throw new AccessFailure(400, "MALFORMED_REQUEST");
    }
  }

  private UUID pathId(String s) {
    try {
      return TaskValidation.uuid(s);
    } catch (AccessFailure e) {
      throw new AccessFailure(400, "MALFORMED_REQUEST");
    }
  }

  private long version(String s) {
    if (s == null) throw new AccessFailure(428, "PRECONDITION_REQUIRED");
    if (!s.matches("\"[1-9][0-9]*\"")) throw new AccessFailure(400, "MALFORMED_REQUEST");
    try {
      return Long.parseLong(s.substring(1, s.length() - 1));
    } catch (NumberFormatException e) {
      throw new AccessFailure(400, "MALFORMED_REQUEST");
    }
  }

  private ResponseEntity<TaskView> response(int status, TaskView task) {
    return ResponseEntity.status(status).eTag("\"" + task.version() + "\"").body(task);
  }

  private String requestId(HttpServletResponse response) {
    return response.getHeader("X-Request-ID");
  }

  @GetMapping
  @RequireWorkspaceAccess
  public TaskService.Page list(
      WorkspaceAccess a,
      HttpServletRequest r,
      HttpServletResponse response,
      @RequestBody(required = false) byte[] data) {
    request(r, bytes(data), false, true);
    int limit = 20;
    String supplied = r.getParameter("limit");
    if (supplied != null) {
      try {
        if (!supplied.matches("[0-9]+")) throw new IllegalArgumentException();
        limit = Integer.parseInt(supplied);
        if (limit < 1 || limit > 100) throw new IllegalArgumentException();
      } catch (RuntimeException e) {
        throw new AccessFailure(400, "MALFORMED_REQUEST");
      }
    }
    UUID board = r.getParameter("board_id") == null ? null : pathId(r.getParameter("board_id"));
    return service.list(
        a,
        r.getHeader("Authorization"),
        requestId(response),
        board,
        limit,
        r.getParameter("cursor"));
  }

  @PostMapping
  @RequireWorkspaceAccess
  public ResponseEntity<TaskView> create(
      WorkspaceAccess a,
      HttpServletRequest r,
      HttpServletResponse response,
      @RequestBody(required = false) byte[] data,
      @RequestHeader(value = "Idempotency-Key", required = false) String key) {
    byte[] body = bytes(data);
    request(r, body, true, false);
    if (key == null) throw new AccessFailure(400, "IDEMPOTENCY_KEY_REQUIRED");
    UUID id = pathId(key);
    if (id.version() != 4 || id.variant() != 2) throw new AccessFailure(400, "MALFORMED_REQUEST");
    return response(
        201,
        service.create(
            a,
            r.getHeader("Authorization"),
            requestId(response),
            id,
            TaskValidation.create(json(body))));
  }

  @GetMapping("/{task_id}")
  @RequireWorkspaceAccess
  public ResponseEntity<TaskView> get(
      WorkspaceAccess a,
      @PathVariable String task_id,
      HttpServletRequest r,
      HttpServletResponse response,
      @RequestBody(required = false) byte[] data) {
    request(r, bytes(data), false, false);
    return response(
        200, service.get(a, r.getHeader("Authorization"), requestId(response), pathId(task_id)));
  }

  @PatchMapping("/{task_id}")
  @RequireWorkspaceAccess
  public ResponseEntity<TaskView> patch(
      WorkspaceAccess a,
      @PathVariable String task_id,
      HttpServletRequest r,
      HttpServletResponse response,
      @RequestBody(required = false) byte[] data,
      @RequestHeader(value = "If-Match", required = false) String match) {
    byte[] body = bytes(data);
    request(r, body, true, false);
    return response(
        200,
        service.patch(
            a,
            r.getHeader("Authorization"),
            requestId(response),
            pathId(task_id),
            version(match),
            json(body)));
  }

  @DeleteMapping("/{task_id}")
  @RequireWorkspaceAccess
  public ResponseEntity<Void> delete(
      WorkspaceAccess a,
      @PathVariable String task_id,
      HttpServletRequest r,
      HttpServletResponse response,
      @RequestBody(required = false) byte[] data,
      @RequestHeader(value = "If-Match", required = false) String match) {
    request(r, bytes(data), false, false);
    service.delete(
        a, r.getHeader("Authorization"), requestId(response), pathId(task_id), version(match));
    return ResponseEntity.noContent().build();
  }
}
