package com.taskmanagement.task.cleanup;

import com.networknt.schema.*;
import com.rabbitmq.client.AMQP;
import java.nio.ByteBuffer;
import java.nio.charset.*;
import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.databind.*;
import tools.jackson.databind.json.JsonMapper;

@Component
public class CleanupValidator {
  public static class InvalidEvent extends RuntimeException {}

  private final JsonSchema schema;
  private final ObjectMapper mapper =
      JsonMapper.builder()
          .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
          .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
          .build();

  public CleanupValidator() {
    try (var source = CleanupValidator.class.getResourceAsStream("/contracts/event.schema.json")) {
      var config = new SchemaValidatorsConfig();
      config.setFormatAssertionsEnabled(true);
      schema =
          JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012).getSchema(source, config);
    } catch (Exception e) {
      throw new IllegalStateException("Cannot load cleanup event schema", e);
    }
  }

  public CleanupEvent validate(
      byte[] body, AMQP.BasicProperties properties, String exchange, String route) {
    try {
      if (body.length > 65536
          || properties == null
          || !"application/json".equals(properties.getContentType())) throw new InvalidEvent();
      String text =
          StandardCharsets.UTF_8
              .newDecoder()
              .onMalformedInput(CodingErrorAction.REPORT)
              .onUnmappableCharacter(CodingErrorAction.REPORT)
              .decode(ByteBuffer.wrap(body))
              .toString();
      JsonNode n = mapper.readTree(text);
      if (n == null || !schema.validate(text, InputFormat.JSON).isEmpty()) throw new InvalidEvent();
      String type = n.get("type").asString();
      if (!type.equals("workspace.member_removed.v1") && !type.equals("workspace.deleted.v1"))
        throw new InvalidEvent();
      if (!"identity-workspace".equals(n.get("producer").asString())) throw new InvalidEvent();
      if (!(("events".equals(exchange) && type.equals(route))
          || ("retry-return".equals(exchange) && "task-cleanup.v1".equals(route))))
        throw new InvalidEvent();
      UUID id = UUID.fromString(n.get("id").asString()),
          request = UUID.fromString(n.get("request_id").asString());
      if (id.version() != 4
          || id.variant() != 2
          || !id.toString().equals(properties.getMessageId())
          || !type.equals(properties.getType())
          || !request.toString().equals(properties.getCorrelationId())) throw new InvalidEvent();
      JsonNode data = n.get("data");
      UUID workspace = UUID.fromString(data.get("workspace_id").asString()),
          aggregate = UUID.fromString(n.get("aggregate_id").asString());
      UUID membership =
          type.equals("workspace.member_removed.v1")
              ? UUID.fromString(data.get("membership_id").asString())
              : null;
      if (!aggregate.equals(membership == null ? workspace : membership)
          || (membership != null && n.get("aggregate_version").longValue() != 2))
        throw new InvalidEvent();
      return new CleanupEvent(id, type, workspace, membership, request);
    } catch (InvalidEvent e) {
      throw e;
    } catch (Exception e) {
      throw new InvalidEvent();
    }
  }
}
