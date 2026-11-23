package com.taskmanagement.task.comments;
import com.taskmanagement.task.access.*;
import com.taskmanagement.task.board.BoardService;
import com.taskmanagement.task.tasks.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;
import static org.junit.jupiter.api.Assertions.*;
@SpringBootTest @Testcontainers
class CommentServiceIT {
    @Container static final PostgreSQLContainer DB=new PostgreSQLContainer("postgres:17.6");
    @DynamicPropertySource static void props(DynamicPropertyRegistry r) { r.add("spring.datasource.url",DB::getJdbcUrl);r.add("spring.datasource.username",DB::getUsername);r.add("spring.datasource.password",DB::getPassword);r.add("task.cursor-secret",()->"a-test-secret-with-at-least-32-characters"); }
    @Autowired CommentService comments; @Autowired TaskService tasks; @Autowired BoardService boards; @Autowired JdbcTemplate db; @Autowired ObjectMapper mapper; @Autowired TaskCursor taskCursor;
    @MockitoBean WorkspaceAccessClient identity;
    final UUID workspace=UUID.randomUUID(),actor=UUID.randomUUID(),actorGen=UUID.randomUUID(),member=UUID.randomUUID(),memberGen=UUID.randomUUID(); final String requestId=UUID.randomUUID().toString();
    WorkspaceAccess access(boolean includeMember,String actorName,String memberName) {
        var user=new WorkspaceAccess.User(actor,actorName); var all=new ArrayList<WorkspaceAccess.Member>(); all.add(new WorkspaceAccess.Member(actorGen,user,WorkspaceAccess.Role.owner,Instant.EPOCH));
        if(includeMember) all.add(new WorkspaceAccess.Member(memberGen,new WorkspaceAccess.User(member,memberName),WorkspaceAccess.Role.member,Instant.EPOCH));
        return new WorkspaceAccess(user,workspace,1,actorGen,WorkspaceAccess.Role.owner,all);
    }
    UUID board() { return boards.create(workspace,actor,UUID.randomUUID(),"Board").id(); }
    UUID task(WorkspaceAccess a,UUID board) { return tasks.create(a,"Bearer test",requestId,UUID.randomUUID(),new TaskInput(board,"Task","",null,null,null)).id(); }
    CommentInput input() { return new CommentInput("😀 @Sam @Sam @Me",List.of(new CommentInput.Mention(member,2,4),new CommentInput.Mention(member,7,4),new CommentInput.Mention(actor,12,3))); }
    @Test void replayEventsAndHistoricalSnapshots() throws Exception {
        var a=access(true,"Author","Sam"); UUID board=board(),task=task(a,board),key=UUID.randomUUID(); CommentView created=comments.create(a,task,key,input(),requestId);
        assertEquals(created,comments.create(a,task,key,input(),requestId)); assertEquals(3,created.mentions().size()); assertEquals(1,tasks.get(a,"Bearer test",requestId,task).version());
        assertEquals("IDEMPOTENCY_CONFLICT",assertThrows(AccessFailure.class,()->comments.create(a,task,key,new CommentInput("changed",List.of()),requestId)).code());
        var eventStrings=db.queryForList("SELECT payload::text FROM task_service.outbox WHERE payload->>'aggregate_id'=?",String.class,created.id().toString()); assertEquals(1,eventStrings.size());
        com.taskmanagement.task.outbox.EventSchemaAssertions.valid(eventStrings.getFirst());
        var event=mapper.readTree(eventStrings.getFirst()); UUID.fromString(event.get("id").asString()); Instant.parse(event.get("occurred_at").asString());
        var expected=mapper.readTree(mapper.writeValueAsString(Map.of("id",event.get("id").asString(),"type","task.comment_mentioned.v1","schema_version",1,"producer","task","occurred_at",event.get("occurred_at").asString(),"request_id",requestId,"aggregate_id",created.id().toString(),"aggregate_version",1,"data",Map.of("workspace_id",workspace.toString(),"task_id",task.toString(),"comment_id",created.id().toString(),"actor",Map.of("id",actor.toString(),"display_name","Author"),"recipient_id",member.toString(),"recipient_membership_id",memberGen.toString())))); assertEquals(expected,event);
        var changed=access(false,"Renamed","none"); assertEquals(created,comments.list(changed,task,20,null).items().getFirst()); assertEquals(created,comments.create(changed,task,key,input(),requestId));
        assertEquals(422,assertThrows(AccessFailure.class,()->comments.create(changed,task,UUID.randomUUID(),input(),requestId)).status());
        db.update("INSERT INTO task_service.membership_tombstone(workspace_id,membership_id) VALUES (?,?)",workspace,memberGen);
        assertEquals(422,assertThrows(AccessFailure.class,()->comments.create(a,task,UUID.randomUUID(),input(),requestId)).status());
    }
    @Test void rollbackAndDistinctRecipientAtomicity() {
        var a=access(true,"Author","Sam"); UUID board=board(),task=task(a,board),key=UUID.randomUUID();
        db.execute("CREATE FUNCTION task_service.fail_comment_event_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'rollback test'; END $$"); db.execute("CREATE TRIGGER fail_comment_event_test BEFORE INSERT ON task_service.outbox FOR EACH ROW EXECUTE FUNCTION task_service.fail_comment_event_test()");
        try { assertThrows(RuntimeException.class,()->comments.create(a,task,key,input(),requestId)); } finally { db.execute("DROP TRIGGER fail_comment_event_test ON task_service.outbox"); db.execute("DROP FUNCTION task_service.fail_comment_event_test()"); }
        assertEquals(0,db.queryForObject("SELECT count(*) FROM task_service.comment WHERE task_id=?",Integer.class,task)); assertEquals(0,db.queryForObject("SELECT count(*) FROM task_service.comment_create_key WHERE actor_id=?",Integer.class,actor));
        comments.create(a,task,key,input(),requestId); assertEquals(1,db.queryForObject("SELECT count(*) FROM task_service.comment WHERE task_id=?",Integer.class,task));
    }
    @Test void concurrentReplayAndScopedCursors() throws Exception {
        var a=access(true,"Author","Sam"); UUID board=board(),task=task(a,board),other=task(a,board),key=UUID.randomUUID(); var pool=Executors.newFixedThreadPool(2); var start=new CountDownLatch(1);
        try { Callable<CommentView> create=()->{ start.await();return comments.create(a,task,key,input(),requestId); }; var first=pool.submit(create); var second=pool.submit(create); start.countDown(); assertEquals(first.get(20,TimeUnit.SECONDS),second.get(20,TimeUnit.SECONDS)); } finally { pool.shutdownNow(); }
        assertEquals(1,db.queryForObject("SELECT count(*) FROM task_service.comment WHERE task_id=?",Integer.class,task));
        comments.create(a,task,UUID.randomUUID(),new CommentInput("literal @Sam",List.of()),requestId);
        var page=comments.list(a,task,1,null); assertNotNull(page.next_cursor()); var next=comments.list(a,task,100,page.next_cursor()); assertEquals(1,next.items().size()); assertNull(next.next_cursor());
        assertEquals("INVALID_CURSOR",assertThrows(AccessFailure.class,()->comments.list(a,other,1,page.next_cursor())).code());
        assertEquals("INVALID_CURSOR",assertThrows(AccessFailure.class,()->comments.list(a,task,1,page.next_cursor()+"x")).code());
        var otherActor=new WorkspaceAccess(new WorkspaceAccess.User(member,"Sam"),workspace,1,memberGen,WorkspaceAccess.Role.member,a.members());
        assertEquals("INVALID_CURSOR",assertThrows(AccessFailure.class,()->comments.list(otherActor,task,1,page.next_cursor())).code());
        String taskToken=taskCursor.encode(actor,workspace,task,new TaskCursor.Position(Instant.now(),UUID.randomUUID())); assertEquals("INVALID_CURSOR",assertThrows(AccessFailure.class,()->comments.list(a,task,1,taskToken)).code());
        assertEquals(1,db.queryForObject("SELECT count(*) FROM task_service.outbox WHERE event_type='task.comment_mentioned.v1' AND payload->'data'->>'task_id'=?",Integer.class,task.toString()));
    }
    @Test void deletedParentsHideListReplayAndNewCreates() {
        var a=access(true,"Author","Sam"); UUID board=board(),first=task(a,board),second=task(a,board),key=UUID.randomUUID(); comments.create(a,first,key,input(),requestId);
        tasks.delete(a,"Bearer test",requestId,first,1);
        assertEquals(404,assertThrows(AccessFailure.class,()->comments.list(a,first,20,null)).status()); assertEquals(404,assertThrows(AccessFailure.class,()->comments.create(a,first,key,input(),requestId)).status()); assertEquals(404,assertThrows(AccessFailure.class,()->comments.create(a,first,UUID.randomUUID(),input(),requestId)).status());
        UUID otherKey=UUID.randomUUID(); comments.create(a,second,otherKey,input(),requestId); boards.delete(workspace,board,1);
        assertEquals(404,assertThrows(AccessFailure.class,()->comments.list(a,second,20,null)).status()); assertEquals(404,assertThrows(AccessFailure.class,()->comments.create(a,second,otherKey,input(),requestId)).status()); assertEquals(404,assertThrows(AccessFailure.class,()->comments.create(a,second,UUID.randomUUID(),input(),requestId)).status());
    }
    @Test void parentDeleteAndCreateSerialize() throws Exception {
        var a=access(true,"Author","Sam"); UUID board=board(),task=task(a,board); var pool=Executors.newFixedThreadPool(2); var start=new CountDownLatch(1);
        try { var create=pool.submit(()->{ start.await(); try { comments.create(a,task,UUID.randomUUID(),input(),requestId);return 201; } catch(AccessFailure e) { return e.status(); }}); var delete=pool.submit(()->{ start.await(); tasks.delete(a,"Bearer test",requestId,task,1);return 204; }); start.countDown(); int result=create.get(20,TimeUnit.SECONDS); assertTrue(result==201||result==404); assertEquals(204,delete.get(20,TimeUnit.SECONDS)); assertEquals(404,assertThrows(AccessFailure.class,()->comments.list(a,task,20,null)).status()); } finally { pool.shutdownNow(); }
    }
    @Test void multipleRecipientsAndKeyScopes() {
        UUID another=UUID.randomUUID(), anotherGen=UUID.randomUUID(); var original=access(true,"Author","Sam");
        var all=new ArrayList<>(original.members()); all.add(new WorkspaceAccess.Member(anotherGen,new WorkspaceAccess.User(another,"Kim"),WorkspaceAccess.Role.member,Instant.EPOCH));
        var a=new WorkspaceAccess(original.actor(),workspace,1,actorGen,WorkspaceAccess.Role.owner,all);
        UUID board=board(), first=task(a,board), second=task(a,board), key=UUID.randomUUID();
        var mentions=new CommentInput("@Sam @Kim",List.of(new CommentInput.Mention(member,0,4),new CommentInput.Mention(another,5,4)));
        var firstComment=comments.create(a,first,key,mentions,requestId);
        assertEquals(2,db.queryForObject("SELECT count(*) FROM task_service.outbox WHERE payload->>'aggregate_id'=?",Integer.class,firstComment.id().toString()));
        assertEquals(Set.of(member.toString(),another.toString()),new HashSet<>(db.queryForList("SELECT payload->'data'->>'recipient_id' FROM task_service.outbox WHERE payload->>'aggregate_id'=?",String.class,firstComment.id().toString())));
        assertNotEquals(firstComment.id(),comments.create(a,second,key,mentions,requestId).id());
        var other=new WorkspaceAccess(new WorkspaceAccess.User(member,"Sam"),workspace,1,memberGen,WorkspaceAccess.Role.member,all);
        assertNotEquals(firstComment.id(),comments.create(other,first,key,mentions,requestId).id());
        assertEquals(3,db.queryForObject("SELECT count(*) FROM task_service.comment WHERE workspace_id=?",Integer.class,workspace));
    }
    @Test void tombstoneGenerationDoesNotBlockRejoinedMember() {
        var old=access(true,"Author","Sam"); UUID board=board(),task=task(old,board);
        db.update("INSERT INTO task_service.membership_tombstone(workspace_id,membership_id) VALUES (?,?)",workspace,memberGen);
        assertEquals(422,assertThrows(AccessFailure.class,()->comments.create(old,task,UUID.randomUUID(),input(),requestId)).status());
        UUID newGeneration=UUID.randomUUID(); var owner=old.members().getFirst();
        var rejoined=new WorkspaceAccess(old.actor(),workspace,2,actorGen,old.actorRole(),List.of(owner,new WorkspaceAccess.Member(newGeneration,new WorkspaceAccess.User(member,"New Sam"),WorkspaceAccess.Role.member,Instant.EPOCH)));
        CommentView created=comments.create(rejoined,task,UUID.randomUUID(),input(),requestId);
        assertEquals("New Sam",created.mentions().getFirst().user().display_name());
        assertEquals(newGeneration.toString(),db.queryForObject("SELECT payload->'data'->>'recipient_membership_id' FROM task_service.outbox WHERE payload->>'aggregate_id'=?",String.class,created.id().toString()));
    }

}
