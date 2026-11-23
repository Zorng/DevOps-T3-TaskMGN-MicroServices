package com.taskmanagement.task.comments;
import com.taskmanagement.task.access.*;
import com.taskmanagement.task.tasks.TaskValidation;
import jakarta.servlet.http.*;
import java.util.*;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.*;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.core.StreamReadFeature;
@RestController
@RequestMapping("/api/v1/workspaces/{workspace_id}/tasks/{task_id}/comments")
public class CommentController {
    private final CommentService service;
    private final ObjectMapper mapper=JsonMapper.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION).enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build();
    public CommentController(CommentService service) { this.service=service; }
    private UUID uuid(String text) { try { return TaskValidation.uuid(text); } catch(AccessFailure e) { throw new AccessFailure(400,"MALFORMED_REQUEST"); } }
    private byte[] bytes(byte[] b) { return b==null?new byte[0]:b; }
    private void request(HttpServletRequest r,byte[] body,boolean post) {
        if(body.length>65536) throw new AccessFailure(413,"PAYLOAD_TOO_LARGE");
        if(!post && body.length>0) throw new AccessFailure(400,"MALFORMED_REQUEST");
        if(post && body.length>0 && (r.getContentType()==null || !r.getContentType().split(";",2)[0].trim().equalsIgnoreCase("application/json"))) throw new AccessFailure(415,"UNSUPPORTED_MEDIA_TYPE");
        for(var e:r.getParameterMap().entrySet()) if(post || !Set.of("limit","cursor").contains(e.getKey()) || e.getValue().length!=1) throw new AccessFailure(400,"MALFORMED_REQUEST");
    }
    private JsonNode json(byte[] body) { try { return mapper.readTree(body); } catch(RuntimeException e) { throw new AccessFailure(400,"MALFORMED_REQUEST"); } }
    @GetMapping @RequireWorkspaceAccess
    public CommentService.Page list(WorkspaceAccess access,@PathVariable String task_id,HttpServletRequest request,@RequestBody(required=false) byte[] data) {
        request(request,bytes(data),false); int limit=20; String supplied=request.getParameter("limit");
        if(supplied!=null) { try { if(!supplied.matches("[0-9]+")) throw new IllegalArgumentException(); limit=Integer.parseInt(supplied); if(limit<1||limit>100) throw new IllegalArgumentException(); } catch(RuntimeException e) { throw new AccessFailure(400,"MALFORMED_REQUEST"); } }
        return service.list(access,uuid(task_id),limit,request.getParameter("cursor"));
    }
    @PostMapping @RequireWorkspaceAccess
    public ResponseEntity<CommentView> create(WorkspaceAccess access,@PathVariable String task_id,HttpServletRequest request,HttpServletResponse response,@RequestBody(required=false) byte[] data,@RequestHeader(value="Idempotency-Key",required=false) String key) {
        byte[] body=bytes(data); request(request,body,true); if(key==null) throw new AccessFailure(400,"IDEMPOTENCY_KEY_REQUIRED"); UUID parsed=uuid(key); if(parsed.version()!=4||parsed.variant()!=2) throw new AccessFailure(400,"MALFORMED_REQUEST");
        return ResponseEntity.status(201).body(service.create(access,uuid(task_id),parsed,CommentValidation.parse(json(body)),response.getHeader("X-Request-ID")));
    }
}
