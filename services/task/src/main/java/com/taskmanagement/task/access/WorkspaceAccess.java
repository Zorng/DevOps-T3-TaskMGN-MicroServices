package com.taskmanagement.task.access;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/** A validated snapshot from Identity; membership IDs identify membership generations. */
public record WorkspaceAccess(User actor, UUID workspaceId, long workspaceVersion,
                              UUID actorMembershipId, Role actorRole, List<Member> members) {
    public WorkspaceAccess { members = List.copyOf(members); }
    public enum Role { owner, member }
    public record User(UUID id, String displayName) {}
    public record Member(UUID membershipId, User user, Role role, Instant joinedAt) {}
    public boolean isOwner() { return actorRole == Role.owner; }
}
