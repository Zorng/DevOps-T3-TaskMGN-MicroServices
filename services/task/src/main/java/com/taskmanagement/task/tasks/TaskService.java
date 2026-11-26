package com.taskmanagement.task.tasks;

import com.taskmanagement.task.access.*;
import com.taskmanagement.task.board.BoardService;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.function.Function;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

@Service
public class TaskService {
  private final JdbcTemplate db;
  private final BoardService boards;
  private final WorkspaceAccessClient identity;
  private final TransactionTemplate tx;
  private final ObjectMapper mapper;
  private final TaskCursor cursors;

  public TaskService(
      JdbcTemplate db,
      BoardService boards,
      WorkspaceAccessClient identity,
      TransactionTemplate tx,
      ObjectMapper mapper,
      TaskCursor cursors) {
    this.db = db;
    this.boards = boards;
    this.identity = identity;
    this.tx = tx;
    this.mapper = mapper;
    this.cursors = cursors;
  }

  private record Row(TaskView view, UUID generation, Long workspaceVersion) {}

  public record Page(List<TaskView> items, String next_cursor) {}

  private static class Refresh extends RuntimeException {}

  private Row row(ResultSet r, int n) throws SQLException {
    UUID assignee = r.getObject("assignee_id", UUID.class);
    var v =
        new TaskView(
            r.getObject("id", UUID.class),
            r.getObject("workspace_id", UUID.class),
            r.getObject("board_id", UUID.class),
            r.getLong("number"),
            r.getString("title"),
            r.getString("description"),
            assignee == null ? null : new TaskView.User(assignee, r.getString("assignee_name")),
            r.getObject("start_date", LocalDate.class),
            r.getObject("due_date", LocalDate.class),
            new TaskView.User(r.getObject("creator_id", UUID.class), r.getString("creator_name")),
            r.getLong("version"),
            r.getTimestamp("created_at").toInstant(),
            r.getTimestamp("updated_at").toInstant());
    return new Row(
        v,
        r.getObject("assignee_membership_id", UUID.class),
        (Long) r.getObject("assignee_workspace_version"));
  }

  private <T> T run(
      WorkspaceAccess initial,
      String authorization,
      String requestId,
      Function<WorkspaceAccess, T> action) {
    WorkspaceAccess snapshot = initial;
    for (int attempt = 0; attempt < 3; attempt++) {
      WorkspaceAccess current = snapshot;
      try {
        return tx.execute(
            status -> {
              lock(current);
              return action.apply(current);
            });
      } catch (Refresh stale) {
        if (attempt == 2) throw new AccessFailure(503, "DEPENDENCY_UNAVAILABLE");
        snapshot = identity.check(initial.workspaceId(), authorization, requestId);
      }
    }
    throw new AccessFailure(503, "DEPENDENCY_UNAVAILABLE");
  }

  private void lock(WorkspaceAccess a) {
    boards.lockWorkspace(a.workspaceId());
    if (Boolean.TRUE.equals(
        db.queryForObject(
            "SELECT EXISTS(SELECT 1 FROM task_service.workspace_tombstone WHERE workspace_id=?) OR EXISTS(SELECT 1 FROM task_service.membership_tombstone WHERE workspace_id=? AND membership_id=?)",
            Boolean.class,
            a.workspaceId(),
            a.workspaceId(),
            a.actorMembershipId()))) throw new AccessFailure(404, "NOT_FOUND");
  }

  private Row find(UUID workspace, UUID id, boolean lock) {
    var result =
        db.query(
            "SELECT t.* FROM task_service.task t JOIN task_service.board b ON b.id=t.board_id AND b.workspace_id=t.workspace_id AND b.deleted_at IS NULL WHERE t.workspace_id=? AND t.id=? AND t.deleted_at IS NULL"
                + (lock ? " FOR UPDATE OF t" : ""),
            this::row,
            workspace,
            id);
    if (result.isEmpty()) throw new AccessFailure(404, "NOT_FOUND");
    return result.getFirst();
  }

  /**
   * Within a caller transaction, acquire workspace synchronization then validate active parent and
   * task.
   */
  public TaskView lockVisible(WorkspaceAccess access, UUID task) {
    lock(access);
    return find(access.workspaceId(), task, true).view();
  }

  private WorkspaceAccess.Member assignee(WorkspaceAccess a, UUID id) {
    if (id == null) return null;
    var member =
        a.members().stream()
            .filter(m -> m.user().id().equals(id))
            .findFirst()
            .orElseThrow(() -> new AccessFailure(422, "VALIDATION_FAILED"));
    if (Boolean.TRUE.equals(
        db.queryForObject(
            "SELECT EXISTS(SELECT 1 FROM task_service.membership_tombstone WHERE workspace_id=? AND membership_id=?)",
            Boolean.class,
            a.workspaceId(),
            member.membershipId()))) throw new AccessFailure(422, "VALIDATION_FAILED");
    return member;
  }

