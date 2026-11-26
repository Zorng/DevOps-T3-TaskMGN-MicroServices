package com.taskmanagement.task.cleanup;

import static org.junit.jupiter.api.Assertions.*;

import com.taskmanagement.task.access.*;
import com.taskmanagement.task.board.*;
import com.taskmanagement.task.comments.*;
import com.taskmanagement.task.tasks.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;

@SpringBootTest
@Testcontainers
class CleanupServiceIT {
  @Container static final PostgreSQLContainer DB = new PostgreSQLContainer("postgres:17.6");

  @DynamicPropertySource
  static void props(DynamicPropertyRegistry r) {
    r.add("spring.datasource.url", DB::getJdbcUrl);
    r.add("spring.datasource.username", DB::getUsername);
    r.add("spring.datasource.password", DB::getPassword);
    r.add("task.cursor-secret", () -> "a-test-secret-with-at-least-32-characters");
  }

  @Autowired org.springframework.transaction.support.TransactionTemplate tx;
  @Autowired CleanupService cleanup;
  @Autowired BoardService boards;
  @Autowired TaskService tasks;
  @Autowired CommentService comments;
  @Autowired JdbcTemplate db;
  @Autowired ObjectMapper mapper;
  final UUID workspace = UUID.randomUUID(),
      actor = UUID.randomUUID(),
      actorGen = UUID.randomUUID(),
      member = UUID.randomUUID(),
      oldGen = UUID.randomUUID(),
      newGen = UUID.randomUUID(),
      request = UUID.randomUUID();

  WorkspaceAccess access(UUID generation) {
    var owner = new WorkspaceAccess.User(actor, "Owner");
    return new WorkspaceAccess(
        owner,
        workspace,
        3,
        actorGen,
        WorkspaceAccess.Role.owner,
        List.of(
            new WorkspaceAccess.Member(actorGen, owner, WorkspaceAccess.Role.owner, Instant.EPOCH),
            new WorkspaceAccess.Member(
                generation,
                new WorkspaceAccess.User(member, "Member"),
                WorkspaceAccess.Role.member,
                Instant.EPOCH)));
  }

  CleanupEvent removed(UUID gen) {
    return new CleanupEvent(
        UUID.randomUUID(), "workspace.member_removed.v1", workspace, gen, request);
  }

  CleanupEvent deleted() {
    return new CleanupEvent(UUID.randomUUID(), "workspace.deleted.v1", workspace, null, request);
  }

  @Test
  void duplicateRemovalPreservesHistoricalCommentsAndRejoinedAssignment() {
    var old = access(oldGen);
    UUID board = boards.createAuthorized(old, UUID.randomUUID(), "Board").id();
    var task =
        tasks.create(
            old,
            "Bearer token",
            request.toString(),
            UUID.randomUUID(),
            new TaskInput(board, "Task", "", member, null, null));
    var comment =
        comments.create(
            old,
            task.id(),
            UUID.randomUUID(),
            new CommentInput("@Member", List.of(new CommentInput.Mention(member, 0, 7))),
            request.toString());
    var event = removed(oldGen);
    assertTrue(cleanup.apply(event));
    assertFalse(cleanup.apply(event));
    var cleared = tasks.get(access(newGen), "Bearer token", request.toString(), task.id());
    assertNull(cleared.assignee());
    assertEquals(2, cleared.version());
    var reassigned =
        tasks.patch(
            access(newGen),
            "Bearer token",
            request.toString(),
            task.id(),
            2,
            mapper.readTree("{\"assignee_id\":\"" + member + "\"}"));
    assertEquals(3, reassigned.version());
    assertTrue(cleanup.apply(removed(oldGen)));
    assertEquals(
        reassigned, tasks.get(access(newGen), "Bearer token", request.toString(), task.id()));
    assertEquals(comment, comments.list(access(newGen), task.id(), 20, null).items().getFirst());
    assertEquals(
        404,
        assertThrows(
                AccessFailure.class,
                () ->
                    boards.createAuthorized(
                        new WorkspaceAccess(
                            old.actor(), workspace, 3, oldGen, old.actorRole(), old.members()),
                        UUID.randomUUID(),
                        "stale"))
            .status());
    assertEquals(
        422,
        assertThrows(
                AccessFailure.class,
                () ->
                    comments.create(
                        old,
                        task.id(),
                        UUID.randomUUID(),
                        new CommentInput(
                            "@Member", List.of(new CommentInput.Mention(member, 0, 7))),
                        request.toString()))
            .status());
  }

