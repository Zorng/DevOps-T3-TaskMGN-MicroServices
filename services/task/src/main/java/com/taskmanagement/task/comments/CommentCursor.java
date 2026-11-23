package com.taskmanagement.task.comments;
import com.taskmanagement.task.access.AccessFailure;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
@Component
public class CommentCursor {
    private final byte[] secret;
    public record Position(Instant created,UUID id) {}
    public CommentCursor(@Value("${task.cursor-secret:}") String secret) { this.secret=secret.getBytes(StandardCharsets.UTF_8); }
    public void requireConfigured() { if(secret.length<32) throw new AccessFailure(503,"DEPENDENCY_UNAVAILABLE"); }
    private byte[] mac(byte[] body) { requireConfigured(); try { var mac=Mac.getInstance("HmacSHA256"); mac.init(new SecretKeySpec(secret,"HmacSHA256")); return mac.doFinal(body); } catch(Exception e) { throw new IllegalStateException(e); } }
    public String encode(UUID actor,UUID workspace,UUID task,Position position) {
        byte[] data=("comment-v1|"+actor+"|"+workspace+"|"+task+"|"+position.created()+"|"+position.id()).getBytes(StandardCharsets.UTF_8);
        var encoder=Base64.getUrlEncoder().withoutPadding(); return encoder.encodeToString(data)+"."+encoder.encodeToString(mac(data));
    }
    public Position decode(String cursor,UUID actor,UUID workspace,UUID task) {
        try {
            if(cursor.length()>1024) throw new IllegalArgumentException(); String[] parts=cursor.split("\\.",-1); if(parts.length!=2) throw new IllegalArgumentException();
            var decoder=Base64.getUrlDecoder(); byte[] data=decoder.decode(parts[0]),signature=decoder.decode(parts[1]); if(!MessageDigest.isEqual(mac(data),signature)) throw new IllegalArgumentException();
            String[] values=new String(data,StandardCharsets.UTF_8).split("\\|",-1);
            if(values.length!=6 || !values[0].equals("comment-v1") || !values[1].equals(actor.toString()) || !values[2].equals(workspace.toString()) || !values[3].equals(task.toString())) throw new IllegalArgumentException();
            return new Position(Instant.parse(values[4]),UUID.fromString(values[5]));
        } catch(AccessFailure e) { throw e; } catch(RuntimeException e) { throw new AccessFailure(400,"INVALID_CURSOR"); }
    }
}
