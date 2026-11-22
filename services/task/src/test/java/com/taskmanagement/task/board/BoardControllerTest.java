package com.taskmanagement.task.board;

import com.taskmanagement.task.access.AccessFailure;
import com.taskmanagement.task.access.WorkspaceAccess;
import jakarta.servlet.http.HttpServletRequest;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class BoardControllerTest {
    final UUID workspace=UUID.randomUUID(), actor=UUID.randomUUID(), id=UUID.randomUUID(), key=UUID.randomUUID();
    final WorkspaceAccess access=new WorkspaceAccess(new WorkspaceAccess.User(actor,"User"),workspace,1,UUID.randomUUID(),WorkspaceAccess.Role.owner,List.of());
    final BoardService service=mock(BoardService.class);
    final BoardController controller=new BoardController(service,new ObjectMapper());
    final HttpServletRequest request=mock(HttpServletRequest.class);
    final Instant now=Instant.parse("2026-11-14T10:00:00Z");
    final Board board=new Board(id,workspace,"Todo",1,now,now);
    byte[] body(String text) { return text.getBytes(StandardCharsets.UTF_8); }
    @Test void listDelegatesToWorkspace() { when(service.list(workspace)).thenReturn(List.of(board)); assertEquals(List.of(board),controller.list(access,request,null).get("items")); }
    @Test void getReturnsBoardAndEtag() { when(service.visible(workspace,id)).thenReturn(board); var r=controller.get(access,id.toString(),request,null); assertEquals(board,r.getBody()); assertEquals("\"1\"",r.getHeaders().getETag()); }
    @Test void createNormalizesAndRejectsInvalidName() {
        when(request.getContentType()).thenReturn("application/json"); when(service.create(workspace,actor,key,"Todo")).thenReturn(board);
        assertEquals(board,controller.create(access,request,body("{\"name\":\" Todo \"}"),key.toString()).getBody());
        assertEquals(422,assertThrows(AccessFailure.class,()->controller.create(access,request,body("{\"name\":null}"),key.toString())).status());
    }
    @Test void renameDelegatesVersion() { when(request.getContentType()).thenReturn("application/json"); when(service.rename(workspace,id,1,"Todo")).thenReturn(board);
        assertEquals(board,controller.rename(access,id.toString(),request,body("{\"name\":\"Todo\"}"),"\"1\"").getBody());
        assertEquals(428,assertThrows(AccessFailure.class,()->controller.rename(access,id.toString(),request,body("{}"),null)).status()); }
    @Test void deleteDelegatesVersion() { assertEquals(204,controller.delete(access,id.toString(),request,null,"\"1\"").getStatusCode().value()); verify(service).delete(workspace,id,1); }
}