  private TaskView reconcile(Row row, WorkspaceAccess access) {
    if (row.generation() == null) return row.view();
    if (access.workspaceVersion() < row.workspaceVersion()) throw new Refresh();
    boolean active =
        access.members().stream()
            .anyMatch(
                m ->
                    m.membershipId().equals(row.generation())
                        && m.user().id().equals(row.view().assignee().id()));
    boolean tombstone =
        Boolean.TRUE.equals(
            db.queryForObject(
                "SELECT EXISTS(SELECT 1 FROM task_service.membership_tombstone WHERE workspace_id=? AND membership_id=?)",
                Boolean.class,
                access.workspaceId(),
                row.generation()));
    if (active && !tombstone) return row.view();
    Instant now = Instant.now().truncatedTo(ChronoUnit.MILLIS);
    int updated =
        db.update(
            "UPDATE task_service.task SET assignee_id=NULL, assignee_name=NULL, assignee_membership_id=NULL, assignee_workspace_version=NULL, version=version+1, updated_at=? WHERE id=? AND version=? AND assignee_membership_id=?",
            Timestamp.from(now),
            row.view().id(),
            row.view().version(),
            row.generation());
    if (updated != 1) throw new Refresh();
    return find(access.workspaceId(), row.view().id(), false).view();
  }

  private void boardForDestination(UUID workspace, UUID board) {
    try {
      boards.lockVisible(workspace, board);
    } catch (AccessFailure e) {
      if (e.status() == 404)
        throw new AccessFailure(422, "VALIDATION_FAILED", "board_id", "Board is unavailable.");
      throw e;
    }
  }

  private Instant now() {
    return Instant.now().truncatedTo(ChronoUnit.MILLIS);
  }

  private void emit(
      WorkspaceAccess a, WorkspaceAccess.Member recipient, TaskView task, String requestId) {
    if (recipient == null || recipient.user().id().equals(a.actor().id())) return;
    UUID id = UUID.randomUUID();
    Instant time = now();
    Map<String, Object> event =
        Map.of(
            "id",
            id,
            "type",
            "task.assigned.v1",
            "schema_version",
            1,
            "producer",
            "task",
            "occurred_at",
            time.toString(),
            "request_id",
            requestId,
            "aggregate_id",
            task.id(),
            "aggregate_version",
            task.version(),
            "data",
            Map.of(
                "workspace_id",
                a.workspaceId(),
                "task_id",
                task.id(),
                "actor",
                Map.of("id", a.actor().id(), "display_name", a.actor().displayName()),
                "recipient_id",
                recipient.user().id(),
                "recipient_membership_id",
                recipient.membershipId()));
    db.update(
        "INSERT INTO task_service.outbox(id,event_type,payload,created_at) VALUES (?, 'task.assigned.v1', CAST(? AS jsonb), ?)",
        id,
        mapper.writeValueAsString(event),
        Timestamp.from(time));
  }

  public TaskView create(
      WorkspaceAccess access, String auth, String requestId, UUID key, TaskInput input) {
    String fingerprint = mapper.writeValueAsString(input);
    return run(
        access,
        auth,
        requestId,
        a -> {
          String path = "/api/v1/workspaces/" + a.workspaceId() + "/tasks";
          db.update(
              "DELETE FROM task_service.task_create_key WHERE actor_id=? AND path=? AND key=? AND expires_at<now()",
              a.actor().id(),
              path,
              key);
          var existing =
              db.query(
                  "SELECT task_id,fingerprint,response::text FROM task_service.task_create_key WHERE actor_id=? AND path=? AND key=?",
                  (r, n) -> new String[] {r.getString(1), r.getString(2), r.getString(3)},
                  a.actor().id(),
                  path,
                  key);
          if (!existing.isEmpty()) {
            var e = existing.getFirst();
            find(a.workspaceId(), UUID.fromString(e[0]), false);
            if (!fingerprint.equals(e[1])) throw new AccessFailure(409, "IDEMPOTENCY_CONFLICT");
            return mapper.readValue(e[2], TaskView.class);
          }
          boardForDestination(a.workspaceId(), input.board());
          var member = assignee(a, input.assignee());
          long number =
              db.queryForObject(
                  "INSERT INTO task_service.task_number(workspace_id,last_number) VALUES (?,1) ON CONFLICT(workspace_id) DO UPDATE SET last_number=task_service.task_number.last_number+1 RETURNING last_number",
                  Long.class,
                  a.workspaceId());
          UUID id = UUID.randomUUID();
          Instant time = now();
          db.update(
              "INSERT INTO task_service.task(id,workspace_id,board_id,number,title,description,creator_id,creator_name,assignee_id,assignee_name,assignee_membership_id,assignee_workspace_version,start_date,due_date,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
              id,
              a.workspaceId(),
              input.board(),
              number,
              input.title(),
              input.description(),
              a.actor().id(),
              a.actor().displayName(),
              member == null ? null : member.user().id(),
              member == null ? null : member.user().displayName(),
              member == null ? null : member.membershipId(),
              member == null ? null : a.workspaceVersion(),
              input.start(),
              input.due(),
              Timestamp.from(time),
              Timestamp.from(time));
          TaskView result = find(a.workspaceId(), id, false).view();
          emit(a, member, result, requestId);
          db.update(
              "INSERT INTO task_service.task_create_key(actor_id,path,key,fingerprint,task_id,response,expires_at) VALUES (?,?,?,?,?,CAST(? AS jsonb),now()+interval '25 hours')",
              a.actor().id(),
              path,
              key,
              fingerprint,
              id,
              mapper.writeValueAsString(result));
          return result;
        });
  }

