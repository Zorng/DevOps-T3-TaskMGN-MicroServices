package com.taskmanagement.task.outbox;

import static org.junit.jupiter.api.Assertions.*;

import com.networknt.schema.*;
import java.nio.file.*;

public final class EventSchemaAssertions {
  private EventSchemaAssertions() {}

  public static void valid(String event) throws Exception {
    try (var source =
        Files.newInputStream(Path.of("../../api_contracts/events/event.schema.json"))) {
      var config = new SchemaValidatorsConfig();
      config.setFormatAssertionsEnabled(true);
      var schema =
          JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012).getSchema(source, config);
      var errors = schema.validate(event, InputFormat.JSON);
      assertTrue(errors.isEmpty(), () -> errors.toString());
    }
  }
}
