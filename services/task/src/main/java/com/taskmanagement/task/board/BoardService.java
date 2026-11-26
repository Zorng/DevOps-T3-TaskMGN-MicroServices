package com.taskmanagement.task.board;

import com.taskmanagement.task.access.AccessFailure;
import com.taskmanagement.task.access.WorkspaceAccess;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class BoardService {
  private final JdbcTemplate db;

  public BoardService(JdbcTemplate db) {
    this.db = db;
  }

  private Board board(ResultSet r, int row) throws SQLException {
    return new Board(
        r.getObject("id", UUID.class),
        r.getObject("workspace_id", UUID.class),
        r.getString("name"),
        r.getLong("version"),
        r.getTimestamp("created_at").toInstant(),
        r.getTimestamp("updated_at").toInstant());
  }

  public void lockWorkspace(UUID workspace) {
    db.queryForObject(
        "SELECT pg_advisory_xact_lock(hashtextextended(?, 0)) IS NULL",
        Boolean.class,
        workspace.toString());
  }

  private void requireActive(WorkspaceAccess access) {
    lockWorkspace(access.workspaceId());
    if (Boolean.TRUE.equals(
        db.queryForObject(
            "SELECT EXISTS(SELECT 1 FROM task_service.workspace_tombstone WHERE workspace_id=?) OR EXISTS(SELECT 1 FROM task_service.membership_tombstone WHERE workspace_id=? AND membership_id=?)",
            Boolean.class,
            access.workspaceId(),
            access.workspaceId(),
            access.actorMembershipId()))) throw new AccessFailure(404, "NOT_FOUND");
  }

  @Transactional
  public List<Board> listAuthorized(WorkspaceAccess access) {
    requireActive(access);
    return list(access.workspaceId());
  }

  @Transactional
  public Board getAuthorized(WorkspaceAccess access, UUID id) {
    requireActive(access);
    return visible(access.workspaceId(), id);
  }

  @Transactional
  public Board createAuthorized(WorkspaceAccess access, UUID key, String name) {
    requireActive(access);
    return create(access.workspaceId(), access.actor().id(), key, name);
  }

  @Transactional
  public Board renameAuthorized(WorkspaceAccess access, UUID id, long version, String name) {
    requireActive(access);
    return rename(access.workspaceId(), id, version, name);
  }

  @Transactional
  public void deleteAuthorized(WorkspaceAccess access, UUID id, long version) {
    requireActive(access);
    delete(access.workspaceId(), id, version);
  }

  public List<Board> list(UUID workspace) {
    return db.query(
        "SELECT * FROM task_service.board WHERE workspace_id=? AND deleted_at IS NULL ORDER BY created_at, id",
        this::board,
        workspace);
  }

  /** Read-side parent visibility check. */
  public Board visible(UUID workspace, UUID id) {
    return find(workspace, id, false);
  }

  /** Lock the active parent for descendant mutations within the caller transaction. */
  public Board lockVisible(UUID workspace, UUID id) {
    return find(workspace, id, true);
  }

  private Board find(UUID workspace, UUID id, boolean lock) {
    var found =
        db.query(
            "SELECT * FROM task_service.board WHERE workspace_id=? AND id=? AND deleted_at IS NULL"
                + (lock ? " FOR UPDATE" : ""),
            this::board,
            workspace,
            id);
    if (found.isEmpty()) throw new AccessFailure(404, "NOT_FOUND");
    return found.getFirst();
  }

  @Transactional
  public Board create(UUID workspace, UUID actor, UUID key, String name) {
    lockWorkspace(workspace);
    String path = "/api/v1/workspaces/" + workspace + "/boards";
    db.update(
        "DELETE FROM task_service.board_create_key WHERE actor_id=? AND path=? AND key=? AND expires_at < now()",
        actor,
        path,
        key);
    var replay =
        db.query(
            "SELECT * FROM task_service.board_create_key WHERE actor_id=? AND path=? AND key=?",
            (r, n) -> {
              UUID id = r.getObject("board_id", UUID.class);
              visible(workspace, id);
              if (!name.equals(r.getString("fingerprint")))
                throw new AccessFailure(409, "IDEMPOTENCY_CONFLICT");
              Instant created = r.getTimestamp("original_created_at").toInstant();
              return new Board(id, workspace, r.getString("original_name"), 1, created, created);
            },
            actor,
            path,
            key);
    if (!replay.isEmpty()) return replay.getFirst();
    int count =
        db.queryForObject(
            "SELECT count(*) FROM task_service.board WHERE workspace_id=? AND deleted_at IS NULL",
            Integer.class,
            workspace);
    if (count >= 5) throw new AccessFailure(409, "BOARD_LIMIT_REACHED");
    UUID id = UUID.randomUUID();
    Instant now = Instant.now().truncatedTo(ChronoUnit.MILLIS);
    db.update(
        "INSERT INTO task_service.board(id, workspace_id, name, version, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
        id,
        workspace,
        name,
        java.sql.Timestamp.from(now),
        java.sql.Timestamp.from(now));
    db.update(
        "INSERT INTO task_service.board_create_key(actor_id, path, key, fingerprint, board_id, original_name, original_created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, now() + interval '25 hours')",
        actor,
        path,
        key,
        name,
        id,
        name,
        java.sql.Timestamp.from(now));
    return new Board(id, workspace, name, 1, now, now);
  }

  @Transactional
  public Board rename(UUID workspace, UUID id, long version, String name) {
    Board old = find(workspace, id, true);
    if (old.version() != version) throw new AccessFailure(412, "VERSION_CONFLICT");
    if (old.name().equals(name)) return old;
    Instant now = Instant.now().truncatedTo(ChronoUnit.MILLIS);
    db.update(
        "UPDATE task_service.board SET name=?, version=version+1, updated_at=? WHERE id=?",
        name,
        java.sql.Timestamp.from(now),
        id);
    return new Board(id, workspace, name, old.version() + 1, old.created_at(), now);
  }

  @Transactional
  public void delete(UUID workspace, UUID id, long version) {
    lockWorkspace(workspace);
    Board old = find(workspace, id, true);
    if (old.version() != version) throw new AccessFailure(412, "VERSION_CONFLICT");
    db.update("UPDATE task_service.board SET deleted_at=now() WHERE id=?", id);
  }
}
