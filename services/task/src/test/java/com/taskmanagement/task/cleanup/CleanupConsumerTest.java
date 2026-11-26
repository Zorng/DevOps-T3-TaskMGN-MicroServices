package com.taskmanagement.task.cleanup;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.rabbitmq.client.*;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.util.*;
import java.util.concurrent.TimeoutException;
import org.junit.jupiter.api.Test;

class CleanupConsumerTest {
  @Test
  void successfulAndDuplicateDeliveryAckAfterCommit() throws Exception {
    var service = mock(CleanupService.class);
    var validator = mock(CleanupValidator.class);
    var channel = mock(Channel.class);
    var delivery = mock(GetResponse.class);
    var props = new AMQP.BasicProperties.Builder().build();
    var event =
        new CleanupEvent(
            UUID.randomUUID(), "workspace.deleted.v1", UUID.randomUUID(), null, UUID.randomUUID());
    var envelope = new Envelope(7, false, "events", "workspace.deleted.v1");
    when(channel.basicGet("task-cleanup.v1", false)).thenReturn(delivery);
    when(delivery.getProps()).thenReturn(props);
    when(delivery.getBody()).thenReturn(new byte[0]);
    when(delivery.getEnvelope()).thenReturn(envelope);
    when(validator.validate(any(), eq(props), eq("events"), eq("workspace.deleted.v1")))
        .thenReturn(event);
    when(service.apply(event)).thenReturn(true, false);
    var consumer =
        new CleanupConsumer(
            "localhost",
            5672,
            "taskmgn",
            "task",
            "pass",
            false,
            service,
            validator,
            new SimpleMeterRegistry());
    consumer.processOne(channel);
    consumer.processOne(channel);
    var order = inOrder(service, channel);
    order.verify(service).apply(event);
    order.verify(channel).basicAck(7, false);
    order.verify(service).apply(event);
    order.verify(channel).basicAck(7, false);
  }

  @Test
  void unconfirmedForwardKeepsOriginalUnacknowledged() throws Exception {
    var service = mock(CleanupService.class);
    var validator = mock(CleanupValidator.class);
    var channel = mock(Channel.class);
    var delivery = mock(GetResponse.class);
    var props = new AMQP.BasicProperties.Builder().build();
    var event =
        new CleanupEvent(
            UUID.randomUUID(), "workspace.deleted.v1", UUID.randomUUID(), null, UUID.randomUUID());
    var envelope = new Envelope(1, false, "events", "workspace.deleted.v1");
    when(channel.basicGet("task-cleanup.v1", false)).thenReturn(delivery);
    when(delivery.getProps()).thenReturn(props);
    when(delivery.getBody()).thenReturn(new byte[0]);
    when(delivery.getEnvelope()).thenReturn(envelope);
    when(validator.validate(any(), any(), any(), any())).thenReturn(event);
    when(service.apply(event)).thenThrow(new IllegalStateException());
    when(channel.waitForConfirms(2000)).thenThrow(new TimeoutException());
    var consumer =
        new CleanupConsumer(
            "localhost",
            5672,
            "taskmgn",
            "task",
            "pass",
            false,
            service,
            validator,
            new SimpleMeterRegistry());
    assertThrows(TimeoutException.class, () -> consumer.processOne(channel));
    verify(channel, never()).basicAck(anyLong(), anyBoolean());
  }
}
