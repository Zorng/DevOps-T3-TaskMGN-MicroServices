package com.taskmanagement.task.access;

public class AccessFailure extends RuntimeException {
    private final int status;
    private final String code;
    public AccessFailure(int status, String code) { this.status = status; this.code = code; }
    public int status() { return status; }
    public String code() { return code; }
    static AccessFailure unavailable() { return new AccessFailure(503, "DEPENDENCY_UNAVAILABLE"); }
}
