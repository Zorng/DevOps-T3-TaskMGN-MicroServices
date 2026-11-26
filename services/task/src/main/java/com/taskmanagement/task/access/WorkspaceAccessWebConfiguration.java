package com.taskmanagement.task.access;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.core.MethodParameter;
import org.springframework.stereotype.Component;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.HandlerMapping;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Component
public class WorkspaceAccessWebConfiguration implements WebMvcConfigurer {
  private static final String ATTRIBUTE = WorkspaceAccess.class.getName();
  private final WorkspaceAccessClient client;

  public WorkspaceAccessWebConfiguration(WorkspaceAccessClient client) {
    this.client = client;
  }

  @Override
  public void addInterceptors(InterceptorRegistry registry) {
    registry.addInterceptor(interceptor());
  }

  HandlerInterceptor interceptor() {
    return new HandlerInterceptor() {
      @Override
      public boolean preHandle(
          HttpServletRequest request, HttpServletResponse response, Object handler) {
        if (!(handler instanceof HandlerMethod method)) return true;
        RequireWorkspaceAccess guard = method.getMethodAnnotation(RequireWorkspaceAccess.class);
        if (guard == null) return true;
        @SuppressWarnings("unchecked")
        Map<String, String> variables =
            (Map<String, String>)
                request.getAttribute(HandlerMapping.URI_TEMPLATE_VARIABLES_ATTRIBUTE);
        UUID workspace;
        try {
          String value = variables.get("workspace_id");
          workspace = UUID.fromString(value);
          if (!workspace.toString().equalsIgnoreCase(value)) throw new IllegalArgumentException();
        } catch (RuntimeException ex) {
          throw new AccessFailure(400, "MALFORMED_REQUEST");
        }
        WorkspaceAccess access =
            client.check(
                workspace, request.getHeader("Authorization"), response.getHeader("X-Request-ID"));
        if (guard.owner() && !access.isOwner()) throw new AccessFailure(403, "FORBIDDEN");
        request.setAttribute(ATTRIBUTE, access);
        return true;
      }
    };
  }

  @Override
  public void addArgumentResolvers(List<HandlerMethodArgumentResolver> resolvers) {
    resolvers.add(resolver());
  }

  HandlerMethodArgumentResolver resolver() {
    return new HandlerMethodArgumentResolver() {
      @Override
      public boolean supportsParameter(MethodParameter parameter) {
        return parameter.getParameterType() == WorkspaceAccess.class;
      }

      @Override
      public Object resolveArgument(
          MethodParameter parameter,
          ModelAndViewContainer container,
          NativeWebRequest request,
          WebDataBinderFactory factory) {
        Object access = request.getAttribute(ATTRIBUTE, NativeWebRequest.SCOPE_REQUEST);
        if (access == null)
          throw new IllegalStateException("WorkspaceAccess requires @RequireWorkspaceAccess");
        return access;
      }
    };
  }
}
