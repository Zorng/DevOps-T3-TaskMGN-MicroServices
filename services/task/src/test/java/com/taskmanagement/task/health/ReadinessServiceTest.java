package com.taskmanagement.task.health;

import java.sql.Connection;
import java.sql.SQLException;
import javax.sql.DataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class ReadinessServiceTest {
    private final DataSource database = mock(DataSource.class);
    private final Connection connection = mock(Connection.class);
    private final IdentityHealthProbe identity = mock(IdentityHealthProbe.class);
    private final ReadinessService service = new ReadinessService(database, identity);

    @BeforeEach
    void setUp() throws SQLException {
        when(database.getConnection()).thenReturn(connection);
    }

    @Test
    void readyOnlyWhenDatabaseAndIdentityAreAvailable() throws SQLException {
        when(connection.isValid(1)).thenReturn(true);
        when(identity.isAvailable()).thenReturn(true);
        assertTrue(service.isReady());
        verify(connection).close();
    }

    @Test
    void databaseFailureIsNotReadyAndDoesNotProbeIdentity() throws SQLException {
        when(database.getConnection()).thenThrow(new SQLException("unavailable"));
        assertFalse(service.isReady());
        verifyNoInteractions(identity);
    }

    @Test
    void invalidConnectionIsNotReadyAndIsClosed() throws SQLException {
        when(connection.isValid(1)).thenReturn(false);
        assertFalse(service.isReady());
        verify(connection).close();
        verifyNoInteractions(identity);
    }

    @Test
    void identityFailureIsNotReady() throws SQLException {
        when(connection.isValid(1)).thenReturn(true);
        when(identity.isAvailable()).thenReturn(false);
        assertFalse(service.isReady());
    }
}
