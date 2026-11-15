package com.taskmanagement.task.health;

import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class HealthApiTest {
    private final ReadinessService readiness = mock(ReadinessService.class);
    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        mvc = MockMvcBuilders.standaloneSetup(new HealthController(readiness))
                .addFilters(new RequestCorrelationFilter()).build();
    }

    @Test
    void liveEndpointIsUnauthenticatedAndIndependent() throws Exception {
        var response = mvc.perform(get("/health/live"))
                .andExpect(status().isOk())
                .andExpect(content().json("{\"status\":\"ok\"}"))
                .andExpect(header().string("Cache-Control", "no-store"))
                .andReturn().getResponse();
        assertDoesNotThrow(() -> UUID.fromString(response.getHeader("X-Request-ID")));
        assertNull(MDC.get("request_id"));
        verifyNoInteractions(readiness);
    }

    @Test
    void readinessReturnsOnlyContractStatusAndPreservesCorrelation() throws Exception {
        when(readiness.isReady()).thenReturn(true);
        String id = UUID.randomUUID().toString();
        mvc.perform(get("/health/ready").header("X-Request-ID", id))
                .andExpect(status().isOk()).andExpect(content().json("{\"status\":\"ok\"}"))
                .andExpect(header().string("X-Request-ID", id));
    }

    @Test
    void unavailableReadinessIs503WithNoDependencyDetails() throws Exception {
        when(readiness.isReady()).thenReturn(false);
        mvc.perform(get("/health/ready"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(content().json("{\"status\":\"unavailable\"}"))
                .andExpect(header().string("Cache-Control", "no-store"));
    }

    @Test
    void malformedCorrelationIdIsReplaced() throws Exception {
        String id = mvc.perform(get("/health/live").header("X-Request-ID", "invalid"))
                .andExpect(status().isOk()).andReturn().getResponse().getHeader("X-Request-ID");
        assertDoesNotThrow(() -> UUID.fromString(id));
    }

    @Test
    void healthEndpointsDoNotAcceptWrites() throws Exception {
        mvc.perform(post("/health/live")).andExpect(status().isMethodNotAllowed());
        mvc.perform(post("/health/ready")).andExpect(status().isMethodNotAllowed());
        verifyNoInteractions(readiness);
    }
}
