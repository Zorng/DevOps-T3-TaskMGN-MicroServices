package com.taskmanagement.task.access;

import com.taskmanagement.task.board.Board;
import com.taskmanagement.task.board.BoardController;
import com.taskmanagement.task.board.BoardService;
import com.taskmanagement.task.health.RequestCorrelationFilter;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import tools.jackson.databind.ObjectMapper;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class BoardApiTest {
    final UUID workspace=UUID.randomUUID(), actor=UUID.randomUUID(), boardId=UUID.randomUUID(), key=UUID.randomUUID();
    final String base="/api/v1/workspaces/" + workspace + "/boards";
    final WorkspaceAccessClient client=mock(WorkspaceAccessClient.class);
    final BoardService service=mock(BoardService.class);
    final Instant time=Instant.parse("2026-11-14T10:00:00Z");
    final Board board=new Board(boardId, workspace, "Todo", 1, time, time);
    MockMvc mvc;
    WorkspaceAccess access(WorkspaceAccess.Role role) { return new WorkspaceAccess(new WorkspaceAccess.User(actor,"User"),workspace,1,UUID.randomUUID(),role,List.of()); }
    @BeforeEach void setup() {
        var config=new WorkspaceAccessWebConfiguration(client);
        mvc=MockMvcBuilders.standaloneSetup(new BoardController(service,new ObjectMapper())).addInterceptors(config.interceptor())
                .setCustomArgumentResolvers(config.resolver()).setControllerAdvice(new AccessFailureHandler()).addFilters(new RequestCorrelationFilter()).build();
        when(client.check(eq(workspace),any(),any())).thenReturn(access(WorkspaceAccess.Role.owner));
    }
    @Test void listAndGet() throws Exception {
        when(service.list(workspace)).thenReturn(List.of(board)); when(service.visible(workspace,boardId)).thenReturn(board);
        mvc.perform(get(base)).andExpect(status().isOk()).andExpect(jsonPath("$.items[0].name").value("Todo"));
        mvc.perform(get(base+"/"+boardId)).andExpect(status().isOk()).andExpect(header().string("ETag","\"1\"")).andExpect(jsonPath("$.workspace_id").value(workspace.toString()));
        when(service.visible(workspace,boardId)).thenThrow(new AccessFailure(404,"NOT_FOUND"));
        mvc.perform(get(base+"/"+boardId)).andExpect(status().isNotFound());
        mvc.perform(get(base+"?bad=1")).andExpect(status().isBadRequest());
    }
    @Test void createValidatesAndRequiresAccess() throws Exception {
        when(service.create(workspace,actor,key,"Todo")).thenReturn(board);
        mvc.perform(post(base).header("Idempotency-Key",key).contentType("application/json").content("{\"name\":\"  Todo  \"}"))
                .andExpect(status().isCreated()).andExpect(header().string("ETag","\"1\""));
        mvc.perform(post(base).contentType("application/json").content("{\"name\":\"Todo\"}")).andExpect(status().isBadRequest()).andExpect(jsonPath("$.error.code").value("IDEMPOTENCY_KEY_REQUIRED"));
        for (String bad : List.of("{\"name\":42}","{\"name\":\"\"}","{\"name\":\"x\",\"other\":1}","{\"name\":\""+"😀".repeat(51)+"\"}"))
            mvc.perform(post(base).header("Idempotency-Key",key).contentType("application/json").content(bad)).andExpect(status().isUnprocessableEntity());
        for (String malformed : List.of("{\"name\":\"x\",\"name\":\"y\"}", "{\"name\":\"x\"} {}", "{"))
            mvc.perform(post(base).header("Idempotency-Key",key).contentType("application/json").content(malformed)).andExpect(status().isBadRequest());
        mvc.perform(post(base).header("Idempotency-Key", UUID.fromString("00000000-0000-1000-8000-000000000001")).contentType("application/json").content("{\"name\":\"x\"}")).andExpect(status().isBadRequest());
        mvc.perform(post(base).header("Idempotency-Key",key).contentType("text/plain").content("{}" )).andExpect(status().isUnsupportedMediaType());
        when(client.check(eq(workspace),any(),any())).thenThrow(new AccessFailure(404,"NOT_FOUND"));
        mvc.perform(post(base).header("Idempotency-Key",key).contentType("application/json").content("{\"name\":\"Todo\"}")).andExpect(status().isNotFound());
        verify(service,times(1)).create(any(),any(),any(),any());
    }
    @Test void renameChecksPreconditionAndShape() throws Exception {
        when(service.rename(workspace,boardId,1,"Todo")).thenReturn(board);
        mvc.perform(patch(base+"/"+boardId).header("If-Match","\"1\"").contentType("application/json").content("{\"name\":\"Todo\"}"))
                .andExpect(status().isOk()).andExpect(header().string("ETag","\"1\""));
        mvc.perform(patch(base+"/"+boardId).contentType("application/json").content("{\"name\":\"Todo\"}")).andExpect(status().isPreconditionRequired());
        mvc.perform(patch(base+"/"+boardId).header("If-Match","*").contentType("application/json").content("{\"name\":\"Todo\"}")).andExpect(status().isBadRequest());
        when(service.rename(workspace,boardId,2,"Todo")).thenThrow(new AccessFailure(412,"VERSION_CONFLICT"));
        mvc.perform(patch(base+"/"+boardId).header("If-Match","\"2\"").contentType("application/json").content("{\"name\":\"Todo\"}")).andExpect(status().isPreconditionFailed());
    }
    @Test void deleteRequiresOwnerAndPrecondition() throws Exception {
        mvc.perform(delete(base+"/"+boardId).header("If-Match","\"1\"")).andExpect(status().isNoContent());
        mvc.perform(delete(base+"/"+boardId)).andExpect(status().isPreconditionRequired());
        when(client.check(eq(workspace),any(),any())).thenReturn(access(WorkspaceAccess.Role.member));
        mvc.perform(delete(base+"/"+boardId).header("If-Match","\"1\"")).andExpect(status().isForbidden());
        verify(service,times(1)).delete(any(),any(),anyLong());
    }
    @Test void memberCanCreateAndRenameWhileInaccessibleResourcesFail() throws Exception {
        when(client.check(eq(workspace),any(),any())).thenReturn(access(WorkspaceAccess.Role.member));
        when(service.create(workspace,actor,key,"Todo")).thenReturn(board);
        when(service.rename(workspace,boardId,1,"Todo")).thenReturn(board);
        mvc.perform(post(base).header("Idempotency-Key",key).contentType("application/json").content("{\"name\":\"Todo\"}")).andExpect(status().isCreated());
        mvc.perform(patch(base+"/"+boardId).header("If-Match","\"1\"").contentType("application/json").content("{\"name\":\"Todo\"}")).andExpect(status().isOk());
        when(service.visible(workspace,boardId)).thenThrow(new AccessFailure(404,"NOT_FOUND"));
        when(service.rename(workspace,boardId,1,"Todo")).thenThrow(new AccessFailure(404,"NOT_FOUND"));
        mvc.perform(get(base+"/"+boardId)).andExpect(status().isNotFound());
        mvc.perform(patch(base+"/"+boardId).header("If-Match","\"1\"").contentType("application/json").content("{\"name\":\"Todo\"}")).andExpect(status().isNotFound());
        when(client.check(eq(workspace),any(),any())).thenReturn(access(WorkspaceAccess.Role.owner));
        doThrow(new AccessFailure(404,"NOT_FOUND")).when(service).delete(workspace,boardId,1);
        mvc.perform(delete(base+"/"+boardId).header("If-Match","\"1\"")).andExpect(status().isNotFound());
        when(client.check(eq(workspace),any(),any())).thenThrow(new AccessFailure(404,"NOT_FOUND"));
        mvc.perform(get(base)).andExpect(status().isNotFound());
        mvc.perform(get(base+"/"+boardId)).andExpect(status().isNotFound());
        mvc.perform(patch(base+"/"+boardId).header("If-Match","\"1\"").contentType("application/json").content("{\"name\":\"Todo\"}")).andExpect(status().isNotFound());
        mvc.perform(delete(base+"/"+boardId).header("If-Match","\"1\"")).andExpect(status().isNotFound());
        verify(service,times(2)).rename(workspace,boardId,1,"Todo");
        verify(service,times(1)).delete(workspace,boardId,1);
    }

}
