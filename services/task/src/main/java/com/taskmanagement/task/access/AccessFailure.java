package com.taskmanagement.task.access;

public class AccessFailure extends RuntimeException {
    private final int status;
    private final String code;
    private String field;
    private String reason;
    public AccessFailure(int status, String code) { this.status = status; this.code = code; }
    public AccessFailure(int status, String code, String field, String reason) { this(status, code); this.field = field; this.reason = reason; }
    public String field() { return field; }
    public String reason() { return reason; }
    public int status() { return status; }
    public String code() { return code; }
    static AccessFailure unavailable() { return new AccessFailure(503, "DEPENDENCY_UNAVAILABLE"); }
}
