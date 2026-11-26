package com.taskmanagement.task.outbox;

import static org.junit.jupiter.api.Assertions.*;

import com.rabbitmq.client.*;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.wait.strategy.Wait;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;

@SpringBootTest
@Testcontainers
class OutboxPublisherIT {
  @Container static final PostgreSQLContainer DB = new PostgreSQLContainer("postgres:17.6");

  @Container
  static final GenericContainer<?> RABBIT =
      new GenericContainer<>("rabbitmq:4.1-management-alpine")
          .withEnv("RABBITMQ_DEFAULT_USER", "admin")
          .withEnv("RABBITMQ_DEFAULT_PASS", "test-admin-password")
          .withExposedPorts(5672)
          .waitingFor(Wait.forLogMessage(".*Server startup complete.*\\n", 1));

  @DynamicPropertySource
  static void props(DynamicPropertyRegistry r) {
    r.add("spring.datasource.url", DB::getJdbcUrl);
    r.add("spring.datasource.username", DB::getUsername);
    r.add("spring.datasource.password", DB::getPassword);
  }

  @Autowired JdbcTemplate db;
  @Autowired TransactionTemplate tx;
  @Autowired ObjectMapper mapper;
  @Autowired com.taskmanagement.task.board.BoardService boards;
  @Autowired com.taskmanagement.task.tasks.TaskService tasks;
  static Connection admin;
  static Channel control;

  @BeforeAll
  static void provision() throws Exception {
    cmd("add_vhost", "taskmgn");
    cmd("set_permissions", "-p", "taskmgn", "admin", ".*", ".*", ".*");
    cmd("add_user", "task", "test-task-password");
    cmd("set_permissions", "-p", "taskmgn", "task", "^$", "^events$", "^$");
    reconnectAdmin();
    control.exchangeDeclare("events", "topic", true);
    control.queueDeclare("notifications.v1", true, false, false, Map.of("x-queue-type", "quorum"));
    control.queueBind("notifications.v1", "events", "task.assigned.v1");
    control.queueBind("notifications.v1", "events", "task.comment_mentioned.v1");
  }

  static void reconnectAdmin() throws Exception {
    var factory = new ConnectionFactory();
    factory.setHost(RABBIT.getHost());
    factory.setPort(RABBIT.getMappedPort(5672));
    factory.setUsername("admin");
    factory.setPassword("test-admin-password");
    factory.setVirtualHost("taskmgn");
    admin = factory.newConnection();
    control = admin.createChannel();
  }

  static void cmd(String... args) throws Exception {
    var command = new ArrayList<String>();
    command.add("rabbitmqctl");
    command.addAll(List.of(args));
    var result = RABBIT.execInContainer(command.toArray(String[]::new));
    assertEquals(0, result.getExitCode(), result.getStderr());
  }

  @AfterAll
  static void close() throws Exception {
    if (control != null) control.close();
    if (admin != null) admin.close();
  }

  @BeforeEach
  void clear() throws Exception {
    db.update("DELETE FROM task_service.outbox");
    control.queuePurge("notifications.v1");
  }

  RabbitEventTransport rabbit() throws Exception {
    return new RabbitEventTransport(
        RABBIT.getHost(),
        RABBIT.getMappedPort(5672),
        "taskmgn",
        "task",
        "test-task-password",
        false);
  }

  OutboxPublisher publisher(EventTransport transport) {
    return new OutboxPublisher(db, tx, transport, mapper, new SimpleMeterRegistry());
  }

  UUID insert(String type) {
    UUID id = UUID.randomUUID(), request = UUID.randomUUID();
    String body = mapper.writeValueAsString(Map.of("id", id, "type", type, "request_id", request));
    db.update(
        "INSERT INTO task_service.outbox(id,event_type,payload,created_at) VALUES (?,?,CAST(? AS jsonb),?)",
        id,
        type,
        body,
        Timestamp.from(Instant.now()));
    return id;
  }

  void due(UUID id) {
    db.update(
        "UPDATE task_service.outbox SET next_attempt_at=now()-interval '1 second' WHERE id=?", id);
  }

