package com.taskmanagement.task.outbox;

import io.micrometer.core.instrument.MeterRegistry;
import java.time.Instant;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicLong;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.ObjectMapper;

@Component
@EnableScheduling
@ConditionalOnProperty(name="task.outbox.enabled",havingValue="true")
public class OutboxPublisher {
    private static final Logger LOG=LoggerFactory.getLogger(OutboxPublisher.class);
    private final JdbcTemplate db; private final TransactionTemplate tx; private final EventTransport transport; private final ObjectMapper mapper; private final MeterRegistry metrics;
    private final AtomicLong pending=new AtomicLong(), oldestSeconds=new AtomicLong();
    public OutboxPublisher(JdbcTemplate db,TransactionTemplate tx,EventTransport transport,ObjectMapper mapper,MeterRegistry metrics) {
        this.db=db;this.tx=tx;this.transport=transport;this.mapper=mapper;this.metrics=metrics;
        metrics.gauge("task.outbox.pending",pending); metrics.gauge("task.outbox.oldest.seconds",oldestSeconds);
    }
    static double delaySeconds(int failures) {
        double base=Math.min(60,Math.scalb(1.0,Math.min(Math.max(0,failures-1),6)));
        if (base==60) return ThreadLocalRandom.current().nextDouble(50.0,60.0);
        return Math.min(60,base*ThreadLocalRandom.current().nextDouble(1.0,1.2));
    }
    /** One row lock spans a bounded publication and its database update. Crash rolls back the lock/update. */
    public boolean publishOne() {
        Boolean processed=tx.execute(status->{
            var rows=db.query("SELECT id,event_type,payload::text,attempts FROM task_service.outbox WHERE published_at IS NULL AND next_attempt_at<=now() ORDER BY next_attempt_at,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1",(r,n)->new OutboxEvent(r.getObject(1,java.util.UUID.class),r.getString(2),r.getString(3),mapper.readTree(r.getString(3)).path("request_id").asString(),r.getInt(4)));
            if(rows.isEmpty()) return false;
            OutboxEvent event=rows.getFirst();
            try {
                transport.publish(event);
                db.update("UPDATE task_service.outbox SET published_at=now() WHERE id=?",event.id());
                afterCommit(()->{ metrics.counter("task.outbox.published").increment(); LOG.info("Published event_id={} request_id={} type={}",event.id(),event.requestId(),event.type()); });
            } catch(Exception e) {
                int failures=event.attempts()==Integer.MAX_VALUE?Integer.MAX_VALUE:event.attempts()+1;
                db.update("UPDATE task_service.outbox SET attempts=?,next_attempt_at=now()+(? * interval '1 second') WHERE id=?",failures,delaySeconds(failures),event.id());
                afterCommit(()->{ metrics.counter("task.outbox.publish.failures").increment(); LOG.warn("Publication failed event_id={} request_id={} type={} attempts={}",event.id(),event.requestId(),event.type(),failures); });
            }
            return true;
        });
        return Boolean.TRUE.equals(processed);
    }
    private void afterCommit(Runnable action) { TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() { @Override public void afterCommit() { action.run(); } }); }
    public void refreshMetrics() {
        db.query("SELECT count(*), COALESCE(EXTRACT(EPOCH FROM (now()-min(created_at))),0) FROM task_service.outbox WHERE published_at IS NULL",r->{ if(r.next()) { pending.set(r.getLong(1)); oldestSeconds.set(Math.max(0,r.getLong(2))); } return null; });
        if(oldestSeconds.get()>300) LOG.warn("Oldest pending Task outbox event exceeds five minutes age_seconds={} pending={}",oldestSeconds.get(),pending.get());
    }
    @Scheduled(fixedDelayString="${task.outbox.poll-ms:1000}")
    public void poll() {
        try { for(int i=0;i<25 && publishOne();i++) {} refreshMetrics(); }
        catch(RuntimeException e) { LOG.warn("Task outbox poll failed; pending records remain for retry"); }
    }
}
