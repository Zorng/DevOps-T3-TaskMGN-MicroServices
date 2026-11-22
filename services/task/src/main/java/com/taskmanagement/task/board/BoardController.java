package com.taskmanagement.task.board;

import com.taskmanagement.task.access.AccessFailure;
import com.taskmanagement.task.access.RequireWorkspaceAccess;
import com.taskmanagement.task.access.WorkspaceAccess;
import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.core.StreamReadFeature;

@RestController
@RequestMapping("/api/v1/workspaces/{workspace_id}/boards")
public class BoardController {
    private final BoardService service;
    private final ObjectMapper mapper;
    public BoardController(BoardService service, ObjectMapper mapper) { this.service = service; this.mapper = JsonMapper.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION).enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build(); }
    private void request(HttpServletRequest request, byte[] body, boolean hasBody) {
        if (request.getQueryString() != null) throw new AccessFailure(400, "MALFORMED_REQUEST");
        if (body.length > 65536) throw new AccessFailure(413, "PAYLOAD_TOO_LARGE");
        if (!hasBody && body.length > 0) throw new AccessFailure(400, "MALFORMED_REQUEST");
        if (hasBody && body.length > 0 && (request.getContentType() == null || !request.getContentType().split(";", 2)[0].trim().equalsIgnoreCase("application/json")))
            throw new AccessFailure(415, "UNSUPPORTED_MEDIA_TYPE");
    }
    private UUID uuid(String text) {
        try { UUID result = UUID.fromString(text); if (!result.toString().equalsIgnoreCase(text)) throw new IllegalArgumentException(); return result; }
        catch (RuntimeException ex) { throw new AccessFailure(400, "MALFORMED_REQUEST"); }
    }
    private String name(byte[] body) {
        JsonNode value;
        try { value = mapper.readTree(body); } catch (RuntimeException ex) { throw new AccessFailure(400, "MALFORMED_REQUEST"); }
        if (value == null || !value.isObject() || value.size() != 1 || !value.has("name") || !value.get("name").isString()) throw new AccessFailure(422, "VALIDATION_FAILED", "name", "Must be a string of 1 to 50 characters with no unknown fields.");
        String raw = value.get("name").asString();
        int start = 0, end = raw.length();
        while (start < end && whitespace(raw.codePointAt(start))) start += Character.charCount(raw.codePointAt(start));
        while (end > start && whitespace(raw.codePointBefore(end))) end -= Character.charCount(raw.codePointBefore(end));
        String name = raw.substring(start, end);
        int length = name.codePointCount(0, name.length());
        if (length < 1 || length > 50) throw new AccessFailure(422, "VALIDATION_FAILED", "name", "Must be a string of 1 to 50 characters with no unknown fields.");
        return name;
    }
    private boolean whitespace(int codepoint) { return Character.isWhitespace(codepoint) || Character.isSpaceChar(codepoint); }
    private long version(String value) {
        if (value == null) throw new AccessFailure(428, "PRECONDITION_REQUIRED");
        if (!value.matches("\"[1-9][0-9]*\"")) throw new AccessFailure(400, "MALFORMED_REQUEST");
        try { return Long.parseLong(value.substring(1, value.length()-1)); }
        catch (NumberFormatException ex) { throw new AccessFailure(400, "MALFORMED_REQUEST"); }
    }
    private ResponseEntity<Board> response(int status, Board board) { return ResponseEntity.status(status).eTag("\"" + board.version() + "\"").body(board); }
    @GetMapping @RequireWorkspaceAccess
    public Map<String, Object> list(WorkspaceAccess access, HttpServletRequest request, @RequestBody(required=false) byte[] bytes) {
        request(request, bytes == null ? new byte[0] : bytes, false); return Map.of("items", service.list(access.workspaceId()));
    }
    @PostMapping @RequireWorkspaceAccess
    public ResponseEntity<Board> create(WorkspaceAccess access, HttpServletRequest request, @RequestBody(required=false) byte[] bytes,
                                        @RequestHeader(value="Idempotency-Key", required=false) String key) {
        byte[] body = bytes == null ? new byte[0] : bytes; request(request, body, true);
        if (key == null) throw new AccessFailure(400, "IDEMPOTENCY_KEY_REQUIRED");
        UUID parsedKey = uuid(key);
        if (parsedKey.version() != 4 || parsedKey.variant() != 2) throw new AccessFailure(400, "MALFORMED_REQUEST");
        return response(201, service.create(access.workspaceId(), access.actor().id(), parsedKey, name(body)));
    }
    @GetMapping("/{board_id}") @RequireWorkspaceAccess
    public ResponseEntity<Board> get(WorkspaceAccess access, @PathVariable String board_id, HttpServletRequest request, @RequestBody(required=false) byte[] bytes) {
        request(request, bytes == null ? new byte[0] : bytes, false); return response(200, service.visible(access.workspaceId(), uuid(board_id)));
    }
    @PatchMapping("/{board_id}") @RequireWorkspaceAccess
    public ResponseEntity<Board> rename(WorkspaceAccess access, @PathVariable String board_id, HttpServletRequest request, @RequestBody(required=false) byte[] bytes,
                                        @RequestHeader(value="If-Match", required=false) String match) {
        byte[] body = bytes == null ? new byte[0] : bytes; request(request, body, true);
        return response(200, service.rename(access.workspaceId(), uuid(board_id), version(match), name(body)));
    }
    @DeleteMapping("/{board_id}") @RequireWorkspaceAccess(owner=true)
    public ResponseEntity<Void> delete(WorkspaceAccess access, @PathVariable String board_id, HttpServletRequest request, @RequestBody(required=false) byte[] bytes,
                                       @RequestHeader(value="If-Match", required=false) String match) {
        request(request, bytes == null ? new byte[0] : bytes, false);
        service.delete(access.workspaceId(), uuid(board_id), version(match)); return ResponseEntity.noContent().build();
    }
}
