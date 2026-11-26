package com.taskmanagement.task.board;

import static org.junit.jupiter.api.Assertions.*;

import com.taskmanagement.task.access.AccessFailure;
import java.util.UUID;
import java.util.concurrent.*;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;

@SpringBootTest
@Testcontainers
class BoardServiceIT {
  @Container static final PostgreSQLContainer DB = new PostgreSQLContainer("postgres:17.6");

  @DynamicPropertySource
  static void properties(DynamicPropertyRegistry r) {
    r.add("spring.datasource.url", DB::getJdbcUrl);
    r.add("spring.datasource.username", DB::getUsername);
    r.add("spring.datasource.password", DB::getPassword);
  }

  @Autowired BoardService service;
  @Autowired org.springframework.jdbc.core.JdbcTemplate jdbc;

  @Test
  void quotaConcurrencyReplayAndVersioning() throws Exception {
    UUID workspace = UUID.randomUUID(), actor = UUID.randomUUID();
    for (int i = 0; i < 4; i++) service.create(workspace, actor, UUID.randomUUID(), "Board" + i);
    var executor = Executors.newFixedThreadPool(2);
    var start = new CountDownLatch(1);
    try {
      Callable<Integer> create =
          () -> {
            start.await();
            try {
              service.create(workspace, actor, UUID.randomUUID(), "Race");
              return 201;
            } catch (AccessFailure ex) {
              assertEquals("BOARD_LIMIT_REACHED", ex.code());
              return ex.status();
            }
          };
      var a = executor.submit(create);
      var b = executor.submit(create);
      start.countDown();
      assertEquals(610, a.get(20, TimeUnit.SECONDS) + b.get(20, TimeUnit.SECONDS));
      assertEquals(5, service.list(workspace).size());
    } finally {
      executor.shutdownNow();
    }
    var removed = service.list(workspace).getFirst();
    assertEquals(
        "VERSION_CONFLICT",
        assertThrows(AccessFailure.class, () -> service.delete(workspace, removed.id(), 2)).code());
    service.delete(workspace, removed.id(), 1);
    assertEquals(
        404,
        assertThrows(AccessFailure.class, () -> service.visible(workspace, removed.id())).status());
    UUID key = UUID.randomUUID();
    Board first = service.create(workspace, actor, key, " New ");
    assertEquals(first, service.create(workspace, actor, key, " New "));
    assertEquals(
        "IDEMPOTENCY_CONFLICT",
        assertThrows(AccessFailure.class, () -> service.create(workspace, actor, key, "Different"))
            .code());
    assertEquals(first, service.rename(workspace, first.id(), 1, " New "));
    Board changed = service.rename(workspace, first.id(), 1, "Renamed");
    assertEquals(2, changed.version());
    assertEquals(first, service.create(workspace, actor, key, " New "));
    assertEquals(
        412,
        assertThrows(AccessFailure.class, () -> service.rename(workspace, first.id(), 1, "stale"))
            .status());
    UUID wrongWorkspace = UUID.randomUUID();
    assertEquals(
        404,
        assertThrows(AccessFailure.class, () -> service.visible(wrongWorkspace, first.id()))
            .status());
    assertEquals(
        404,
        assertThrows(
                AccessFailure.class, () -> service.rename(wrongWorkspace, first.id(), 2, "Wrong"))
            .status());
    assertEquals(
        404,
        assertThrows(AccessFailure.class, () -> service.delete(wrongWorkspace, first.id(), 2))
            .status());
    assertEquals(changed, service.visible(workspace, first.id()));
    service.delete(workspace, first.id(), 2);
    assertEquals(
        404,
        assertThrows(AccessFailure.class, () -> service.create(workspace, actor, key, " New "))
            .status());
  }

  @Test
  void sameKeyConcurrentCreatesOneBoard() throws Exception {
    UUID workspace = UUID.randomUUID(), actor = UUID.randomUUID(), key = UUID.randomUUID();
    var executor = Executors.newFixedThreadPool(2);
    var start = new CountDownLatch(1);
    try {
      Callable<Board> create =
          () -> {
            start.await();
            return service.create(workspace, actor, key, "Same");
          };
      var a = executor.submit(create);
      var b = executor.submit(create);
      start.countDown();
      assertEquals(a.get(20, TimeUnit.SECONDS), b.get(20, TimeUnit.SECONDS));
      assertEquals(1, service.list(workspace).size());
    } finally {
      executor.shutdownNow();
    }
  }

  @Test
  void keysAreScopedAndCanExpire() {
    UUID workspace = UUID.randomUUID(),
        otherWorkspace = UUID.randomUUID(),
        actor = UUID.randomUUID(),
        otherActor = UUID.randomUUID(),
        key = UUID.randomUUID();
    Board first = service.create(workspace, actor, key, "One");
    assertNotEquals(first.id(), service.create(workspace, otherActor, key, "Two").id());
    assertNotEquals(first.id(), service.create(otherWorkspace, actor, key, "Three").id());
    jdbc.update(
        "UPDATE task_service.board_create_key SET expires_at=now()-interval '1 second' WHERE actor_id=? AND key=? AND path=?",
        actor,
        key,
        "/api/v1/workspaces/" + workspace + "/boards");
    assertNotEquals(first.id(), service.create(workspace, actor, key, "One").id());
    assertEquals(3, service.list(workspace).size());
  }
}
