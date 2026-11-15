package com.taskmanagement.task.health;

import java.sql.Connection;
import java.sql.SQLException;
import javax.sql.DataSource;
import org.springframework.stereotype.Service;

@Service
public class ReadinessService {
    private final DataSource dataSource;
    private final IdentityHealthProbe identity;

    public ReadinessService(DataSource dataSource, IdentityHealthProbe identity) {
        this.dataSource = dataSource;
        this.identity = identity;
    }

    public boolean isReady() {
        try (Connection connection = dataSource.getConnection()) {
            if (!connection.isValid(1)) {
                return false;
            }
        } catch (SQLException ex) {
            return false;
        }
        return identity.isAvailable();
    }
}
