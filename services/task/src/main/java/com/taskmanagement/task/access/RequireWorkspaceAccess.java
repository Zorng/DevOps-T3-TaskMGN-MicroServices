package com.taskmanagement.task.access;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** Protect a handler with a fresh Identity check; path must include workspace_id. */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface RequireWorkspaceAccess {
    boolean owner() default false;
}
