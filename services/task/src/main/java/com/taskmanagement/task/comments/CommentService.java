package com.taskmanagement.task.comments;
import com.taskmanagement.task.access.*;
import com.taskmanagement.task.tasks.TaskService;
import java.sql.*;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.ObjectMapper;
@Service
public class CommentService {
    private final JdbcTemplate db; private final TaskService tasks; private final TransactionTemplate tx; private final ObjectMapper mapper; private final CommentCursor cursors;
    public record Page(List<CommentView> items,String next_cursor) {}
    public CommentService(JdbcTemplate db,TaskService tasks,TransactionTemplate tx,ObjectMapper mapper,CommentCursor cursors) { this.db=db;this.tasks=tasks;this.tx=tx;this.mapper=mapper;this.cursors=cursors; }
    private CommentView row(ResultSet r,int n) throws SQLException {
        UUID id=r.getObject("id",UUID.class);
        var mentions=db.query("SELECT * FROM task_service.comment_mention WHERE comment_id=? ORDER BY position",(m,i)->new CommentView.Mention(new CommentView.User(m.getObject("user_id",UUID.class),m.getString("display_name")),m.getInt("start_offset"),m.getInt("length")),id);
        return new CommentView(id,r.getObject("workspace_id",UUID.class),r.getObject("task_id",UUID.class),r.getString("body"),new CommentView.User(r.getObject("author_id",UUID.class),r.getString("author_name")),mentions,r.getTimestamp("created_at").toInstant());
    }
    private WorkspaceAccess.Member member(WorkspaceAccess access,UUID id) {
        var member=access.members().stream().filter(m->m.user().id().equals(id)).findFirst().orElseThrow(()->new AccessFailure(422,"VALIDATION_FAILED","mentions","Mentioned member is unavailable."));
        if(Boolean.TRUE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM task_service.membership_tombstone WHERE workspace_id=? AND membership_id=?)",Boolean.class,access.workspaceId(),member.membershipId()))) throw new AccessFailure(422,"VALIDATION_FAILED","mentions","Mentioned member is unavailable.");
        return member;
    }
    private void emit(WorkspaceAccess access,UUID task,UUID comment,WorkspaceAccess.Member recipient,String requestId,Instant time) {
        UUID id=UUID.randomUUID();
        var data=Map.of("workspace_id",access.workspaceId(),"task_id",task,"comment_id",comment,"actor",Map.of("id",access.actor().id(),"display_name",access.actor().displayName()),"recipient_id",recipient.user().id(),"recipient_membership_id",recipient.membershipId());
        Map<String,Object> event=Map.of("id",id,"type","task.comment_mentioned.v1","schema_version",1,"producer","task","occurred_at",time.toString(),"request_id",requestId,"aggregate_id",comment,"aggregate_version",1,"data",data);
        db.update("INSERT INTO task_service.outbox(id,event_type,payload,created_at) VALUES (?,'task.comment_mentioned.v1',CAST(? AS jsonb),?)",id,mapper.writeValueAsString(event),Timestamp.from(time));
    }
    public CommentView create(WorkspaceAccess access,UUID task,UUID key,CommentInput input,String requestId) {
        return tx.execute(status->{
            tasks.lockVisible(access,task);
            String path="/api/v1/workspaces/"+access.workspaceId()+"/tasks/"+task+"/comments", fingerprint=mapper.writeValueAsString(input);
            db.update("DELETE FROM task_service.comment_create_key WHERE actor_id=? AND path=? AND key=? AND expires_at<now()",access.actor().id(),path,key);
            var existing=db.query("SELECT fingerprint,response::text FROM task_service.comment_create_key WHERE actor_id=? AND path=? AND key=?",(r,n)->new String[]{r.getString(1),r.getString(2)},access.actor().id(),path,key);
            if(!existing.isEmpty()) { var e=existing.getFirst(); if(!e[0].equals(fingerprint)) throw new AccessFailure(409,"IDEMPOTENCY_CONFLICT"); return mapper.readValue(e[1],CommentView.class); }
            var selected=new LinkedHashMap<UUID,WorkspaceAccess.Member>();
            for(var mention:input.mentions()) if(!selected.containsKey(mention.user_id())) selected.put(mention.user_id(),member(access,mention.user_id()));
            UUID id=UUID.randomUUID(); Instant time=Instant.now().truncatedTo(ChronoUnit.MILLIS);
            db.update("INSERT INTO task_service.comment(id,workspace_id,task_id,body,author_id,author_name,created_at) VALUES (?,?,?,?,?,?,?)",id,access.workspaceId(),task,input.body(),access.actor().id(),access.actor().displayName(),Timestamp.from(time));
            List<CommentView.Mention> mentions=new ArrayList<>(); int position=0;
            for(var mention:input.mentions()) { var m=selected.get(mention.user_id()); db.update("INSERT INTO task_service.comment_mention(comment_id,position,user_id,display_name,membership_id,start_offset,length) VALUES (?,?,?,?,?,?,?)",id,position++,m.user().id(),m.user().displayName(),m.membershipId(),mention.start(),mention.length()); mentions.add(new CommentView.Mention(new CommentView.User(m.user().id(),m.user().displayName()),mention.start(),mention.length())); }
            var result=new CommentView(id,access.workspaceId(),task,input.body(),new CommentView.User(access.actor().id(),access.actor().displayName()),mentions,time);
            for(var m:selected.values()) if(!m.user().id().equals(access.actor().id())) emit(access,task,id,m,requestId,time);
            db.update("INSERT INTO task_service.comment_create_key(actor_id,path,key,fingerprint,comment_id,response,expires_at) VALUES (?,?,?,?,?,CAST(? AS jsonb),now()+interval '25 hours')",access.actor().id(),path,key,fingerprint,id,mapper.writeValueAsString(result)); return result;
        });
    }
    public Page list(WorkspaceAccess access,UUID task,int limit,String cursor) {
        cursors.requireConfigured(); CommentCursor.Position position=cursor==null?null:cursors.decode(cursor,access.actor().id(),access.workspaceId(),task);
        return tx.execute(status->{ tasks.lockVisible(access,task);
            String sql="SELECT * FROM task_service.comment WHERE workspace_id=? AND task_id=?"+(position==null?"":" AND (created_at,id)>(?,?)")+" ORDER BY created_at,id LIMIT ?";
            List<Object> args=new ArrayList<>(List.of(access.workspaceId(),task)); if(position!=null) { args.add(Timestamp.from(position.created())); args.add(position.id()); } args.add(limit+1);
            var rows=db.query(sql,this::row,args.toArray()); boolean more=rows.size()>limit; var items=List.copyOf(rows.subList(0,Math.min(rows.size(),limit))); var last=items.isEmpty()?null:items.getLast();
            return new Page(items,more?cursors.encode(access.actor().id(),access.workspaceId(),task,new CommentCursor.Position(last.created_at(),last.id())):null);
        });
    }
}
