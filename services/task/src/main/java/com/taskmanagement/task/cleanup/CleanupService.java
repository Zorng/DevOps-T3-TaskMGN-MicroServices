package com.taskmanagement.task.cleanup;

import com.taskmanagement.task.board.BoardService;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

@Service
public class CleanupService {
  private final JdbcTemplate db;
  private final TransactionTemplate tx;
  private final BoardService boards;

  public CleanupService(JdbcTemplate db, TransactionTemplate tx, BoardService boards) {
    this.db = db;
    this.tx = tx;
    this.boards = boards;
  }

  /**
   * Returns false for an already committed event. A thrown exception rolls back marker and effects.
   */
  public boolean apply(CleanupEvent event) {
    return Boolean.TRUE.equals(
        tx.execute(
            status -> {
              db.execute("SET LOCAL lock_timeout = '3s'");
              db.execute("SET LOCAL statement_timeout = '5s'");
              boards.lockWorkspace(event.workspace());
              int inserted =
                  db.update(
                      "INSERT INTO task_service.processed_event(consumer_name,event_id) VALUES ('task-cleanup.v1',?) ON CONFLICT DO NOTHING",
                      event.id());
              if (inserted == 0) return false;
              if (event.type().equals("workspace.member_removed.v1")) {
                db.update(
                    "INSERT INTO task_service.membership_tombstone(workspace_id,membership_id) VALUES (?,?) ON CONFLICT DO NOTHING",
                    event.workspace(),
                    event.membership());
                db.update(
                    "UPDATE task_service.task SET assignee_id=NULL,assignee_name=NULL,assignee_membership_id=NULL,assignee_workspace_version=NULL,version=version+1,updated_at=date_trunc('milliseconds',clock_timestamp()) WHERE workspace_id=? AND assignee_membership_id=? AND deleted_at IS NULL",
                    event.workspace(),
                    event.membership());
              } else if (event.type().equals("workspace.deleted.v1")) {
                db.update(
                    "INSERT INTO task_service.workspace_tombstone(workspace_id) VALUES (?) ON CONFLICT DO NOTHING",
                    event.workspace());
              } else throw new IllegalArgumentException("Unsupported cleanup event");
              return true;
            }));
  }
}