  @Test
  void confirmedRoutableMessagesArePersistentAndCarryIdentity() throws Exception {
    var adapter = rabbit();
    var worker = publisher(adapter);
    for (String type : List.of("task.assigned.v1", "task.comment_mentioned.v1")) {
      UUID id = insert(type);
      String payload =
          db.queryForObject(
              "SELECT payload::text FROM task_service.outbox WHERE id=?", String.class, id);
      assertTrue(worker.publishOne());
      var msg = control.basicGet("notifications.v1", true);
      assertNotNull(msg);
      assertEquals(id.toString(), msg.getProps().getMessageId());
      assertEquals(type, msg.getProps().getType());
      assertEquals(2, msg.getProps().getDeliveryMode());
      assertEquals("application/json", msg.getProps().getContentType());
      assertEquals(
          mapper.readTree(payload).get("request_id").asString(), msg.getProps().getCorrelationId());
      assertEquals(payload, new String(msg.getBody(), StandardCharsets.UTF_8));
      assertNotNull(
          db.queryForObject(
              "SELECT published_at FROM task_service.outbox WHERE id=?", Timestamp.class, id));
    }
  }

  @Test
  void mandatoryReturnAndOutageRemainPendingThenRecover() throws Exception {
    UUID id = insert("task.assigned.v1");
    var worker = publisher(rabbit());
    control.queueUnbind("notifications.v1", "events", "task.assigned.v1");
    try {
      worker.publishOne();
      assertNull(
          db.queryForObject(
              "SELECT published_at FROM task_service.outbox WHERE id=?", Timestamp.class, id));
      assertEquals(
          1,
          db.queryForObject(
              "SELECT attempts FROM task_service.outbox WHERE id=?", Integer.class, id));
    } finally {
      control.queueBind("notifications.v1", "events", "task.assigned.v1");
    }
    due(id);
    var offline =
        publisher(
            new RabbitEventTransport(
                "127.0.0.1", 1, "taskmgn", "task", "test-task-password", false));
    offline.publishOne();
    assertEquals(
        2,
        db.queryForObject(
            "SELECT attempts FROM task_service.outbox WHERE id=?", Integer.class, id));
    due(id);
    worker.publishOne();
    assertNotNull(control.basicGet("notifications.v1", true));
    assertNotNull(
        db.queryForObject(
            "SELECT published_at FROM task_service.outbox WHERE id=?", Timestamp.class, id));
  }

  @Test
  void nackTimeoutRestartAndCrashAfterConfirmRetryIdenticalPayload() throws Exception {
    UUID id = insert("task.assigned.v1");
    String original =
        db.queryForObject(
            "SELECT payload::text FROM task_service.outbox WHERE id=?", String.class, id);
    for (String failure : List.of("nack", "timeout")) {
      var worker =
          publisher(
              event -> {
                throw new java.io.IOException(failure);
              });
      worker.publishOne();
      assertNull(
          db.queryForObject(
              "SELECT published_at FROM task_service.outbox WHERE id=?", Timestamp.class, id));
      due(id);
    }
    db.execute(
        "CREATE FUNCTION task_service.fail_mark_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.published_at IS NOT NULL THEN RAISE EXCEPTION 'crash simulation'; END IF; RETURN NEW; END $$");
    db.execute(
        "CREATE TRIGGER fail_mark_test BEFORE UPDATE ON task_service.outbox FOR EACH ROW EXECUTE FUNCTION task_service.fail_mark_test()");
    try {
      assertThrows(RuntimeException.class, () -> publisher(rabbit()).publishOne());
    } finally {
      db.execute("DROP TRIGGER fail_mark_test ON task_service.outbox");
      db.execute("DROP FUNCTION task_service.fail_mark_test()");
    }
    assertNull(
        db.queryForObject(
            "SELECT published_at FROM task_service.outbox WHERE id=?", Timestamp.class, id));
    publisher(rabbit()).publishOne();
    var sink = new HashSet<String>();
    int effects = 0;
    for (int i = 0; i < 2; i++) {
      var msg = control.basicGet("notifications.v1", true);
      assertNotNull(msg);
      assertEquals(id.toString(), msg.getProps().getMessageId());
      assertEquals(original, new String(msg.getBody(), StandardCharsets.UTF_8));
      if (sink.add(msg.getProps().getMessageId())) effects++;
    }
    assertEquals(1, effects);
  }

