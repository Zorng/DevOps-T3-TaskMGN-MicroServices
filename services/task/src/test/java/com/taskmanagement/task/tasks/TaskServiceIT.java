package com.taskmanagement.task.tasks;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.taskmanagement.task.access.*;
import com.taskmanagement.task.board.BoardService;
import java.time.*;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;

@SpringBootTest
@Testcontainers
class TaskServiceIT {
  @Container static final PostgreSQLContainer DB = new PostgreSQLContainer("postgres:17.6");

  @DynamicPropertySource
  static void props(DynamicPropertyRegistry r) {
    r.add("spring.datasource.url", DB::getJdbcUrl);
    r.add("spring.datasource.username", DB::getUsername);
    r.add("spring.datasource.password", DB::getPassword);
    r.add("task.cursor-secret", () -> "a-test-secret-with-at-least-32-characters");
  }

  @Autowired TaskService tasks;
  @Autowired BoardService boards;
  @Autowired JdbcTemplate db;
  @Autowired ObjectMapper mapper;
  @MockitoBean WorkspaceAccessClient identity;
  final UUID workspace = UUID.randomUUID(),
      actor = UUID.randomUUID(),
      actorGen = UUID.randomUUID(),
      recipient = UUID.randomUUID(),
      recipientGen = UUID.randomUUID();
  final String requestId = UUID.randomUUID().toString();

  WorkspaceAccess access(long version, boolean member) {
    var user = new WorkspaceAccess.User(actor, "Owner");
    var members = new ArrayList<WorkspaceAccess.Member>();
    members.add(
        new WorkspaceAccess.Member(actorGen, user, WorkspaceAccess.Role.owner, Instant.EPOCH));
    if (member)
      members.add(
          new WorkspaceAccess.Member(
              recipientGen,
              new WorkspaceAccess.User(recipient, "Member"),
              WorkspaceAccess.Role.member,
              Instant.EPOCH));
    return new WorkspaceAccess(
        user, workspace, version, actorGen, WorkspaceAccess.Role.owner, members);
  }

  UUID board() {
    return boards.create(workspace, actor, UUID.randomUUID(), "Board").id();
  }

  TaskInput input(UUID board, UUID assignee) {
    return new TaskInput(board, "Task", "", assignee, null, null);
  }

  TaskView create(WorkspaceAccess a, UUID board, UUID member) {
    return tasks.create(a, "Bearer test", requestId, UUID.randomUUID(), input(board, member));
  }

