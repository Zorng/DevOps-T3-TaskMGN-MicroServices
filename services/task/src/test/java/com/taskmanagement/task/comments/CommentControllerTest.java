package com.taskmanagement.task.comments;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.taskmanagement.task.access.*;
import jakarta.servlet.http.*;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Test;

class CommentControllerTest {
  final UUID workspace = UUID.randomUUID(),
      actor = UUID.randomUUID(),
      task = UUID.randomUUID(),
      key = UUID.randomUUID();
  final WorkspaceAccess access =
      new WorkspaceAccess(
          new WorkspaceAccess.User(actor, "User"),
          workspace,
          1,
          UUID.randomUUID(),
          WorkspaceAccess.Role.member,
          List.of());
  final CommentService service = mock(CommentService.class);
  final CommentController controller = new CommentController(service);
  final HttpServletRequest request = mock(HttpServletRequest.class);
  final HttpServletResponse response = mock(HttpServletResponse.class);
  final CommentView view =
      new CommentView(
          UUID.randomUUID(),
          workspace,
          task,
          "hello",
          new CommentView.User(actor, "User"),
          List.of(),
          Instant.EPOCH);

  @Test
  void listDelegates() {
    when(service.list(access, task, 20, null))
        .thenReturn(new CommentService.Page(List.of(view), null));
    assertEquals(view, controller.list(access, task.toString(), request, null).items().getFirst());
  }

  @Test
  void createDelegates() {
    when(request.getContentType()).thenReturn("application/json");
    when(service.create(eq(access), eq(task), eq(key), any(), any())).thenReturn(view);
    assertEquals(
        view,
        controller
            .create(
                access,
                task.toString(),
                request,
                response,
                "{\"body\":\"hello\"}".getBytes(),
                key.toString())
            .getBody());
  }
}
