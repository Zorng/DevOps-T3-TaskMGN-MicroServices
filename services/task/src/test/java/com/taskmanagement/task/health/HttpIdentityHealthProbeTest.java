package com.taskmanagement.task.health;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class HttpIdentityHealthProbeTest {
    private final HttpClient client = mock(HttpClient.class);
    private final HttpIdentityHealthProbe probe = new HttpIdentityHealthProbe(client, URI.create("http://identity:8000"));

    @Test
    @SuppressWarnings("unchecked")
    void usesBoundedCredentialFreeReadinessProbe() throws Exception {
        HttpResponse<Object> response = mock(HttpResponse.class);
        when(client.send(any(), any())).thenReturn(response);
        when(response.statusCode()).thenReturn(200);
        assertTrue(probe.isAvailable());
        ArgumentCaptor<HttpRequest> request = ArgumentCaptor.forClass(HttpRequest.class);
        verify(client).send(request.capture(), any());
        assertEquals(URI.create("http://identity:8000/health/ready"), request.getValue().uri());
        assertEquals(Duration.ofSeconds(1), request.getValue().timeout().orElseThrow());
        assertTrue(request.getValue().headers().firstValue("Authorization").isEmpty());
    }

    @Test
    @SuppressWarnings("unchecked")
    void nonOkStatusIsNotAvailable() throws Exception {
        HttpResponse<Object> response = mock(HttpResponse.class);
        when(client.send(any(), any())).thenReturn(response);
        when(response.statusCode()).thenReturn(503);
        assertFalse(probe.isAvailable());
    }

    @Test
    void networkFailureIsNotAvailable() throws Exception {
        when(client.send(any(), any())).thenThrow(new IOException("connection failed"));
        assertFalse(probe.isAvailable());
    }

    @Test
    void interruptedProbeRestoresInterruptFlag() throws Exception {
        when(client.send(any(), any())).thenThrow(new InterruptedException());
        try {
            assertFalse(probe.isAvailable());
            assertTrue(Thread.currentThread().isInterrupted());
        } finally {
            Thread.interrupted();
        }
    }

    @Test
    void rejectsOriginsWithCredentialsPathsOrUnsupportedSchemes() {
        for (String uri : new String[]{"file:///tmp/identity", "http://user:secret@identity", "http://identity/api", "http://identity?token=x"}) {
            assertThrows(IllegalArgumentException.class, () -> new HttpIdentityHealthProbe(client, URI.create(uri)));
        }
    }
}
