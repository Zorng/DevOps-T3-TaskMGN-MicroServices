package com.taskmanagement.task.outbox;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.MapPropertySource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.ObjectMapper;

class OutboxPublisherTest {
  @Test
  void retryBackoffIsBoundedAndJittered() {
    for (int n = 1; n < 20; n++) {
      double d = OutboxPublisher.delaySeconds(n);
      double base = Math.min(60, Math.pow(2, n - 1));
      assertTrue(d >= (base == 60 ? 50 : base) && d <= Math.min(60, base * 1.2));
    }
  }

  @Test
  void cappedRetriesRetainJitter() {
    assertTrue(
        java.util.stream.IntStream.range(0, 20)
                .mapToDouble(i -> OutboxPublisher.delaySeconds(20))
                .distinct()
                .count()
            > 1);
  }

  @Test
  void enabledPublisherSchedulesPolling() {
    var context = new AnnotationConfigApplicationContext();
    context
        .getEnvironment()
        .getPropertySources()
        .addFirst(
            new MapPropertySource(
                "test", Map.of("task.outbox.enabled", "true", "task.outbox.poll-ms", "20")));
    var tx = mock(TransactionTemplate.class);
    when(tx.execute(any())).thenReturn(false);
    context.registerBean(JdbcTemplate.class, () -> mock(JdbcTemplate.class));
    context.registerBean(TransactionTemplate.class, () -> tx);
    context.registerBean(EventTransport.class, () -> event -> {});
    context.registerBean(ObjectMapper.class, () -> new ObjectMapper());
    context.registerBean(MeterRegistry.class, () -> new SimpleMeterRegistry());
    context.register(OutboxPublisher.class);
    try {
      context.refresh();
      verify(tx, timeout(2000).atLeastOnce()).execute(any());
    } finally {
      context.close();
    }
  }
}
