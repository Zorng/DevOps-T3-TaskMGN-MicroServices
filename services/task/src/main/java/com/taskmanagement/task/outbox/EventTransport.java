package com.taskmanagement.task.outbox;

public interface EventTransport {
  /** Success means a positive broker confirm and no mandatory return; any ambiguity throws. */
  void publish(OutboxEvent event) throws Exception;
}
