package com.taskmanagement.task.access;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.taskmanagement.task.comments.*;
import com.taskmanagement.task.health.RequestCorrelationFilter;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class CommentApiTest {
  final UUID workspace = UUID.randomUUID(),
      actor = UUID.randomUUID(),
      task = UUID.randomUUID(),
      key = UUID.randomUUID();
  final String base = "/api/v1/workspaces/" + workspace + "/tasks/" + task + "/comments";
  final WorkspaceAccess access =
      new WorkspaceAccess(
          new WorkspaceAccess.User(actor, "User"),
          workspace,
          1,
          UUID.randomUUID(),
          WorkspaceAccess.Role.member,
          List.of());
  final WorkspaceAccessClient client = mock(WorkspaceAccessClient.class);
  final CommentService service = mock(CommentService.class);
  MockMvc mvc;
  final CommentView view =
      new CommentView(
          UUID.randomUUID(),
          workspace,
          task,
          "hello",
          new CommentView.User(actor, "User"),
          List.of(),
          Instant.EPOCH);

  @BeforeEach
  void setup() {
    var config = new WorkspaceAccessWebConfiguration(client);
    mvc =
        MockMvcBuilders.standaloneSetup(new CommentController(service))
            .addInterceptors(config.interceptor())
            .setCustomArgumentResolvers(config.resolver())
            .setControllerAdvice(new AccessFailureHandler())
            .addFilters(new RequestCorrelationFilter())
            .build();
    when(client.check(eq(workspace), any(), any())).thenReturn(access);
  }

  @Test
  void listAndCursorValidation() throws Exception {
    when(service.list(access, task, 20, null))
        .thenReturn(new CommentService.Page(List.of(view), null));
    mvc.perform(get(base))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.items[0].body").value("hello"));
    when(service.list(access, task, 20, "bad")).thenThrow(new AccessFailure(400, "INVALID_CURSOR"));
    mvc.perform(get(base).param("cursor", "bad"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.error.code").value("INVALID_CURSOR"));
    when(service.list(access, task, 20, null)).thenThrow(new AccessFailure(404, "NOT_FOUND"));
    mvc.perform(get(base)).andExpect(status().isNotFound());
    mvc.perform(get(base).param("limit", "0")).andExpect(status().isBadRequest());
  }

  @Test
  void createAndValidation() throws Exception {
    when(service.create(eq(access), eq(task), eq(key), any(), any())).thenReturn(view);
    mvc.perform(
            post(base)
                .header("Idempotency-Key", key)
                .contentType("application/json")
                .content("{\"body\":\"hello\"}"))
        .andExpect(status().isCreated())
        .andExpect(jsonPath("$.mentions").isArray());
    mvc.perform(post(base).contentType("application/json").content("{\"body\":\"hello\"}"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.error.code").value("IDEMPOTENCY_KEY_REQUIRED"));
    mvc.perform(
            post(base)
                .header("Idempotency-Key", key)
                .contentType("application/json")
                .content("{\"body\":\" \"}"))
        .andExpect(status().isUnprocessableEntity());
    mvc.perform(
            post(base)
                .header("Idempotency-Key", key)
                .contentType("application/json")
                .content("{\"body\":\"x\",\"unknown\":1}"))
        .andExpect(status().isUnprocessableEntity());
    mvc.perform(
            post(base)
                .header("Idempotency-Key", key)
                .contentType("application/json")
                .content("{\"body\":\"x\",\"body\":\"y\"}"))
        .andExpect(status().isBadRequest());
    when(service.create(eq(access), eq(task), eq(key), any(), any()))
        .thenThrow(new AccessFailure(404, "NOT_FOUND"));
    mvc.perform(
            post(base)
                .header("Idempotency-Key", key)
                .contentType("application/json")
                .content("{\"body\":\"hello\"}"))
        .andExpect(status().isNotFound());
    mvc.perform(patch(base).contentType("application/json").content("{}"))
        .andExpect(status().isMethodNotAllowed());
    mvc.perform(delete(base)).andExpect(status().isMethodNotAllowed());
  }

  @Test
  void deniedAccessPreventsBothRoutes() throws Exception {
    when(client.check(eq(workspace), any(), any())).thenThrow(new AccessFailure(404, "NOT_FOUND"));
    mvc.perform(get(base)).andExpect(status().isNotFound());
    mvc.perform(
            post(base)
                .header("Idempotency-Key", key)
                .contentType("application/json")
                .content("{\"body\":\"hello\"}"))
        .andExpect(status().isNotFound());
    verifyNoInteractions(service);
  }
}
