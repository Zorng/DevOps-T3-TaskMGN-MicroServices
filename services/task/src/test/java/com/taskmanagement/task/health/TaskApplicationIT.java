package com.taskmanagement.task.health;

import static org.junit.jupiter.api.Assertions.*;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Testcontainers
class TaskApplicationIT {
  @Container static final PostgreSQLContainer DATABASE = new PostgreSQLContainer("postgres:17.6");
  private static final AtomicInteger IDENTITY_STATUS = new AtomicInteger(200);
  private static final HttpServer IDENTITY = startIdentity();

  @Value("${local.server.port}")
  private int port;

  @Autowired private JdbcTemplate jdbc;
  @Autowired private Flyway flyway;

  @DynamicPropertySource
  static void properties(DynamicPropertyRegistry registry) {
    registry.add("spring.datasource.url", DATABASE::getJdbcUrl);
    registry.add("spring.datasource.username", DATABASE::getUsername);
    registry.add("spring.datasource.password", DATABASE::getPassword);
    registry.add(
        "task.identity-base-url", () -> "http://127.0.0.1:" + IDENTITY.getAddress().getPort());
  }

  @Test
  void realDatabaseMigrationsAndDependencyFailuresFollowHealthContract() throws Exception {
    assertEquals(
        "task_service",
        jdbc.queryForObject(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name='task_service'",
            String.class));
    assertEquals(
        1,
        jdbc.queryForObject(
            "SELECT count(*) FROM task_service.flyway_schema_history WHERE version='1' AND success",
            Integer.class));
    assertEquals(0, flyway.migrate().migrationsExecuted);
    assertHealth("live", 200, "ok");
    assertHealth("ready", 200, "ok");
    IDENTITY_STATUS.set(503);
    assertHealth("ready", 503, "unavailable");
    assertHealth("live", 200, "ok");
    IDENTITY_STATUS.set(200);
    assertHealth("ready", 200, "ok");
    DATABASE.stop();
    assertHealth("ready", 503, "unavailable");
    assertHealth("live", 200, "ok");
  }

  private void assertHealth(String endpoint, int expected, String state) throws Exception {
    try (HttpClient client = HttpClient.newHttpClient()) {
      var response =
          client.send(
              HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + "/health/" + endpoint))
                  .timeout(Duration.ofSeconds(5))
                  .GET()
                  .build(),
              HttpResponse.BodyHandlers.ofString());
      assertEquals(expected, response.statusCode());
      assertEquals("{\"status\":\"" + state + "\"}", response.body());
      assertEquals("no-store", response.headers().firstValue("Cache-Control").orElseThrow());
    }
  }

  private static HttpServer startIdentity() {
    try {
      HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
      server.createContext(
          "/health/ready",
          exchange -> {
            exchange.sendResponseHeaders(IDENTITY_STATUS.get(), -1);
            exchange.close();
          });
      server.start();
      return server;
    } catch (IOException ex) {
      throw new IllegalStateException("Cannot start isolated Identity test double", ex);
    }
  }

  @AfterAll
  static void stopIdentity() {
    IDENTITY.stop(0);
  }
}
