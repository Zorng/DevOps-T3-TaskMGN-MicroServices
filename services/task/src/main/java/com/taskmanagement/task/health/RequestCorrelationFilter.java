package com.taskmanagement.task.health;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class RequestCorrelationFilter extends OncePerRequestFilter {
  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain chain)
      throws ServletException, IOException {
    String requestId = request.getHeader("X-Request-ID");
    try {
      if (requestId == null || !UUID.fromString(requestId).toString().equalsIgnoreCase(requestId)) {
        requestId = UUID.randomUUID().toString();
      }
    } catch (IllegalArgumentException ex) {
      requestId = UUID.randomUUID().toString();
    }
    response.setHeader("X-Request-ID", requestId);
    response.setHeader("Cache-Control", "no-store");
    MDC.put("request_id", requestId);
    try {
      chain.doFilter(request, response);
    } finally {
      MDC.remove("request_id");
    }
  }
}
