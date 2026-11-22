package com.taskmanagement.task.tasks;
import com.taskmanagement.task.access.*;
import jakarta.servlet.http.*;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
class TaskControllerTest {
    final TaskService service=mock(TaskService.class); final TaskController controller=new TaskController(service);
    final HttpServletRequest req=mock(HttpServletRequest.class); final HttpServletResponse res=mock(HttpServletResponse.class);
    final UUID workspace=UUID.randomUUID(), actor=UUID.randomUUID(), board=UUID.randomUUID(), id=UUID.randomUUID(), key=UUID.randomUUID();
    final WorkspaceAccess access=new WorkspaceAccess(new WorkspaceAccess.User(actor,"User"),workspace,1,UUID.randomUUID(),WorkspaceAccess.Role.member,List.of());
    final TaskView view=new TaskView(id,workspace,board,1,"Task","",null,null,null,new TaskView.User(actor,"User"),1,Instant.EPOCH,Instant.EPOCH);
    @Test void listDelegates() { when(service.list(eq(access),any(),any(),isNull(),eq(20),isNull())).thenReturn(new TaskService.Page(List.of(view),null)); assertEquals(view,controller.list(access,req,res,null).items().getFirst()); }
    @Test void createDelegatesNormalizedInput() { when(req.getContentType()).thenReturn("application/json"); when(service.create(eq(access),any(),any(),eq(key),any())).thenReturn(view); assertEquals(view,controller.create(access,req,res,("{\"board_id\":\""+board+"\",\"title\":\" Task \"}").getBytes(),key.toString()).getBody()); }
    @Test void getDelegates() { when(service.get(eq(access),any(),any(),eq(id))).thenReturn(view); assertEquals(view,controller.get(access,id.toString(),req,res,null).getBody()); }
    @Test void patchDelegates() { when(req.getContentType()).thenReturn("application/json"); when(service.patch(eq(access),any(),any(),eq(id),eq(1L),any())).thenReturn(view); assertEquals(view,controller.patch(access,id.toString(),req,res,"{\"title\":\"Task\"}".getBytes(),"\"1\"").getBody()); }
    @Test void deleteDelegates() { assertEquals(204,controller.delete(access,id.toString(),req,res,null,"\"1\"").getStatusCode().value()); verify(service).delete(eq(access),any(),any(),eq(id),eq(1L)); }
}
