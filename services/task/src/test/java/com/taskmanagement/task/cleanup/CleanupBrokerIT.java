package com.taskmanagement.task.cleanup;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.rabbitmq.client.*;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.wait.strategy.Wait;
import org.testcontainers.junit.jupiter.*;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;

@SpringBootTest
@Testcontainers
class CleanupBrokerIT {
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

  @Autowired CleanupService service;
  @Autowired CleanupValidator validator;
  @Autowired JdbcTemplate db;
  @Autowired ObjectMapper mapper;
  static Connection admin, task;
  static Channel control, consumer;

  @BeforeAll
  static void setup() throws Exception {
    cmd("add_vhost", "taskmgn");
    cmd("set_permissions", "-p", "taskmgn", "admin", ".*", ".*", ".*");
    cmd("add_user", "task", "test-task-password");
    cmd(
        "set_permissions",
        "-p",
        "taskmgn",
        "task",
        "^$",
        "^(retry|dead-letter)$",
        "^task-cleanup\\.v1$");
    admin = connect("admin", "test-admin-password");
    control = admin.createChannel();
    task = connect("task", "test-task-password");
    consumer = task.createChannel();
    control.exchangeDeclare("events", "topic", true);
    control.exchangeDeclare("retry", "direct", true);
    control.exchangeDeclare("retry-return", "direct", true);
    control.exchangeDeclare("dead-letter", "direct", true);
    control.queueDeclare("task-cleanup.v1", true, false, false, Map.of("x-queue-type", "quorum"));
    control.queueDeclare(
        "task-cleanup.v1.dlq", true, false, false, Map.of("x-queue-type", "quorum"));
    control.queueBind("task-cleanup.v1", "events", "workspace.member_removed.v1");
    control.queueBind("task-cleanup.v1", "events", "workspace.deleted.v1");
    control.queueBind("task-cleanup.v1", "retry-return", "task-cleanup.v1");
    control.queueBind("task-cleanup.v1.dlq", "dead-letter", "task-cleanup.v1");
    String[] delays = {"5s", "30s", "2m", "10m", "30m"};
    int[] millis = {5000, 30000, 120000, 600000, 1800000};
    for (int i = 0; i < delays.length; i++) {
      String q = "task-cleanup.v1.retry." + delays[i];
      control.queueDeclare(
          q,
          true,
          false,
          false,
          Map.of(
              "x-message-ttl",
              millis[i],
              "x-dead-letter-exchange",
              "retry-return",
              "x-dead-letter-routing-key",
              "task-cleanup.v1"));
      control.queueBind(q, "retry", q);
    }
    control.confirmSelect();
  }

  static void cmd(String... args) throws Exception {
    var command = new ArrayList<String>();
    command.add("rabbitmqctl");
    command.addAll(List.of(args));
    var r = RABBIT.execInContainer(command.toArray(String[]::new));
    assertEquals(0, r.getExitCode(), r.getStderr());
  }

  static Connection connect(String name, String password) throws Exception {
    var f = new ConnectionFactory();
    f.setHost(RABBIT.getHost());
    f.setPort(RABBIT.getMappedPort(5672));
    f.setVirtualHost("taskmgn");
    f.setUsername(name);
    f.setPassword(password);
    return f.newConnection();
  }

  @AfterAll
  static void close() throws Exception {
    if (consumer != null && consumer.isOpen()) consumer.close();
    if (task != null && task.isOpen()) task.close();
    if (control != null && control.isOpen()) control.close();
    if (admin != null && admin.isOpen()) admin.close();
  }

  @BeforeEach
  void clear() throws Exception {
    control.queuePurge("task-cleanup.v1");
    control.queuePurge("task-cleanup.v1.dlq");
    for (String d : List.of("5s", "30s", "2m", "10m", "30m"))
      control.queuePurge("task-cleanup.v1.retry." + d);
  }

  CleanupConsumer worker(CleanupService s) throws Exception {
    return new CleanupConsumer(
        RABBIT.getHost(),
        RABBIT.getMappedPort(5672),
        "taskmgn",
        "task",
        "test-task-password",
        false,
        s,
        validator,
        new SimpleMeterRegistry());
  }

  record Payload(UUID id, UUID workspace, UUID request, String body, AMQP.BasicProperties props) {}

  Payload payload() {
    UUID id = UUID.randomUUID(),
        workspace = UUID.randomUUID(),
        request = UUID.randomUUID(),
        actor = UUID.randomUUID();
    String type = "workspace.deleted.v1";
    String body =
        mapper.writeValueAsString(
            Map.of(
                "id",
                id,
                "type",
                type,
                "producer",
                "identity-workspace",
                "schema_version",
                1,
                "occurred_at",
                Instant.now().toString(),
                "request_id",
                request,
                "aggregate_id",
                workspace,
                "aggregate_version",
                2,
                "data",
                Map.of(
                    "workspace_id",
                    workspace,
                    "actor",
                    Map.of("id", actor, "display_name", "User"))));
    var props =
        new AMQP.BasicProperties.Builder()
            .deliveryMode(2)
            .contentType("application/json")
            .messageId(id.toString())
            .correlationId(request.toString())
            .type(type)
            .build();
    return new Payload(id, workspace, request, body, props);
  }

