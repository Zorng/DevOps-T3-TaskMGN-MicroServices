package com.taskmanagement.task.tasks;

import com.taskmanagement.task.access.AccessFailure;
import java.time.LocalDate;
import java.util.Set;
import java.util.UUID;
import tools.jackson.databind.JsonNode;

public final class TaskValidation {
  private static final Set<String> FIELDS =
      Set.of("board_id", "title", "description", "assignee_id", "start_date", "due_date");

  private TaskValidation() {}

  private static AccessFailure invalid() {
    return new AccessFailure(422, "VALIDATION_FAILED");
  }

  public static UUID uuid(String value) {
    try {
      UUID id = UUID.fromString(value);
      if (!id.toString().equalsIgnoreCase(value)) throw new IllegalArgumentException();
      return id;
    } catch (RuntimeException e) {
      throw invalid();
    }
  }

  private static UUID id(JsonNode n, boolean nullable) {
    if (n == null || n.isNull()) {
      if (nullable) return null;
      throw invalid();
    }
    if (!n.isString()) throw invalid();
    return uuid(n.asString());
  }

  private static LocalDate date(JsonNode n) {
    if (n == null || n.isNull()) return null;
    if (!n.isString() || !n.asString().matches("[0-9]{4}-[0-9]{2}-[0-9]{2}")) throw invalid();
    try {
      return LocalDate.parse(n.asString());
    } catch (RuntimeException e) {
      throw invalid();
    }
  }

  private static String text(JsonNode n, int max, boolean title) {
    if (n == null || !n.isString()) throw invalid();
    String s = n.asString();
    if (title) {
      int first = 0, last = s.length();
      while (first < last && space(s.codePointAt(first)))
        first += Character.charCount(s.codePointAt(first));
      while (last > first && space(s.codePointBefore(last)))
        last -= Character.charCount(s.codePointBefore(last));
      s = s.substring(first, last);
    }
    int length = s.codePointCount(0, s.length());
    if (length > max || (title && length == 0)) throw invalid();
    return s;
  }

  private static boolean space(int c) {
    return Character.isWhitespace(c) || Character.isSpaceChar(c);
  }

  private static void shape(JsonNode n, boolean create) {
    if (n == null || !n.isObject() || (!create && n.isEmpty())) throw invalid();
    n.propertyNames()
        .forEach(
            k -> {
              if (!FIELDS.contains(k)) throw invalid();
            });
    if (create && (!n.has("board_id") || !n.has("title"))) throw invalid();
  }

  public static TaskInput create(JsonNode n) {
    shape(n, true);
    return build(n, null);
  }

  public static TaskInput merge(JsonNode n, TaskView old) {
    shape(n, false);
    return build(n, old);
  }

  private static TaskInput build(JsonNode n, TaskView old) {
    UUID board = n.has("board_id") ? id(n.get("board_id"), false) : old.board_id();
    String title = n.has("title") ? text(n.get("title"), 200, true) : old.title();
    String description =
        n.has("description")
            ? text(n.get("description"), 5000, false)
            : old == null ? "" : old.description();
    UUID assignee =
        n.has("assignee_id")
            ? id(n.get("assignee_id"), true)
            : old == null || old.assignee() == null ? null : old.assignee().id();
    LocalDate start =
        n.has("start_date") ? date(n.get("start_date")) : old == null ? null : old.start_date();
    LocalDate due =
        n.has("due_date") ? date(n.get("due_date")) : old == null ? null : old.due_date();
    if (start != null && due != null && start.isAfter(due)) throw invalid();
    return new TaskInput(board, title, description, assignee, start, due);
  }
}
