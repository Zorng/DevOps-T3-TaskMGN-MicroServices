package com.taskmanagement.task.access;

import java.io.IOException;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

@Component
public class HttpWorkspaceAccessClient implements WorkspaceAccessClient {
    private static final Logger LOG = LoggerFactory.getLogger(HttpWorkspaceAccessClient.class);
    private static final Duration TIMEOUT = Duration.ofSeconds(2);
    private static final int MAX_BODY = 65536;
    private final HttpClient client;
    private final URI base;
    private final String key;
    private final ObjectMapper mapper = new ObjectMapper();

    @Autowired
    public HttpWorkspaceAccessClient(@Value("${task.identity-base-url}") URI base,
                                     @Value("${task.identity-service-key:}") String key) {
        this(HttpClient.newBuilder().connectTimeout(TIMEOUT).followRedirects(HttpClient.Redirect.NEVER).build(), base, key);
    }
    HttpWorkspaceAccessClient(HttpClient client, URI base, String key) {
        if (base.getHost() == null || !("http".equals(base.getScheme()) || "https".equals(base.getScheme()))
                || base.getUserInfo() != null || base.getQuery() != null || base.getFragment() != null
                || !(base.getPath().isEmpty() || "/".equals(base.getPath()))) {
            throw new IllegalArgumentException("Identity base URL must be an HTTP(S) origin");
        }
        this.client = client; this.base = base; this.key = key;
    }
    @Override
    public WorkspaceAccess check(UUID workspaceId, String authorization, String requestId) {
        if (key == null || key.isBlank()) {
            LOG.error("Identity service credential is not configured");
            throw AccessFailure.unavailable();
        }
        if (authorization == null || !authorization.matches("(?i)Bearer [^\\s]+")) {
            throw new AccessFailure(401, "AUTH_REQUIRED");
        }
        HttpRequest request;
        try {
            request = HttpRequest.newBuilder(base.resolve("/internal/v1/workspaces/" + workspaceId + "/access"))
                    .timeout(TIMEOUT).header("X-Service-Key", key).header("Authorization", authorization)
                    .header("X-Request-ID", requestId).POST(HttpRequest.BodyPublishers.noBody()).build();
        } catch (IllegalArgumentException ex) { throw AccessFailure.unavailable(); }
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                var pending = client.sendAsync(request, info -> new BoundedBody());
                HttpResponse<byte[]> response;
                try { response = pending.get(TIMEOUT.toMillis(), TimeUnit.MILLISECONDS); }
                catch (TimeoutException ex) { pending.cancel(true); throw new IOException("Identity timed out"); }
                catch (ExecutionException ex) { throw new IOException("Identity transport failed"); }
                byte[] body = response.body();
                int status = response.statusCode();
                if (status == 503 && attempt == 0) continue;
                if (body.length > MAX_BODY) throw AccessFailure.unavailable();
                if (status == 200) return parseSnapshot(body, workspaceId);
                if (status == 401) {
                    String code = errorCode(body);
                    if ("SERVICE_CREDENTIAL_INVALID".equals(code)) {
                        LOG.error("Identity rejected the Task service credential");
                        throw AccessFailure.unavailable();
                    }
                    if ("AUTH_REQUIRED".equals(code) || "SESSION_INVALID".equals(code)) throw new AccessFailure(401, code);
                    throw AccessFailure.unavailable();
                }
                if (status == 404) throw new AccessFailure(404, "NOT_FOUND");
                throw AccessFailure.unavailable();
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt(); throw AccessFailure.unavailable();
            } catch (IOException ex) {
                if (attempt == 1) throw AccessFailure.unavailable();
            }
        }
        throw AccessFailure.unavailable();
    }
    private String errorCode(byte[] body) {
        try { return mapper.readTree(body).path("error").path("code").asString(); }
        catch (RuntimeException ex) { return ""; }
    }
    private WorkspaceAccess parseSnapshot(byte[] body, UUID requested) {
        try {
            JsonNode n = mapper.readTree(body);
            fields(n, "actor", "workspace_id", "workspace_version", "actor_membership_id", "actor_role", "members");
            UUID workspace = uuid(n.get("workspace_id"));
            if (!workspace.equals(requested)) throw new IllegalArgumentException();
            JsonNode version = n.get("workspace_version");
            if (!version.isIntegralNumber() || !version.canConvertToLong()) throw new IllegalArgumentException();
            long v = version.longValue();
            if (v < 1 || v > 9007199254740991L) throw new IllegalArgumentException();
            var actor = user(n.get("actor"));
            UUID actorMembership = uuid(n.get("actor_membership_id"));
            var role = role(n.get("actor_role"));
            JsonNode nodes = n.get("members");
            if (!nodes.isArray() || nodes.size() < 1 || nodes.size() > 20) throw new IllegalArgumentException();
            var members = new ArrayList<WorkspaceAccess.Member>();
            var users = new HashSet<UUID>(); var generations = new HashSet<UUID>();
            boolean actorFound = false; int owners = 0;
            for (JsonNode m : nodes) {
                fields(m, "membership_id", "user", "role", "joined_at");
                UUID generation = uuid(m.get("membership_id")); var member = user(m.get("user"));
                var memberRole = role(m.get("role"));
                if (memberRole == WorkspaceAccess.Role.owner) owners++;
                String joined = string(m.get("joined_at"));
                if (!joined.endsWith("Z")) throw new IllegalArgumentException();
                Instant instant = Instant.parse(joined);
                if (!users.add(member.id()) || !generations.add(generation)) throw new IllegalArgumentException();
                if (member.id().equals(actor.id())) {
                    if (!member.equals(actor) || !generation.equals(actorMembership) || memberRole != role) throw new IllegalArgumentException();
                    actorFound = true;
                }
                members.add(new WorkspaceAccess.Member(generation, member, memberRole, instant));
            }
            if (!actorFound || owners != 1) throw new IllegalArgumentException();
            return new WorkspaceAccess(actor, workspace, v, actorMembership, role, members);
        } catch (RuntimeException ex) { throw AccessFailure.unavailable(); }
    }
    private void fields(JsonNode node, String... names) {
        if (node == null || !node.isObject() || node.size() != names.length) throw new IllegalArgumentException();
        for (String name : names) if (!node.hasNonNull(name)) throw new IllegalArgumentException();
    }
    private String string(JsonNode node) {
        if (node == null || !node.isString()) throw new IllegalArgumentException();
        return node.asString();
    }
    private UUID uuid(JsonNode node) {
        String s = string(node); UUID id = UUID.fromString(s);
        if (!id.toString().equalsIgnoreCase(s)) throw new IllegalArgumentException();
        return id;
    }
    private WorkspaceAccess.Role role(JsonNode node) { return WorkspaceAccess.Role.valueOf(string(node)); }
    private WorkspaceAccess.User user(JsonNode node) {
        fields(node, "id", "display_name"); String name = string(node.get("display_name"));
        if (name.isBlank() || name.codePointCount(0, name.length()) > 200) throw new IllegalArgumentException();
        return new WorkspaceAccess.User(uuid(node.get("id")), name);
    }
    private static final class BoundedBody implements HttpResponse.BodySubscriber<byte[]> {
        private final CompletableFuture<byte[]> result = new CompletableFuture<>();
        private final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        private Flow.Subscription subscription;
        @Override public CompletionStage<byte[]> getBody() { return result; }
        @Override public void onSubscribe(Flow.Subscription subscription) { this.subscription = subscription; subscription.request(1); }
        @Override public void onNext(List<ByteBuffer> buffers) {
            for (ByteBuffer buffer : buffers) {
                if (buffer.remaining() > MAX_BODY - bytes.size()) {
                    subscription.cancel(); result.completeExceptionally(new IOException("Identity response too large")); return;
                }
                byte[] part = new byte[buffer.remaining()]; buffer.get(part); bytes.writeBytes(part);
            }
            subscription.request(1);
        }
        @Override public void onError(Throwable error) { result.completeExceptionally(error); }
        @Override public void onComplete() { result.complete(bytes.toByteArray()); }
    }

}
