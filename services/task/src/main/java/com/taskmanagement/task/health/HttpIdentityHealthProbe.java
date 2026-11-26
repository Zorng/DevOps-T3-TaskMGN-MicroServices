package com.taskmanagement.task.health;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class HttpIdentityHealthProbe implements IdentityHealthProbe {
  private static final Duration TIMEOUT = Duration.ofSeconds(1);
  private final HttpClient client;
  private final URI readinessUri;

  @Autowired
  public HttpIdentityHealthProbe(@Value("${task.identity-base-url}") URI baseUri) {
    this(
        HttpClient.newBuilder()
            .connectTimeout(TIMEOUT)
            .followRedirects(HttpClient.Redirect.NEVER)
            .build(),
        baseUri);
  }

  HttpIdentityHealthProbe(HttpClient client, URI baseUri) {
    if (baseUri.getHost() == null
        || !("http".equals(baseUri.getScheme()) || "https".equals(baseUri.getScheme()))
        || baseUri.getUserInfo() != null
        || baseUri.getQuery() != null
        || baseUri.getFragment() != null
        || !(baseUri.getPath().isEmpty() || "/".equals(baseUri.getPath()))) {
      throw new IllegalArgumentException(
          "Identity base URL must be an HTTP(S) origin without credentials or a path");
    }
    this.client = client;
    this.readinessUri = baseUri.resolve("/health/ready");
  }

  @Override
  public boolean isAvailable() {
    HttpRequest request = HttpRequest.newBuilder(readinessUri).timeout(TIMEOUT).GET().build();
    try {
      return client.send(request, HttpResponse.BodyHandlers.discarding()).statusCode() == 200;
    } catch (InterruptedException ex) {
      Thread.currentThread().interrupt();
      return false;
    } catch (IOException ex) {
      return false;
    }
  }
}
