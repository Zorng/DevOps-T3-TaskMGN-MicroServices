# REST Endpoint Inventory

Generated from the OpenAPI documents. Public paths are exposed unchanged through the gateway; /internal and /health are private. All /api/v1 routes require an application bearer session except Google challenge/exchange.

| Work area | Method | Path | Operation |
| --- | --- | --- | --- |
| Identity & Workspace | POST | `/api/v1/auth/google/challenges` | Start Google sign-in |
| Identity & Workspace | POST | `/api/v1/auth/google/exchange` | Exchange verified Google credential for application session |
| Identity & Workspace | POST | `/api/v1/auth/logout` | Revoke current session |
| Identity & Workspace | GET | `/api/v1/me` | Get current profile and owned workspace count |
| Identity & Workspace | GET | `/api/v1/workspaces` | List joined and owned workspaces |
| Identity & Workspace | POST | `/api/v1/workspaces` | Create workspace |
| Identity & Workspace | GET | `/api/v1/workspaces/{workspace_id}` | Get workspace |
| Identity & Workspace | PATCH | `/api/v1/workspaces/{workspace_id}` | Rename workspace |
| Identity & Workspace | DELETE | `/api/v1/workspaces/{workspace_id}` | Delete workspace |
| Identity & Workspace | GET | `/api/v1/workspaces/{workspace_id}/members` | List workspace members |
| Identity & Workspace | DELETE | `/api/v1/workspaces/{workspace_id}/members/{user_id}` | Remove member |
| Identity & Workspace | DELETE | `/api/v1/workspaces/{workspace_id}/membership` | Leave workspace |
| Identity & Workspace | POST | `/api/v1/workspaces/{workspace_id}/invitations` | Invite by exact registered email |
| Identity & Workspace | GET | `/api/v1/workspaces/{workspace_id}/invitations` | List workspace invitations |
| Identity & Workspace | GET | `/api/v1/invitations` | List invitations addressed to me |
| Identity & Workspace | GET | `/api/v1/invitations/{invitation_id}` | Get invitation |
| Identity & Workspace | POST | `/api/v1/invitations/{invitation_id}/response` | Accept or decline invitation |
| Identity & Workspace | POST | `/api/v1/invitations/{invitation_id}/cancel` | Cancel pending invitation |
| Identity & Workspace | GET | `/health/live` | Service live |
| Identity & Workspace | GET | `/health/ready` | Service ready |
| Task | GET | `/api/v1/workspaces/{workspace_id}/boards` | List workspace boards |
| Task | POST | `/api/v1/workspaces/{workspace_id}/boards` | Create board/status |
| Task | GET | `/api/v1/workspaces/{workspace_id}/boards/{board_id}` | Get board |
| Task | PATCH | `/api/v1/workspaces/{workspace_id}/boards/{board_id}` | Rename board/status |
| Task | DELETE | `/api/v1/workspaces/{workspace_id}/boards/{board_id}` | Delete board and contents |
| Task | GET | `/api/v1/workspaces/{workspace_id}/tasks` | List workspace tasks |
| Task | POST | `/api/v1/workspaces/{workspace_id}/tasks` | Create task |
| Task | GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}` | Get task |
| Task | PATCH | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}` | Edit task or move to another board |
| Task | DELETE | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}` | Delete task and comments |
| Task | GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/comments` | List task comments |
| Task | POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/comments` | Add comment with explicit mentions |
| Task | GET | `/health/live` | Service live |
| Task | GET | `/health/ready` | Service ready |
| Notification | GET | `/api/v1/notifications` | List my notifications |
| Notification | GET | `/api/v1/notifications/unread-count` | Get my unread count |
| Notification | POST | `/api/v1/notifications/{notification_id}/read` | Mark my notification read |
| Notification | GET | `/health/live` | Service live |
| Notification | GET | `/health/ready` | Service ready |
| Identity & Workspace (private) | POST | `/internal/v1/auth/introspect` | Validate application session |
| Identity & Workspace (private) | POST | `/internal/v1/workspaces/{workspace_id}/access` | Get current actor access and membership generations |
