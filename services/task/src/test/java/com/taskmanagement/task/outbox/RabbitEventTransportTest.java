package com.taskmanagement.task.outbox;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.rabbitmq.client.*;
import java.util.UUID;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class RabbitEventTransportTest {
  ConnectionFactory factory = mock(ConnectionFactory.class);
  Connection connection = mock(Connection.class);
  Channel channel = mock(Channel.class);
  OutboxEvent event =
      new OutboxEvent(UUID.randomUUID(), "task.assigned.v1", "{}", UUID.randomUUID().toString(), 0);

  @BeforeEach
  void setup() throws Exception {
    when(factory.newConnection()).thenReturn(connection);
    when(connection.createChannel()).thenReturn(channel);
  }

  @Test
  void negativeConfirmAndConfirmTimeoutAreFailures() throws Exception {
    when(channel.waitForConfirms(2000)).thenReturn(false).thenThrow(new TimeoutException());
    var transport = new RabbitEventTransport(factory);
    assertThrows(IllegalStateException.class, () -> transport.publish(event));
    assertThrows(IllegalStateException.class, () -> transport.publish(event));
    verify(channel, times(2)).basicPublish(eq("events"), eq(event.type()), eq(true), any(), any());
  }

  @Test
  void mandatoryReturnOverridesPositiveConfirm() throws Exception {
    AtomicReference<ReturnListener> listener = new AtomicReference<>();
    doAnswer(
            i -> {
              listener.set(i.getArgument(0));
              return null;
            })
        .when(channel)
        .addReturnListener(any(ReturnListener.class));
    doAnswer(
            i -> {
              listener
                  .get()
                  .handleReturn(312, "NO_ROUTE", "events", event.type(), null, new byte[0]);
              return null;
            })
        .when(channel)
        .basicPublish(anyString(), anyString(), eq(true), any(), any());
    when(channel.waitForConfirms(2000)).thenReturn(true);
    assertThrows(
        IllegalStateException.class, () -> new RabbitEventTransport(factory).publish(event));
  }
}
