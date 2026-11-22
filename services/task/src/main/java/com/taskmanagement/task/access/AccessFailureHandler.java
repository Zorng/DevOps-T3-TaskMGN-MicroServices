package com.taskmanagement.task.access;

import jakarta.servlet.http.HttpServletResponse;
import java.util.List;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class AccessFailureHandler {
    @ExceptionHandler(AccessFailure.class)
    public ResponseEntity<Map<String, Object>> handle(AccessFailure failure, HttpServletResponse response) {
        String message = switch (failure.status()) {
            case 400 -> "The request is malformed.";
            case 401 -> "Authentication is required or the session is invalid.";
            case 403 -> "This action is forbidden.";
            case 404 -> "The resource was not found.";
            case 409 -> "The request conflicts with current state.";
            case 412 -> "The resource version has changed.";
            case 413 -> "The request is too large.";
            case 415 -> "The media type is unsupported.";
            case 422 -> "The request contains invalid fields.";
            case 428 -> "A precondition is required.";
            default -> "A dependency is unavailable.";
        };
        var result = ResponseEntity.status(failure.status());
        if (failure.status() == 503) result.header("Retry-After", "2");
        return result.body(Map.of("error", Map.of("code", failure.code(), "message", message,
                "request_id", response.getHeader("X-Request-ID"), "details", failure.status() == 422
                        ? List.of(Map.of("field", "name", "reason", "Must be a string of 1 to 50 characters with no unknown fields.")) : List.of())));
    }
}
