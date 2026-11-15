package com.taskmanagement.task.health;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class HealthControllerTest {
    private final ReadinessService readiness = mock(ReadinessService.class);
    private final HealthController controller = new HealthController(readiness);

    @Test
    void livenessDoesNotCheckExternalDependencies() {
        assertEquals("ok", controller.live().status());
        verifyNoInteractions(readiness);
    }

    @Test
    void readinessReturnsOkWhenDependenciesAreAvailable() {
        when(readiness.isReady()).thenReturn(true);
        var result = controller.ready();
        assertEquals(200, result.getStatusCode().value());
        assertEquals("ok", result.getBody().status());
    }

    @Test
    void readinessReturnsUnavailableWhenDependenciesFail() {
        when(readiness.isReady()).thenReturn(false);
        var result = controller.ready();
        assertEquals(503, result.getStatusCode().value());
        assertEquals("unavailable", result.getBody().status());
    }
}
