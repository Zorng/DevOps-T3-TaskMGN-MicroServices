package com.taskmanagement.task.access;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class HttpWorkspaceAccessClientTest {
    static final String WORKSPACE = "00000000-0000-4000-8000-000000000003";
    static final String ACTOR = "00000000-0000-4000-8000-000000000001";
    static final String MEMBER = "00000000-0000-4000-8000-000000000009";
    static final String SNAPSHOT = """
        {"actor":{"id":"%s","display_name":"Owner"},"workspace_id":"%s","workspace_version":2,
         "actor_membership_id":"%s","actor_role":"owner","members":[{"membership_id":"%s",
         "user":{"id":"%s","display_name":"Owner"},"role":"owner","joined_at":"2026-11-14T10:00:00Z"}]}
        """.formatted(ACTOR, WORKSPACE, MEMBER, MEMBER, ACTOR);
    HttpServer server;
    HttpWorkspaceAccessClient client;
    AtomicInteger status = new AtomicInteger(200), calls = new AtomicInteger(), delay = new AtomicInteger();
    AtomicReference<String> body = new AtomicReference<>(SNAPSHOT), authorization = new AtomicReference<>(), key = new AtomicReference<>(), requestId = new AtomicReference<>(), path = new AtomicReference<>();
    @BeforeEach void start() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            calls.incrementAndGet(); authorization.set(exchange.getRequestHeaders().getFirst("Authorization"));
            key.set(exchange.getRequestHeaders().getFirst("X-Service-Key")); requestId.set(exchange.getRequestHeaders().getFirst("X-Request-ID")); path.set(exchange.getRequestURI().getPath());
            byte[] bytes = body.get().getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status.get(), bytes.length);
            if (delay.get() > 0) {
                exchange.getResponseBody().write(bytes, 0, 1); exchange.getResponseBody().flush();
                try { Thread.sleep(delay.get()); } catch (InterruptedException ex) { Thread.currentThread().interrupt(); }
            }
            try { exchange.getResponseBody().write(bytes, delay.get() > 0 ? 1 : 0, bytes.length - (delay.get() > 0 ? 1 : 0)); }
            catch (java.io.IOException ignored) { } finally { exchange.close(); }
        });
        server.start();
        client = new HttpWorkspaceAccessClient(HttpClient.newHttpClient(), URI.create("http://127.0.0.1:" + server.getAddress().getPort()), "task-secret");
    }
    @AfterEach void stop() { if (server != null) server.stop(0); }
    WorkspaceAccess check() { return client.check(UUID.fromString(WORKSPACE), "Bearer opaque", "00000000-0000-4000-8000-000000000012"); }
    void failure(int expectedStatus, String expectedCode) {
        var ex = assertThrows(AccessFailure.class, this::check); assertEquals(expectedStatus, ex.status()); assertEquals(expectedCode, ex.code());
    }
    @Test void forwardsCredentialsAndCorrelationAndNeverCaches() {
        WorkspaceAccess result = check();
        assertEquals(UUID.fromString(ACTOR), result.actor().id()); assertEquals(UUID.fromString(MEMBER), result.actorMembershipId());
        assertEquals(2, result.workspaceVersion()); assertTrue(result.isOwner()); assertEquals(1, result.members().size());
        assertEquals("Bearer opaque", authorization.get()); assertEquals("task-secret", key.get());
        assertEquals("00000000-0000-4000-8000-000000000012", requestId.get());
        assertEquals("/internal/v1/workspaces/" + WORKSPACE + "/access", path.get());
        status.set(404); failure(404, "NOT_FOUND"); assertEquals(2, calls.get());
    }
    @Test void mapsAuthenticationAndServiceFailuresWithoutRetry() {
        status.set(401); body.set("{\"error\":{\"code\":\"SESSION_INVALID\"}}"); failure(401, "SESSION_INVALID");
        body.set("{\"error\":{\"code\":\"SERVICE_CREDENTIAL_INVALID\"}}"); failure(503, "DEPENDENCY_UNAVAILABLE");
        body.set("garbage"); failure(503, "DEPENDENCY_UNAVAILABLE"); assertEquals(3, calls.get());
        status.set(403); failure(503, "DEPENDENCY_UNAVAILABLE"); assertEquals(4, calls.get());
    }
    @Test void retries503OnceAndMapsOtherErrors() {
        status.set(503); failure(503, "DEPENDENCY_UNAVAILABLE"); assertEquals(2, calls.get());
        status.set(500); failure(503, "DEPENDENCY_UNAVAILABLE"); assertEquals(3, calls.get());
    }
    @Test void rejectsInconsistentAndInvalidSnapshots() {
        for (String invalid : new String[]{
            SNAPSHOT.replace("\"workspace_version\":2", "\"workspace_version\":0"),
            SNAPSHOT.replace("\"actor_role\":\"owner\"", "\"actor_role\":\"member\""),
            SNAPSHOT.replace("\"workspace_id\":\"" + WORKSPACE, "\"workspace_id\":\"" + ACTOR),
            SNAPSHOT.replace("\"actor_membership_id\":\"" + MEMBER, "\"actor_membership_id\":\"" + ACTOR),
            SNAPSHOT.replace("\"display_name\":\"Owner\"", "\"display_name\":\"\""),
            SNAPSHOT.replace("\"workspace_version\":2", "\"workspace_version\":\"2\""),
            SNAPSHOT.replace("\"members\":[{", "\"extra\":1,\"members\":[{"), "not json"}) {
            body.set(invalid); failure(503, "DEPENDENCY_UNAVAILABLE");
        }
    }
    @Test void absentCredentialsFailClosed() {
        client = new HttpWorkspaceAccessClient(HttpClient.newHttpClient(), URI.create("http://127.0.0.1:" + server.getAddress().getPort()), "");
        failure(503, "DEPENDENCY_UNAVAILABLE"); assertEquals(0, calls.get());
    }
    @Test void missingBearerDoesNotContactIdentity() {
        var ex = assertThrows(AccessFailure.class, () -> client.check(UUID.fromString(WORKSPACE), null, "id"));
        assertEquals(401, ex.status()); assertEquals(0, calls.get());
    }
    @Test void stalledResponseBodyIsBounded() {
        delay.set(6000);
        long start = System.nanoTime(); failure(503, "DEPENDENCY_UNAVAILABLE");
        long elapsed = java.time.Duration.ofNanos(System.nanoTime() - start).toMillis();
        assertTrue(elapsed < 5500, "both attempts must time out: " + elapsed);
    }
    @Test void transportFailureIsUnavailable() {
        server.stop(0); failure(503, "DEPENDENCY_UNAVAILABLE");
    }
}
