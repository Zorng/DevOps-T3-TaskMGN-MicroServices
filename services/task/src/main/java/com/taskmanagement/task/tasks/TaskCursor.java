package com.taskmanagement.task.tasks;

import com.taskmanagement.task.access.AccessFailure;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Base64;
import java.util.UUID;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class TaskCursor {
    private final byte[] secret;
    public record Position(Instant created, UUID id) {}
    public TaskCursor(@Value("${task.cursor-secret:}") String secret) { this.secret=secret.getBytes(StandardCharsets.UTF_8); }
    public void requireConfigured() { if (secret.length < 32) throw new AccessFailure(503,"DEPENDENCY_UNAVAILABLE"); }
    private byte[] mac(byte[] body) {
        requireConfigured();
        try { var mac=Mac.getInstance("HmacSHA256"); mac.init(new SecretKeySpec(secret,"HmacSHA256")); return mac.doFinal(body); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
    public String encode(UUID actor, UUID workspace, UUID filter, Position position) {
        String value="1|"+actor+"|"+workspace+"|"+(filter==null?"-":filter)+"|"+position.created()+"|"+position.id();
        byte[] data=value.getBytes(StandardCharsets.UTF_8); var enc=Base64.getUrlEncoder().withoutPadding();
        return enc.encodeToString(data)+"."+enc.encodeToString(mac(data));
    }
    public Position decode(String cursor, UUID actor, UUID workspace, UUID filter) {
        try {
            if (cursor.length()>1024) throw new IllegalArgumentException();
            String[] parts=cursor.split("\\.",-1); if (parts.length!=2) throw new IllegalArgumentException();
            var decoder=Base64.getUrlDecoder(); byte[] data=decoder.decode(parts[0]); byte[] signature=decoder.decode(parts[1]);
            if (!MessageDigest.isEqual(mac(data),signature)) throw new IllegalArgumentException();
            String[] f=new String(data,StandardCharsets.UTF_8).split("\\|",-1);
            if (f.length!=6 || !f[0].equals("1") || !f[1].equals(actor.toString()) || !f[2].equals(workspace.toString()) || !f[3].equals(filter==null?"-":filter.toString())) throw new IllegalArgumentException();
            return new Position(Instant.parse(f[4]),UUID.fromString(f[5]));
        } catch (AccessFailure e) { throw e; } catch (RuntimeException e) { throw new AccessFailure(400,"INVALID_CURSOR"); }
    }
}