  @Test
  void deletionTombstoneBlocksAllStaleBoardAndChildOperations() {
    var a = access(oldGen);
    var board = boards.createAuthorized(a, UUID.randomUUID(), "Board");
    var task =
        tasks.create(
            a,
            "Bearer",
            request.toString(),
            UUID.randomUUID(),
            new TaskInput(board.id(), "Task", "", null, null, null));
    var event = deleted();
    assertTrue(cleanup.apply(event));
    assertFalse(cleanup.apply(event));
    assertEquals(404, assertThrows(AccessFailure.class, () -> boards.listAuthorized(a)).status());
    assertEquals(
        404, assertThrows(AccessFailure.class, () -> boards.getAuthorized(a, board.id())).status());
    assertEquals(
        404,
        assertThrows(
                AccessFailure.class, () -> boards.renameAuthorized(a, board.id(), 1, "changed"))
            .status());
    assertEquals(
        404,
        assertThrows(AccessFailure.class, () -> boards.deleteAuthorized(a, board.id(), 1))
            .status());
    assertEquals(
        404,
        assertThrows(
                AccessFailure.class, () -> boards.createAuthorized(a, UUID.randomUUID(), "new"))
            .status());
    assertEquals(
        404,
        assertThrows(
                AccessFailure.class, () -> tasks.get(a, "Bearer", request.toString(), task.id()))
            .status());
    assertEquals(
        404,
        assertThrows(AccessFailure.class, () -> comments.list(a, task.id(), 20, null)).status());
  }

  @Test
  void rollbackBeforeCommitDoesNotLeaveMarkerOrTombstone() {
    CleanupEvent event = removed(oldGen);
    db.execute(
        "CREATE FUNCTION task_service.fail_cleanup_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'rollback test'; END $$");
    db.execute(
        "CREATE TRIGGER fail_cleanup_test BEFORE INSERT ON task_service.membership_tombstone FOR EACH ROW EXECUTE FUNCTION task_service.fail_cleanup_test()");
    try {
      assertThrows(RuntimeException.class, () -> cleanup.apply(event));
    } finally {
      db.execute("DROP TRIGGER fail_cleanup_test ON task_service.membership_tombstone");
      db.execute("DROP FUNCTION task_service.fail_cleanup_test()");
    }
    assertEquals(
        0,
        db.queryForObject(
            "SELECT count(*) FROM task_service.processed_event WHERE event_id=?",
            Integer.class,
            event.id()));
    assertTrue(cleanup.apply(event));
  }

  @Test
  void concurrentRemovalAndReassignmentCannotClearNewGeneration() throws Exception {
    var old = access(oldGen);
    UUID board = boards.createAuthorized(old, UUID.randomUUID(), "Board").id();
    var task =
        tasks.create(
            old,
            "Bearer",
            request.toString(),
            UUID.randomUUID(),
            new TaskInput(board, "Task", "", member, null, null));
    var start = new CountDownLatch(1);
    var pool = Executors.newFixedThreadPool(2);
    try {
      var remove =
          pool.submit(
              () -> {
                start.await();
                return cleanup.apply(removed(oldGen));
              });
      var replace =
          pool.submit(
              () -> {
                start.await();
                try {
                  return tasks.patch(
                      access(newGen),
                      "Bearer",
                      request.toString(),
                      task.id(),
                      1,
                      mapper.readTree("{\"assignee_id\":\"" + member + "\"}"));
                } catch (AccessFailure e) {
                  assertEquals(412, e.status());
                  return null;
                }
              });
      start.countDown();
      assertTrue(remove.get(20, TimeUnit.SECONDS));
      var result = replace.get(20, TimeUnit.SECONDS);
      if (result == null) {
        var current = tasks.get(access(newGen), "Bearer", request.toString(), task.id());
        result =
            tasks.patch(
                access(newGen),
                "Bearer",
                request.toString(),
                task.id(),
                current.version(),
                mapper.readTree("{\"assignee_id\":\"" + member + "\"}"));
      }
      assertEquals(member, result.assignee().id());
      assertTrue(cleanup.apply(removed(oldGen)));
      assertEquals(result, tasks.get(access(newGen), "Bearer", request.toString(), task.id()));
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  void staleBoardCreateWaitingBehindDeletionCannotResurrectWorkspace() throws Exception {
    var access = access(oldGen);
    var started = new CountDownLatch(1);
    var release = new CountDownLatch(1);
    var pool = Executors.newFixedThreadPool(2);
    try {
      var deletion =
          pool.submit(
              () ->
                  tx.execute(
                      status -> {
                        boards.lockWorkspace(workspace);
                        started.countDown();
                        try {
                          if (!release.await(5, TimeUnit.SECONDS))
                            throw new IllegalStateException();
                        } catch (InterruptedException e) {
                          Thread.currentThread().interrupt();
                          throw new IllegalStateException(e);
                        }
                        return cleanup.apply(deleted());
                      }));
      assertTrue(started.await(5, TimeUnit.SECONDS));
      var stale =
          pool.submit(
              () ->
                  assertThrows(
                      AccessFailure.class,
                      () -> boards.createAuthorized(access, UUID.randomUUID(), "stale")));
      release.countDown();
      assertTrue(deletion.get(10, TimeUnit.SECONDS));
      assertEquals(404, stale.get(10, TimeUnit.SECONDS).status());
      assertEquals(
          0,
          db.queryForObject(
              "SELECT count(*) FROM task_service.board WHERE workspace_id=?",
              Integer.class,
              workspace));
    } finally {
      release.countDown();
      pool.shutdownNow();
    }
  }
}
