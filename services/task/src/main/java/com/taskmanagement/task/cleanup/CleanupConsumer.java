package com.taskmanagement.task.cleanup;
import com.rabbitmq.client.*;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.slf4j.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.*;
import org.springframework.stereotype.Component;

@Component @EnableScheduling
@ConditionalOnProperty(name="task.cleanup.enabled",havingValue="true")
public class CleanupConsumer {
    private static final Logger LOG=LoggerFactory.getLogger(CleanupConsumer.class);
    private static final String QUEUE="task-cleanup.v1";
    private static final String[] DELAYS={"5s","30s","2m","10m","30m"};
    private final ConnectionFactory factory; private final CleanupService service; private final CleanupValidator validator; private final MeterRegistry metrics;
    private volatile long backoffUntil;
    public CleanupConsumer(@Value("${task.broker.host}") String host,@Value("${task.broker.port}") int port,@Value("${task.broker.vhost}") String vhost,
        @Value("${task.broker.username}") String user,@Value("${task.broker.password}") String password,@Value("${task.broker.tls:false}") boolean tls,
        CleanupService service,CleanupValidator validator,MeterRegistry metrics) throws Exception {
        if(host.isBlank() || vhost.isBlank() || user.isBlank() || password.isBlank()) throw new IllegalArgumentException("Broker configuration is incomplete");
        factory=new ConnectionFactory(); factory.setHost(host);factory.setPort(port);factory.setVirtualHost(vhost);factory.setUsername(user);factory.setPassword(password);
        factory.setConnectionTimeout(2000);factory.setHandshakeTimeout(2000);factory.setChannelRpcTimeout(2000);factory.setShutdownTimeout(1000);factory.setAutomaticRecoveryEnabled(false);
        if(tls) { factory.useSslProtocol(javax.net.ssl.SSLContext.getDefault()); factory.enableHostnameVerification(); }
        this.service=service;this.validator=validator;this.metrics=metrics;
    }
    static int attempt(AMQP.BasicProperties properties) {
        Object raw=properties.getHeaders()==null?null:properties.getHeaders().get("x-attempt");
        if(raw==null) return 1;
        if(!(raw instanceof Number number) || number.longValue()<1 || number.longValue()>6 || number.doubleValue()!=number.longValue()) throw new CleanupValidator.InvalidEvent();
        return number.intValue();
    }
    private void forward(Channel channel,GetResponse delivery,String exchange,String route,int attempt) throws Exception {
        AtomicBoolean returned=new AtomicBoolean(); channel.addReturnListener((code,text,ex,key,props,body)->returned.set(true)); channel.confirmSelect();
        var properties=delivery.getProps(); var headers=new HashMap<String,Object>(); if(properties.getHeaders()!=null) headers.putAll(properties.getHeaders()); headers.put("x-attempt",attempt);
        channel.basicPublish(exchange,route,true,properties.builder().headers(headers).deliveryMode(2).build(),delivery.getBody());
        if(!channel.waitForConfirms(2000) || returned.get()) throw new IllegalStateException("Forward not confirmed and routed");
    }
    /** Process exactly one delivery. Failure to forward or acknowledge is propagated so closing the channel requeues it. */
    public boolean processOne(Channel channel) throws Exception {
        channel.basicQos(1); GetResponse delivery=channel.basicGet(QUEUE,false); if(delivery==null) return false;
        CleanupEvent event;
        try {
            event=validator.validate(delivery.getBody(),delivery.getProps(),delivery.getEnvelope().getExchange(),delivery.getEnvelope().getRoutingKey());
            attempt(delivery.getProps());
        } catch(CleanupValidator.InvalidEvent e) {
            forward(channel,delivery,"dead-letter",QUEUE,1); channel.basicAck(delivery.getEnvelope().getDeliveryTag(),false);
            metrics.counter("task.cleanup.malformed").increment(); LOG.warn("Rejected malformed cleanup event"); return true;
        }
        int attempt=attempt(delivery.getProps());
        boolean applied;
        try { applied=service.apply(event); }
        catch(RuntimeException e) {
            metrics.counter("task.cleanup.failures").increment();
            String exchange=attempt<=5?"retry":"dead-letter",route=attempt<=5?QUEUE+".retry."+DELAYS[attempt-1]:QUEUE;
            forward(channel,delivery,exchange,route,Math.min(6,attempt+1)); channel.basicAck(delivery.getEnvelope().getDeliveryTag(),false);
            metrics.counter(attempt<=5?"task.cleanup.retries":"task.cleanup.deadletter").increment();
            LOG.warn("Cleanup deferred event_id={} request_id={} type={} attempt={}",event.id(),event.requestId(),event.type(),attempt);
            return true;
        }
        channel.basicAck(delivery.getEnvelope().getDeliveryTag(),false);
        metrics.counter(applied?"task.cleanup.consumed":"task.cleanup.duplicate").increment();
        LOG.info("Cleanup event processed event_id={} request_id={} type={} duplicate={}",event.id(),event.requestId(),event.type(),!applied);
        return true;
    }
    @Scheduled(fixedDelayString="${task.cleanup.poll-ms:1000}")
    public void poll() {
        if(System.currentTimeMillis()<backoffUntil) return;
        AtomicReference<Connection> active=new AtomicReference<>(); AtomicBoolean expired=new AtomicBoolean();
        ExecutorService executor=Executors.newSingleThreadExecutor(r->{Thread t=new Thread(r,"task-cleanup-consumer");t.setDaemon(true);return t;});
        Future<?> operation=executor.submit(()->{
            try(Connection connection=factory.newConnection()) {
                active.set(connection); if(expired.get()) throw new IllegalStateException("Consumer poll expired");
                try(Channel channel=connection.createChannel()) { processOne(channel); }
            } catch(Exception e) { throw new CompletionException(e); }
        });
        try { operation.get(10,TimeUnit.SECONDS); }
        catch(Exception e) {
            expired.set(true); Connection connection=active.get(); if(connection!=null) connection.abort(); operation.cancel(true);
            backoffUntil=System.currentTimeMillis()+5000; metrics.counter("task.cleanup.transport.failures").increment(); LOG.warn("Cleanup broker operation failed; delivery retained for retry");
            if(e instanceof InterruptedException) Thread.currentThread().interrupt();
        } finally { executor.shutdownNow(); }
    }
}
