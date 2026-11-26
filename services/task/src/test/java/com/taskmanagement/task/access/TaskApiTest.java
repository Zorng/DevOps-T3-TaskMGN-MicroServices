package com.taskmanagement.task.access;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.taskmanagement.task.health.RequestCorrelationFilter;
import com.taskmanagement.task.tasks.*;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class TaskApiTest {
  UUID workspace = UUID.randomUUID(),
      actor = UUID.randomUUID(),
      id = UUID.randomUUID(),
      board = UUID.randomUUID(),
      key = UUID.randomUUID();
  String base = "/api/v1/workspaces/" + workspace + "/tasks";
  WorkspaceAccessClient client = mock(WorkspaceAccessClient.class);
  TaskService service = mock(TaskService.class);
  MockMvc mvc;
  WorkspaceAccess access =
      new WorkspaceAccess(
          new WorkspaceAccess.User(actor, "Member"),
          workspace,
          1,
          UUID.randomUUID(),
          WorkspaceAccess.Role.member,
          List.of());
  TaskView task =
      new TaskView(
          id,
          workspace,
          board,
          1,
          "Task",
          "",
          null,
          null,
          null,
          new TaskView.User(actor, "Member"),
          1,
          Instant.EPOCH,
          Instant.EPOCH);

  String json() {
    return "{\"board_id\":\"" + board + "\",\"title\":\"Task\"}";
  }

  @BeforeEach
  void setup() {
    var config = new WorkspaceAccessWebConfiguration(client);
    mvc =
        MockMvcBuilders.standaloneSetup(new TaskController(service))
            .addInterceptors(config.interceptor())
            .setCustomArgumentResolvers(config.resolver())
            .setControllerAdvice(new AccessFailureHandler())
            .addFilters(new RequestCorrelationFilter())
            .build();
    when(client.check(eq(workspace), any(), any())).thenReturn(access);
  }

  @Test
  void listRoutesAndRejectsInvalidParameters() throws Exception {
    when(service.list(eq(access), any(), any(), isNull(), eq(20), isNull()))
        .thenReturn(new TaskService.Page(List.of(task), null));
    mvc.perform(get(base))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.items[0].number").value(1))
        .andExpect(jsonPath("$.next_cursor").doesNotExist());
    mvc.perform(get(base).param("unknown", "1")).andExpect(status().isBadRequest());
    mvc.perform(get(base).param("limit", "101")).andExpect(status().isBadRequest());
    when(service.list(eq(access), any(), any(), isNull(), eq(20), eq("bad")))
        .thenThrow(new AccessFailure(400, "INVALID_CURSOR"));
    mvc.perform(get(base).param("cursor", "bad"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.error.code").value("INVALID_CURSOR"));
  }

  @Test
  void createRoutesAndValidates() throws Exception {
    when(service.create(eq(access), any(), any(), eq(key), any())).thenReturn(task);
    mvc.perform(
            post(base)
                .header("Idempotency-Key", key)
                .contentType("application/json")
                .content(json()))
        .andExpect(status().isCreated())
        .andExpect(header().string("ETag", "\"1\""))
        .andExpect(jsonPath("$.title").value("Task"));
    mvc.perform(post(base).contentType("application/json").content(json()))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.error.code").value("IDEMPOTENCY_KEY_REQUIRED"));
    mvc.perform(
            post(base)
                .header("Idempotency-Key", key)
                .contentType("application/json")
                .content("{\"title\":42}"))
        .andExpect(status().isUnprocessableEntity());
    mvc.perform(
            post(base)
                .header("Idempotency-Key", key)
                .contentType("application/json")
                .content("{\"board_id\":\"" + board + "\",\"title\":\"Task\",\"extra\":1}"))
        .andExpect(status().isUnprocessableEntity());
    mvc.perform(post(base).header("Idempotency-Key", key).contentType("text/plain").content(json()))
        .andExpect(status().isUnsupportedMediaType());
  }

  @Test
  void getPatchDeleteAndPreconditions() throws Exception {
    when(service.get(eq(access), any(), any(), eq(id))).thenReturn(task);
    when(service.patch(eq(access), any(), any(), eq(id), eq(1L), any())).thenReturn(task);
    mvc.perform(get(base + "/" + id))
        .andExpect(status().isOk())
        .andExpect(header().string("ETag", "\"1\""));
    mvc.perform(
            patch(base + "/" + id)
                .header("If-Match", "\"1\"")
                .contentType("application/json")
                .content("{\"title\":\"Task\"}"))
        .andExpect(status().isOk());
    mvc.perform(delete(base + "/" + id).header("If-Match", "\"1\""))
        .andExpect(status().isNoContent());
    mvc.perform(patch(base + "/" + id).contentType("application/json").content("{}"))
        .andExpect(status().isPreconditionRequired());
    mvc.perform(delete(base + "/" + id)).andExpect(status().isPreconditionRequired());
    when(service.get(eq(access), any(), any(), eq(id)))
        .thenThrow(new AccessFailure(404, "NOT_FOUND"));
    mvc.perform(get(base + "/" + id)).andExpect(status().isNotFound());
    when(service.patch(eq(access), any(), any(), eq(id), eq(2L), any()))
        .thenThrow(new AccessFailure(412, "VERSION_CONFLICT"));
    mvc.perform(
            patch(base + "/" + id)
                .header("If-Match", "\"2\"")
                .contentType("application/json")
                .content("{\"title\":\"Task\"}"))
        .andExpect(status().isPreconditionFailed());
  }

  @Test
  void accessDenialPreventsEveryRoute() throws Exception {
    when(client.check(eq(workspace), any(), any())).thenThrow(new AccessFailure(404, "NOT_FOUND"));
    mvc.perform(get(base)).andExpect(status().isNotFound());
    mvc.perform(get(base + "/" + id)).andExpect(status().isNotFound());
    mvc.perform(
            post(base)
                .header("Idempotency-Key", key)
                .contentType("application/json")
                .content(json()))
        .andExpect(status().isNotFound());
    mvc.perform(
            patch(base + "/" + id)
                .header("If-Match", "\"1\"")
                .contentType("application/json")
                .content("{}"))
        .andExpect(status().isNotFound());
    mvc.perform(delete(base + "/" + id).header("If-Match", "\"1\""))
        .andExpect(status().isNotFound());
    verifyNoInteractions(service);
  }
}
