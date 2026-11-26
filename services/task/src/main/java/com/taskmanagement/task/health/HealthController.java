package com.taskmanagement.task.health;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/health")
public class HealthController {
  private final ReadinessService readiness;

  public HealthController(ReadinessService readiness) {
    this.readiness = readiness;
  }

  @GetMapping("/live")
  public HealthResponse live() {
    return new HealthResponse("ok");
  }

  @GetMapping("/ready")
  public ResponseEntity<HealthResponse> ready() {
    boolean ready = readiness.isReady();
    return ResponseEntity.status(ready ? 200 : 503)
        .body(new HealthResponse(ready ? "ok" : "unavailable"));
  }

  public record HealthResponse(String status) {}
}