  void publish(Payload p, AMQP.BasicProperties props) throws Exception {
    control.basicPublish(
        "events", "workspace.deleted.v1", true, props, p.body().getBytes(StandardCharsets.UTF_8));
    assertTrue(control.waitForConfirms(2000));
  }

  @Test
  void committedThenRedeliveredIsDeduplicatedAndAcked() throws Exception {
    Payload p = payload();
    publish(p, p.props());
    assertTrue(worker(service).processOne(consumer));
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.workspace_tombstone WHERE workspace_id=?",
            Integer.class,
            p.workspace()));
    publish(p, p.props());
    assertTrue(worker(service).processOne(consumer));
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.processed_event WHERE event_id=?",
            Integer.class,
            p.id()));
    assertNull(control.basicGet("task-cleanup.v1", true));
  }

  @Test
  void retryReturnAndDeadLetterReplayPreserveEnvelope() throws Exception {
    Payload p = payload();
    publish(p, p.props());
    CleanupService failing = mock(CleanupService.class);
    when(failing.apply(any())).thenThrow(new IllegalStateException());
    assertTrue(worker(failing).processOne(consumer));
    Thread.sleep(5500);
    assertTrue(worker(service).processOne(consumer));
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.processed_event WHERE event_id=?",
            Integer.class,
            p.id()));
    var sixth = p.props().builder().headers(Map.of("x-attempt", 6)).build();
    Payload other = payload();
    publish(
        other,
        sixth
            .builder()
            .messageId(other.id().toString())
            .correlationId(other.request().toString())
            .build());
    assertTrue(worker(failing).processOne(consumer));
    GetResponse dlq = control.basicGet("task-cleanup.v1.dlq", false);
    assertNotNull(dlq);
    assertEquals(other.id().toString(), dlq.getProps().getMessageId());
    assertEquals(other.body(), new String(dlq.getBody(), StandardCharsets.UTF_8));
    control.basicPublish("retry-return", "task-cleanup.v1", true, dlq.getProps(), dlq.getBody());
    assertTrue(control.waitForConfirms(2000));
    control.basicAck(dlq.getEnvelope().getDeliveryTag(), false);
    assertTrue(worker(service).processOne(consumer));
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.processed_event WHERE event_id=?",
            Integer.class,
            other.id()));
  }

  @Test
  void malformedDeadLettersAndLaterValidMessageSucceeds() throws Exception {
    Payload p = payload();
    publish(p, p.props().builder().headers(Map.of("x-attempt", "bad")).build());
    assertTrue(worker(service).processOne(consumer));
    assertNotNull(control.basicGet("task-cleanup.v1.dlq", true));
    publish(p, p.props());
    assertTrue(worker(service).processOne(consumer));
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.processed_event WHERE event_id=?",
            Integer.class,
            p.id()));
  }

  @Test
  void unconfirmedRetryLeavesOriginalForRedelivery() throws Exception {
    Payload p = payload();
    publish(p, p.props());
    CleanupService failing = mock(CleanupService.class);
    when(failing.apply(any())).thenThrow(new IllegalStateException());
    control.queueUnbind("task-cleanup.v1.retry.5s", "retry", "task-cleanup.v1.retry.5s");
    try {
      assertThrows(Exception.class, () -> worker(failing).processOne(consumer));
      consumer.close();
      consumer = task.createChannel();
    } finally {
      control.queueBind("task-cleanup.v1.retry.5s", "retry", "task-cleanup.v1.retry.5s");
    }
    assertTrue(worker(service).processOne(consumer));
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.processed_event WHERE event_id=?",
            Integer.class,
            p.id()));
  }

  @Test
  void crashAfterCommitBeforeAckRedeliversWithoutDuplicateEffect() throws Exception {
    Payload p = payload();
    publish(p, p.props());
    Channel proxy = mock(Channel.class, org.mockito.AdditionalAnswers.delegatesTo(consumer));
    doAnswer(
            invocation -> {
              consumer.abort();
              throw new java.io.IOException("simulated disconnect");
            })
        .when(proxy)
        .basicAck(anyLong(), anyBoolean());
    assertThrows(Exception.class, () -> worker(service).processOne(proxy));
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.processed_event WHERE event_id=?",
            Integer.class,
            p.id()));
    consumer = task.createChannel();
    assertTrue(worker(service).processOne(consumer));
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.workspace_tombstone WHERE workspace_id=?",
            Integer.class,
            p.workspace()));
    assertNull(control.basicGet("task-cleanup.v1", true));
  }

  @Test
  void brokerInterruptionRedeliversUnacknowledgedMessage() throws Exception {
    Payload p = payload();
    publish(p, p.props());
    assertNotNull(consumer.basicGet("task-cleanup.v1", false));
    cmd("stop_app");
    try {
      cmd("start_app");
    } finally {
      admin = connect("admin", "test-admin-password");
      control = admin.createChannel();
      control.confirmSelect();
      task = connect("task", "test-task-password");
      consumer = task.createChannel();
    }
    assertTrue(worker(service).processOne(consumer));
    assertEquals(
        1,
        db.queryForObject(
            "SELECT count(*) FROM task_service.processed_event WHERE event_id=?",
            Integer.class,
            p.id()));
  }
}