  @Test
  void creationReplayAssignmentEventAndRollback() throws Exception {
    var a = access(1, true);
    UUID board = board(), key = UUID.randomUUID();
    TaskInput input = input(board, recipient);
    TaskView task = tasks.create(a, "Bearer test", requestId, key, input);
    assertEquals(1, task.number());
    assertEquals(task, tasks.create(a, "Bearer test", requestId, key, input));
    assertEquals(
        "IDEMPOTENCY_CONFLICT",
        assertThrows(
                AccessFailure.class,
                () ->
                    tasks.create(
                        a,
                        "Bearer test",
                        requestId,
                        key,
                        new TaskInput(board, "Different", "", recipient, null, null)))
            .code());
    var event =
        mapper.readTree(
            db.queryForObject(
                "SELECT payload::text FROM task_service.outbox WHERE payload->>'aggregate_id'=?",
                String.class,
                task.id().toString()));
    assertEquals(
        Set.of(
            "id",
            "type",
            "schema_version",
            "producer",
            "occurred_at",
            "request_id",
            "aggregate_id",
            "aggregate_version",
            "data"),
        new HashSet<>(event.propertyNames()));
    assertEquals("task.assigned.v1", event.get("type").asString());
    assertEquals("task", event.get("producer").asString());
    assertEquals(1, event.get("schema_version").asInt());
    assertEquals(1, event.get("aggregate_version").asInt());
    assertEquals(requestId, event.get("request_id").asString());
    assertEquals(task.id().toString(), event.get("aggregate_id").asString());
    com.taskmanagement.task.outbox.EventSchemaAssertions.valid(event.toString());
    var data = event.get("data");
    assertEquals(
        Set.of("workspace_id", "task_id", "actor", "recipient_id", "recipient_membership_id"),
        new HashSet<>(data.propertyNames()));
    assertEquals(recipient.toString(), data.get("recipient_id").asString());
    assertEquals(recipientGen.toString(), data.get("recipient_membership_id").asString());
    assertEquals(actor.toString(), data.get("actor").get("id").asString());
    assertEquals("Owner", data.get("actor").get("display_name").asString());
    UUID.fromString(event.get("id").asString());
    Instant.parse(event.get("occurred_at").asString());
    var expected =
        mapper.readTree(
            mapper.writeValueAsString(
                Map.of(
                    "id",
                    event.get("id").asString(),
                    "type",
                    "task.assigned.v1",
                    "schema_version",
                    1,
                    "producer",
                    "task",
                    "occurred_at",
                    event.get("occurred_at").asString(),
                    "request_id",
                    requestId,
                    "aggregate_id",
                    task.id().toString(),
                    "aggregate_version",
                    1,
                    "data",
                    Map.of(
                        "workspace_id",
                        workspace.toString(),
                        "task_id",
                        task.id().toString(),
                        "actor",
                        Map.of("id", actor.toString(), "display_name", "Owner"),
                        "recipient_id",
                        recipient.toString(),
                        "recipient_membership_id",
                        recipientGen.toString()))));
    assertEquals(expected, event);

    db.execute(
        "CREATE FUNCTION task_service.fail_outbox_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test rollback'; END $$");
    db.execute(
        "CREATE TRIGGER fail_outbox_test BEFORE INSERT ON task_service.outbox FOR EACH ROW EXECUTE FUNCTION task_service.fail_outbox_test()");
    try {
      assertThrows(RuntimeException.class, () -> create(a, board, recipient));
    } finally {
      db.execute("DROP TRIGGER fail_outbox_test ON task_service.outbox");
      db.execute("DROP FUNCTION task_service.fail_outbox_test()");
    }
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.task WHERE workspace_id=?",
            Integer.class,
            workspace));
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.task_create_key WHERE path=?",
            Integer.class,
            "/api/v1/workspaces/" + workspace + "/tasks"));
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.outbox WHERE payload->>'aggregate_id'=?",
            Integer.class,
            task.id().toString()));
    assertEquals(2, create(a, board, null).number());
    boards.delete(workspace, board, 1);
    assertEquals(
        404,
        assertThrows(
                AccessFailure.class, () -> tasks.create(a, "Bearer test", requestId, key, input))
            .status());
  }

  @Test
  void mutationReconciliationAndVisibility() {
    var a = access(1, true);
    UUID source = board(), dest = board();
    TaskView first = create(a, source, recipient);
    TaskView moved =
        tasks.patch(
            a,
            "Bearer test",
            requestId,
            first.id(),
            1,
            mapper.readTree("{\"board_id\":\"" + dest + "\"}"));
    assertEquals(first.id(), moved.id());
    assertEquals(first.number(), moved.number());
    assertEquals(2, moved.version());
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.outbox WHERE payload->>'aggregate_id'=?",
            Integer.class,
            first.id().toString()));
    assertEquals(
        moved,
        tasks.patch(
            a,
            "Bearer test",
            requestId,
            first.id(),
            2,
            mapper.readTree("{\"board_id\":\"" + dest + "\"}")));
    assertEquals(
        412,
        assertThrows(
                AccessFailure.class,
                () ->
                    tasks.patch(
                        a,
                        "Bearer test",
                        requestId,
                        first.id(),
                        1,
                        mapper.readTree("{\"title\":\"stale\"}")))
            .status());
    TaskView cleared = tasks.get(access(2, false), "Bearer test", requestId, first.id());
    assertNull(cleared.assignee());
    assertEquals(3, cleared.version());
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.outbox WHERE payload->>'aggregate_id'=?",
            Integer.class,
            first.id().toString()));
    tasks.delete(access(2, false), "Bearer test", requestId, first.id(), 3);
    assertEquals(
        404,
        assertThrows(AccessFailure.class, () -> tasks.get(a, "Bearer test", requestId, first.id()))
            .status());
  }

  @Test
  void refreshHappensOutsideTransactionAndProtectsNewerAssignment() {
    var newer = access(5, true);
    UUID board = board();
    TaskView task = create(newer, board, recipient);
    when(identity.check(any(), any(), any()))
        .thenAnswer(
            inv -> {
              assertFalse(TransactionSynchronizationManager.isActualTransactionActive());
              return newer;
            });
    TaskView result = tasks.get(access(2, false), "Bearer test", requestId, task.id());
    assertEquals(recipient, result.assignee().id());
    assertEquals(1, result.version());
    verify(identity).check(eq(workspace), eq("Bearer test"), eq(requestId));
    when(identity.check(any(), any(), any())).thenReturn(access(2, false));
    assertEquals(
        503,
        assertThrows(
                AccessFailure.class,
                () -> tasks.get(access(2, false), "Bearer test", requestId, task.id()))
            .status());
  }

  @Test
  void concurrentNumbersIdempotencyAndCursorScope() throws Exception {
    var a = access(1, true);
    UUID board = board(), key = UUID.randomUUID();
    var pool = Executors.newFixedThreadPool(4);
    var start = new CountDownLatch(1);
    try {
      Callable<TaskView> same =
          () -> {
            start.await();
            return tasks.create(a, "Bearer test", requestId, key, input(board, null));
          };
      var one = pool.submit(same);
      var two = pool.submit(same);
      var three =
          pool.submit(
              () -> {
                start.await();
                return create(a, board, null);
              });
      var four =
          pool.submit(
              () -> {
                start.await();
                return create(a, board, null);
              });
      start.countDown();
      assertEquals(one.get(20, TimeUnit.SECONDS), two.get(20, TimeUnit.SECONDS));
      assertEquals(
          Set.of(1L, 2L, 3L),
          Set.of(one.get().number(), three.get().number(), four.get().number()));
    } finally {
      pool.shutdownNow();
    }
    var page = tasks.list(a, "Bearer test", requestId, null, 1, null);
    assertEquals(1, page.items().size());
    assertNotNull(page.next_cursor());
    var second = tasks.list(a, "Bearer test", requestId, null, 2, page.next_cursor());
    assertEquals(2, second.items().size());
    assertNull(second.next_cursor());
    assertEquals(
        "INVALID_CURSOR",
        assertThrows(
                AccessFailure.class,
                () -> tasks.list(a, "Bearer test", requestId, board, 1, page.next_cursor()))
            .code());
    assertEquals(
        "INVALID_CURSOR",
        assertThrows(
                AccessFailure.class,
                () -> tasks.list(a, "Bearer test", requestId, null, 1, page.next_cursor() + "x"))
            .code());
  }

  @Test
  void reassignmentTombstonesAndBoardDeletionRace() throws Exception {
    var a = access(1, true);
    UUID source = board(), dest = board();
    TaskView task = create(a, source, null);
    TaskView assigned =
        tasks.patch(
            a,
            "Bearer test",
            requestId,
            task.id(),
            1,
            mapper.readTree("{\"assignee_id\":\"" + recipient + "\"}"));
    assertEquals(2, assigned.version());
    TaskView unassigned =
        tasks.patch(
            a, "Bearer test", requestId, task.id(), 2, mapper.readTree("{\"assignee_id\":null}"));
    assertEquals(3, unassigned.version());
    TaskView self =
        tasks.patch(
            a,
            "Bearer test",
            requestId,
            task.id(),
            3,
            mapper.readTree("{\"assignee_id\":\"" + actor + "\"}"));
    assertEquals(4, self.version());
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.outbox WHERE payload->>'aggregate_id'=?",
            Integer.class,
            task.id().toString()));
    db.update(
        "INSERT INTO task_service.membership_tombstone(workspace_id,membership_id) VALUES (?,?)",
        workspace,
        recipientGen);
    assertEquals(
        422,
        assertThrows(
                AccessFailure.class,
                () ->
                    tasks.patch(
                        a,
                        "Bearer test",
                        requestId,
                        task.id(),
                        4,
                        mapper.readTree("{\"assignee_id\":\"" + recipient + "\"}")))
            .status());
    var pool = Executors.newFixedThreadPool(2);
    var start = new CountDownLatch(1);
    try {
      var move =
          pool.submit(
              () -> {
                start.await();
                try {
                  tasks.patch(
                      a,
                      "Bearer test",
                      requestId,
                      task.id(),
                      4,
                      mapper.readTree("{\"board_id\":\"" + dest + "\"}"));
                  return 200;
                } catch (AccessFailure e) {
                  return e.status();
                }
              });
      var deletion =
          pool.submit(
              () -> {
                start.await();
                boards.delete(workspace, dest, 1);
                return 204;
              });
      start.countDown();
      int status = move.get(20, TimeUnit.SECONDS);
      assertTrue(status == 200 || status == 422);
      assertEquals(204, deletion.get(20, TimeUnit.SECONDS));
      if (status == 200)
        assertEquals(
            404,
            assertThrows(
                    AccessFailure.class, () -> tasks.get(a, "Bearer test", requestId, task.id()))
                .status());
      else assertEquals(source, tasks.get(a, "Bearer test", requestId, task.id()).board_id());
    } finally {
      pool.shutdownNow();
    }
    db.update("INSERT INTO task_service.workspace_tombstone(workspace_id) VALUES (?)", workspace);
    assertEquals(
        404,
        assertThrows(
                AccessFailure.class, () -> tasks.list(a, "Bearer test", requestId, null, 20, null))
            .status());
  }

  @Test
  void cursorRejectsDifferentActorAndWorkspace() {
    var a = access(1, true);
    UUID board = board();
    create(a, board, null);
    create(a, board, null);
    String cursor = tasks.list(a, "Bearer test", requestId, null, 1, null).next_cursor();
    var other =
        new WorkspaceAccess(
            new WorkspaceAccess.User(recipient, "Member"),
            workspace,
            1,
            recipientGen,
            WorkspaceAccess.Role.member,
            a.members());
    assertEquals(
        "INVALID_CURSOR",
        assertThrows(
                AccessFailure.class,
                () -> tasks.list(other, "Bearer test", requestId, null, 1, cursor))
            .code());
    var otherWorkspace =
        new WorkspaceAccess(a.actor(), UUID.randomUUID(), 1, actorGen, a.actorRole(), a.members());
    assertEquals(
        "INVALID_CURSOR",
        assertThrows(
                AccessFailure.class,
                () -> tasks.list(otherWorkspace, "Bearer test", requestId, null, 1, cursor))
            .code());
  }
}
