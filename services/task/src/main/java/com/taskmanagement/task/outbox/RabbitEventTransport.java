package com.taskmanagement.task.outbox;

import com.rabbitmq.client.AMQP;
import com.rabbitmq.client.Connection;
import com.rabbitmq.client.ConnectionFactory;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "task.outbox.enabled", havingValue = "true")
public class RabbitEventTransport implements EventTransport {
  private final ConnectionFactory factory;

  @org.springframework.beans.factory.annotation.Autowired
  public RabbitEventTransport(
      @Value("${task.broker.host}") String host,
      @Value("${task.broker.port}") int port,
      @Value("${task.broker.vhost}") String vhost,
      @Value("${task.broker.username}") String username,
      @Value("${task.broker.password}") String password,
      @Value("${task.broker.tls:false}") boolean tls)
      throws Exception {
    if (host.isBlank() || username.isBlank() || password.isBlank() || vhost.isBlank())
      throw new IllegalArgumentException("Broker configuration is incomplete");
    factory = new ConnectionFactory();
    factory.setHost(host);
    factory.setPort(port);
    factory.setVirtualHost(vhost);
    factory.setUsername(username);
    factory.setPassword(password);
    factory.setConnectionTimeout(2000);
    factory.setHandshakeTimeout(2000);
    factory.setShutdownTimeout(1000);
    factory.setChannelRpcTimeout(2000);
    factory.setAutomaticRecoveryEnabled(false);
    if (tls) {
      factory.useSslProtocol(javax.net.ssl.SSLContext.getDefault());
      factory.enableHostnameVerification();
    }
  }

  RabbitEventTransport(ConnectionFactory factory) {
    this.factory = factory;
  }

  @Override
  public void publish(OutboxEvent event) throws Exception {
    if (!event.type().equals("task.assigned.v1")
        && !event.type().equals("task.comment_mentioned.v1"))
      throw new IllegalArgumentException("Unsupported outbox event type");
    byte[] payload = event.payload().getBytes(StandardCharsets.UTF_8);
    if (payload.length > 65536) throw new IllegalArgumentException("Event exceeds size limit");
    AtomicReference<Connection> active = new AtomicReference<>();
    AtomicBoolean expired = new AtomicBoolean();
    ExecutorService executor =
        Executors.newSingleThreadExecutor(
            r -> {
              Thread t = new Thread(r, "task-broker-publish");
              t.setDaemon(true);
              return t;
            });
    Future<?> future =
        executor.submit(
            () -> {
              try (Connection connection = factory.newConnection()) {
                active.set(connection);
                if (expired.get()) throw new IllegalStateException("Publication expired");
                try (var channel = connection.createChannel()) {
                  AtomicBoolean returned = new AtomicBoolean();
                  channel.addReturnListener(
                      (replyCode, replyText, exchange, routingKey, properties, body) ->
                          returned.set(true));
                  channel.confirmSelect();
                  var properties =
                      new AMQP.BasicProperties.Builder()
                          .deliveryMode(2)
                          .contentType("application/json")
                          .messageId(event.id().toString())
                          .type(event.type())
                          .correlationId(event.requestId())
                          .build();
                  channel.basicPublish("events", event.type(), true, properties, payload);
                  if (!channel.waitForConfirms(2000) || returned.get())
                    throw new IllegalStateException("Publication was not confirmed and routed");
                }
              } catch (Exception e) {
                throw new CompletionException(e);
              }
            });
    try {
      future.get(5, TimeUnit.SECONDS);
    } catch (TimeoutException e) {
      expired.set(true);
      var connection = active.get();
      if (connection != null) connection.abort();
      future.cancel(true);
      throw e;
    } catch (ExecutionException e) {
      throw new IllegalStateException("Broker publication failed", e);
    } catch (InterruptedException e) {
      expired.set(true);
      var connection = active.get();
      if (connection != null) connection.abort();
      future.cancel(true);
      Thread.currentThread().interrupt();
      throw e;
    } finally {
      executor.shutdownNow();
    }
  }
}