  public TaskView get(WorkspaceAccess access, String auth, String requestId, UUID id) {
    return run(access, auth, requestId, a -> reconcile(find(a.workspaceId(), id, true), a));
  }

  public Page list(
      WorkspaceAccess access, String auth, String requestId, UUID board, int limit, String cursor) {
    cursors.requireConfigured();
    TaskCursor.Position position =
        cursor == null
            ? null
            : cursors.decode(cursor, access.actor().id(), access.workspaceId(), board);
    return run(
        access,
        auth,
        requestId,
        a -> {
          if (board != null) {
            try {
              boards.visible(a.workspaceId(), board);
            } catch (AccessFailure e) {
              throw new AccessFailure(404, "NOT_FOUND");
            }
          }
          String sql =
              "SELECT t.* FROM task_service.task t JOIN task_service.board b ON b.id=t.board_id AND b.workspace_id=t.workspace_id AND b.deleted_at IS NULL WHERE t.workspace_id=? AND t.deleted_at IS NULL"
                  + (board == null ? "" : " AND t.board_id=?")
                  + (position == null ? "" : " AND (t.created_at,t.id)<(?,?)")
                  + " ORDER BY t.created_at DESC,t.id DESC LIMIT ? FOR UPDATE OF t";
          List<Object> args = new ArrayList<>();
          args.add(a.workspaceId());
          if (board != null) args.add(board);
          if (position != null) {
            args.add(Timestamp.from(position.created()));
            args.add(position.id());
          }
          args.add(limit + 1);
          List<Row> rows = db.query(sql, this::row, args.toArray());
          boolean more = rows.size() > limit;
          List<TaskView> items = new ArrayList<>();
          for (Row row : rows.subList(0, Math.min(rows.size(), limit)))
            items.add(reconcile(row, a));
          TaskView last = items.isEmpty() ? null : items.getLast();
          String next =
              more
                  ? cursors.encode(
                      a.actor().id(),
                      a.workspaceId(),
                      board,
                      new TaskCursor.Position(last.created_at(), last.id()))
                  : null;
          return new Page(items, next);
        });
  }

  public TaskView patch(
      WorkspaceAccess access,
      String auth,
      String requestId,
      UUID id,
      long version,
      JsonNode patch) {
    return run(
        access,
        auth,
        requestId,
        a -> {
          Row oldRow = find(a.workspaceId(), id, true);
          TaskView old = reconcile(oldRow, a);
          if (old.version() != version) throw new AccessFailure(412, "VERSION_CONFLICT");
          TaskInput input = TaskValidation.merge(patch, old);
          if (!input.board().equals(old.board_id()))
            boardForDestination(a.workspaceId(), input.board());
          WorkspaceAccess.Member member = assignee(a, input.assignee());
          UUID oldAssignee = old.assignee() == null ? null : old.assignee().id();
          boolean changed =
              !input.board().equals(old.board_id())
                  || !input.title().equals(old.title())
                  || !input.description().equals(old.description())
                  || !Objects.equals(input.assignee(), oldAssignee)
                  || !Objects.equals(input.start(), old.start_date())
                  || !Objects.equals(input.due(), old.due_date());
          if (!changed) return old;
          boolean assignmentChanged = !Objects.equals(input.assignee(), oldAssignee);
          db.update(
              "UPDATE task_service.task SET board_id=?,title=?,description=?,assignee_id=?,assignee_name=?,assignee_membership_id=?,assignee_workspace_version=?,start_date=?,due_date=?,version=version+1,updated_at=? WHERE id=?",
              input.board(),
              input.title(),
              input.description(),
              member == null ? null : member.user().id(),
              member == null
                  ? null
                  : (assignmentChanged
                      ? member.user().displayName()
                      : old.assignee().display_name()),
              member == null ? null : member.membershipId(),
              member == null
                  ? null
                  : (assignmentChanged ? a.workspaceVersion() : oldRow.workspaceVersion()),
              input.start(),
              input.due(),
              Timestamp.from(now()),
              id);
          TaskView result = find(a.workspaceId(), id, false).view();
          if (assignmentChanged) emit(a, member, result, requestId);
          return result;
        });
  }

  public void delete(WorkspaceAccess access, String auth, String requestId, UUID id, long version) {
    run(
        access,
        auth,
        requestId,
        a -> {
          TaskView old = reconcile(find(a.workspaceId(), id, true), a);
          if (old.version() != version) throw new AccessFailure(412, "VERSION_CONFLICT");
          db.update("UPDATE task_service.task SET deleted_at=now() WHERE id=?", id);
          return null;
        });
  }
}
