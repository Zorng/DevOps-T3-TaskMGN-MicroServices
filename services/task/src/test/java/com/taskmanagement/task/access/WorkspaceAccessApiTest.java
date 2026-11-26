package com.taskmanagement.task.access;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.taskmanagement.task.health.RequestCorrelationFilter;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

class WorkspaceAccessApiTest {
  private final WorkspaceAccessClient client = mock(WorkspaceAccessClient.class);
  private final AtomicInteger mutations = new AtomicInteger();
  private MockMvc mvc;
  private final UUID workspace = UUID.fromString(HttpWorkspaceAccessClientTest.WORKSPACE);

  private WorkspaceAccess snapshot(WorkspaceAccess.Role role) {
    var user =
        new WorkspaceAccess.User(UUID.fromString(HttpWorkspaceAccessClientTest.ACTOR), "Verified");
    var generation = UUID.fromString(HttpWorkspaceAccessClientTest.MEMBER);
    return new WorkspaceAccess(
        user,
        workspace,
        2,
        generation,
        role,
        List.of(
            new WorkspaceAccess.Member(
                generation, user, role, Instant.parse("2026-11-14T10:00:00Z"))));
  }

  @RestController
  class TestController {
    @RequireWorkspaceAccess
    @PostMapping("/test/{workspace_id}/member")
    String member(WorkspaceAccess access) {
      mutations.incrementAndGet();
      return access.actor().id().toString();
    }

    @RequireWorkspaceAccess(owner = true)
    @PostMapping("/test/{workspace_id}/owner")
    String owner(WorkspaceAccess access) {
      mutations.incrementAndGet();
      return access.actor().id().toString();
    }
  }

  @BeforeEach
  void setup() {
    var config = new WorkspaceAccessWebConfiguration(client);
    mvc =
        MockMvcBuilders.standaloneSetup(new TestController())
            .addInterceptors(config.interceptor())
            .setCustomArgumentResolvers(config.resolver())
            .setControllerAdvice(new AccessFailureHandler())
            .addFilters(new RequestCorrelationFilter())
            .build();
  }

  @Test
  void memberAndOwnerUseVerifiedSnapshotIgnoringSpoofedHeaders() throws Exception {
    when(client.check(eq(workspace), eq("Bearer token"), anyString()))
        .thenReturn(snapshot(WorkspaceAccess.Role.member), snapshot(WorkspaceAccess.Role.owner));
    String id = UUID.randomUUID().toString();
    mvc.perform(
            post("/test/{workspace_id}/member", workspace)
                .header("Authorization", "Bearer token")
                .header("X-Request-ID", id)
                .header("X-User-ID", UUID.randomUUID())
                .header("X-User-Role", "owner")
                .header("X-Service-Key", "spoof"))
        .andExpect(status().isOk())
        .andExpect(content().string(HttpWorkspaceAccessClientTest.ACTOR))
        .andExpect(header().string("X-Request-ID", id));
    mvc.perform(
            post("/test/{workspace_id}/owner", workspace).header("Authorization", "Bearer token"))
        .andExpect(status().isOk());
    verify(client).check(workspace, "Bearer token", id);
    org.junit.jupiter.api.Assertions.assertEquals(2, mutations.get());
  }

  @Test
  void ownerDenialAndIdentityFailuresPreventMutation() throws Exception {
    when(client.check(eq(workspace), any(), anyString()))
        .thenReturn(snapshot(WorkspaceAccess.Role.member))
        .thenThrow(
            new AccessFailure(401, "SESSION_INVALID"),
            new AccessFailure(401, "AUTH_REQUIRED"),
            new AccessFailure(404, "NOT_FOUND"),
            AccessFailure.unavailable(),
            AccessFailure.unavailable());
    mvc.perform(post("/test/{workspace_id}/owner", workspace))
        .andExpect(status().isForbidden())
        .andExpect(jsonPath("$.error.code").value("FORBIDDEN"));
    mvc.perform(post("/test/{workspace_id}/member", workspace))
        .andExpect(status().isUnauthorized())
        .andExpect(jsonPath("$.error.code").value("SESSION_INVALID"));
    mvc.perform(post("/test/{workspace_id}/member", workspace))
        .andExpect(status().isUnauthorized())
        .andExpect(jsonPath("$.error.code").value("AUTH_REQUIRED"));
    mvc.perform(post("/test/{workspace_id}/member", workspace))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.error.code").value("NOT_FOUND"));
    mvc.perform(post("/test/{workspace_id}/member", workspace))
        .andExpect(status().isServiceUnavailable())
        .andExpect(header().string("Retry-After", "2"))
        .andExpect(jsonPath("$.error.code").value("DEPENDENCY_UNAVAILABLE"))
        .andExpect(jsonPath("$.error.request_id").exists())
        .andExpect(jsonPath("$.error.details").isArray());
    mvc.perform(post("/test/{workspace_id}/member", workspace))
        .andExpect(status().isServiceUnavailable());
    org.junit.jupiter.api.Assertions.assertEquals(0, mutations.get());
  }

  @Test
  void realClientAndGuardRejectRevokedMembershipAndServiceCredential() throws Exception {
    var identity = new HttpWorkspaceAccessClientTest();
    identity.start();
    try {
      var config = new WorkspaceAccessWebConfiguration(identity.client);
      mvc =
          MockMvcBuilders.standaloneSetup(new TestController())
              .addInterceptors(config.interceptor())
              .setCustomArgumentResolvers(config.resolver())
              .setControllerAdvice(new AccessFailureHandler())
              .addFilters(new RequestCorrelationFilter())
              .build();
      mvc.perform(
              post("/test/{workspace_id}/member", workspace)
                  .header("Authorization", "Bearer token"))
          .andExpect(status().isOk());
      identity.status.set(404);
      mvc.perform(
              post("/test/{workspace_id}/member", workspace)
                  .header("Authorization", "Bearer token"))
          .andExpect(status().isNotFound());
      identity.status.set(401);
      identity.body.set("{\"error\":{\"code\":\"SERVICE_CREDENTIAL_INVALID\"}}");
      mvc.perform(
              post("/test/{workspace_id}/member", workspace)
                  .header("Authorization", "Bearer token"))
          .andExpect(status().isServiceUnavailable())
          .andExpect(header().string("Retry-After", "2"))
          .andExpect(jsonPath("$.error.code").value("DEPENDENCY_UNAVAILABLE"));
      org.junit.jupiter.api.Assertions.assertEquals(1, mutations.get());
      org.junit.jupiter.api.Assertions.assertEquals(3, identity.calls.get());
    } finally {
      identity.stop();
    }
  }

  @Test
  void malformedWorkspaceIsRejectedBeforeCallingIdentity() throws Exception {
    mvc.perform(post("/test/invalid/member"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.error.code").value("MALFORMED_REQUEST"));
    verifyNoInteractions(client);
  }
}