  @Test
  void lockedRowsAreNotClaimedByOtherWorkers() throws Exception {
    UUID id = insert("task.assigned.v1");
    var entered = new CountDownLatch(1);
    var release = new CountDownLatch(1);
    var pool = Executors.newFixedThreadPool(2);
    try {
      var first =
          pool.submit(
              () ->
                  publisher(
                          event -> {
                            entered.countDown();
                            assertTrue(release.await(5, TimeUnit.SECONDS));
                          })
                      .publishOne());
      assertTrue(entered.await(5, TimeUnit.SECONDS));
      var second = pool.submit(() -> publisher(event -> fail("duplicate claim")).publishOne());
      assertFalse(second.get(5, TimeUnit.SECONDS));
      release.countDown();
      assertTrue(first.get(5, TimeUnit.SECONDS));
    } finally {
      release.countDown();
      pool.shutdownNow();
    }
    assertNotNull(
        db.queryForObject(
            "SELECT published_at FROM task_service.outbox WHERE id=?", Timestamp.class, id));
  }

  @Test
  void brokerRestartPreservesMessagesAndOutageDoesNotBlockTaskCreate() throws Exception {
    UUID before = insert("task.assigned.v1");
    publisher(rabbit()).publishOne();
    cmd("stop_app");
    try {
      UUID workspace = UUID.randomUUID(),
          actor = UUID.randomUUID(),
          member = UUID.randomUUID(),
          actorGen = UUID.randomUUID(),
          memberGen = UUID.randomUUID();
      var user = new com.taskmanagement.task.access.WorkspaceAccess.User(actor, "Actor");
      var access =
          new com.taskmanagement.task.access.WorkspaceAccess(
              user,
              workspace,
              1,
              actorGen,
              com.taskmanagement.task.access.WorkspaceAccess.Role.owner,
              List.of(
                  new com.taskmanagement.task.access.WorkspaceAccess.Member(
                      actorGen,
                      user,
                      com.taskmanagement.task.access.WorkspaceAccess.Role.owner,
                      Instant.EPOCH),
                  new com.taskmanagement.task.access.WorkspaceAccess.Member(
                      memberGen,
                      new com.taskmanagement.task.access.WorkspaceAccess.User(member, "Member"),
                      com.taskmanagement.task.access.WorkspaceAccess.Role.member,
                      Instant.EPOCH)));
      UUID board = boards.create(workspace, actor, UUID.randomUUID(), "Board").id();
      var task =
          tasks.create(
              access,
              "Bearer test",
              UUID.randomUUID().toString(),
              UUID.randomUUID(),
              new com.taskmanagement.task.tasks.TaskInput(board, "Task", "", member, null, null));
      assertNotNull(task.id());
      assertEquals(
          1,
          db.queryForObject(
              "SELECT count(*) FROM task_service.outbox WHERE published_at IS NULL",
              Integer.class));
      EventSchemaAssertions.valid(
          db.queryForObject(
              "SELECT payload::text FROM task_service.outbox WHERE published_at IS NULL",
              String.class));
    } finally {
      cmd("start_app");
      reconnectAdmin();
    }
    var persisted = control.basicGet("notifications.v1", true);
    assertNotNull(persisted);
    assertEquals(before.toString(), persisted.getProps().getMessageId());
    assertTrue(publisher(rabbit()).publishOne());
    assertNotNull(control.basicGet("notifications.v1", true));
  }

  @Test
  void pendingAgeAndFailuresAreVisibleInMetrics() {
    UUID id = insert("task.assigned.v1");
    db.update(
        "UPDATE task_service.outbox SET created_at=now()-interval '6 minutes' WHERE id=?", id);
    var registry = new SimpleMeterRegistry();
    var worker =
        new OutboxPublisher(
            db,
            tx,
            event -> {
              throw new java.io.IOException();
            },
            mapper,
            registry);
    worker.publishOne();
    worker.refreshMetrics();
    assertEquals(1.0, registry.get("task.outbox.pending").gauge().value());
    assertTrue(registry.get("task.outbox.oldest.seconds").gauge().value() >= 360);
    assertEquals(1.0, registry.get("task.outbox.publish.failures").counter().count());
    assertEquals(
        1,
        db.queryForObject(
            "SELECT attempts FROM task_service.outbox WHERE id=?", Integer.class, id));
  }
}
