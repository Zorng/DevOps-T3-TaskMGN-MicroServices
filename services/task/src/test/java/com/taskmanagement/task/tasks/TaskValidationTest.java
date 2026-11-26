package com.taskmanagement.task.tasks;

import static org.junit.jupiter.api.Assertions.*;

import com.taskmanagement.task.access.AccessFailure;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

class TaskValidationTest {
  ObjectMapper mapper = new ObjectMapper();
  UUID board = UUID.randomUUID();
  TaskView old =
      new TaskView(
          UUID.randomUUID(),
          UUID.randomUUID(),
          board,
          1,
          "Old",
          "",
          null,
          null,
          null,
          new TaskView.User(UUID.randomUUID(), "User"),
          1,
          Instant.EPOCH,
          Instant.EPOCH);

  @Test
  void createDefaultsAndUnicodeLengths() {
    TaskInput value =
        TaskValidation.create(
            mapper.readTree("{\"board_id\":\"" + board + "\",\"title\":\"  😀  \"}"));
    assertEquals("😀", value.title());
    assertEquals("", value.description());
    assertNull(value.assignee());
    assertEquals(
        422,
        assertThrows(
                AccessFailure.class,
                () ->
                    TaskValidation.create(
                        mapper.readTree(
                            "{\"board_id\":\""
                                + board
                                + "\",\"title\":\""
                                + "😀".repeat(201)
                                + "\"}")))
            .status());
  }

  @Test
  void patchRejectsUnknownNullAndInvalidResultingDates() {
    for (String json :
        new String[] {
          "{}",
          "{\"status\":\"x\"}",
          "{\"description\":null}",
          "{\"title\":42}",
          "{\"start_date\":\"2026-02-30\"}",
          "{\"start_date\":\"2026-02-03\",\"due_date\":\"2026-02-02\"}"
        })
      assertEquals(
          422,
          assertThrows(AccessFailure.class, () -> TaskValidation.merge(mapper.readTree(json), old))
              .status());
    assertEquals(
        "Old", TaskValidation.merge(mapper.readTree("{\"description\":\"\"}"), old).title());
  }
}
