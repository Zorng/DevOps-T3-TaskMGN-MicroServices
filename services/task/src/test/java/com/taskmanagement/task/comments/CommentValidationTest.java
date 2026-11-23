package com.taskmanagement.task.comments;
import com.taskmanagement.task.access.AccessFailure;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;
import static org.junit.jupiter.api.Assertions.*;
class CommentValidationTest {
    final ObjectMapper mapper=new ObjectMapper(); final UUID user=UUID.randomUUID();
    String mention(int start,int length) { return "{\"user_id\":\""+user+"\",\"start\":"+start+",\"length\":"+length+"}"; }
    @Test void codepointOffsetsPreserveBodyAndOrder() {
        String body="😀 @Sam @Sam"; String json="{\"body\":\""+body+"\",\"mentions\":["+mention(2,4)+","+mention(7,4)+"]}";
        var input=CommentValidation.parse(mapper.readTree(json)); assertEquals(body,input.body()); assertEquals(2,input.mentions().getFirst().start()); assertEquals(2,input.mentions().size());
        assertTrue(CommentValidation.parse(mapper.readTree("{\"body\":\"literal @Sam\"}")).mentions().isEmpty());
    }
    @Test void rejectMalformedBoundsRangesAndTypes() {
        for(String json:new String[]{"{\"body\":\"   \"}","{\"body\":\"\"}","{\"body\":\""+"😀".repeat(2001)+"\"}","{\"body\":42}","{\"body\":\"x\",\"extra\":1}","{\"body\":\"@Sam\",\"mentions\":null}","{\"body\":\"@Sam\",\"mentions\":["+mention(0,5)+"]}","{\"body\":\"@Sam\",\"mentions\":["+mention(1,2)+"]}","{\"body\":\"@Sam\",\"mentions\":["+mention(0,4)+","+mention(0,3)+"]}","{\"body\":\"@Sam\",\"mentions\":[{\"user_id\":\""+user+"\",\"start\":\"0\",\"length\":4}]}"}) assertEquals(422,assertThrows(AccessFailure.class,()->CommentValidation.parse(mapper.readTree(json))).status(),json);
    }
    @Test void mentionRangeAndRecipientCaps() {
        String body="@x ".repeat(51);
        var same=new java.util.ArrayList<String>();
        for(int i=0;i<51;i++) same.add(mention(i*3,2));
        var valid="{\"body\":\""+body+"\",\"mentions\":["+String.join(",",same.subList(0,50))+"]}";
        assertEquals(50,CommentValidation.parse(mapper.readTree(valid)).mentions().size());
        var tooMany="{\"body\":\""+body+"\",\"mentions\":["+String.join(",",same)+"]}";
        assertEquals(422,assertThrows(AccessFailure.class,()->CommentValidation.parse(mapper.readTree(tooMany))).status());
        var distinct=new java.util.ArrayList<String>();
        for(int i=0;i<21;i++) distinct.add("{\"user_id\":\""+UUID.randomUUID()+"\",\"start\":"+(i*3)+",\"length\":2}");
        var tooManyUsers="{\"body\":\""+body+"\",\"mentions\":["+String.join(",",distinct)+"]}";
        assertEquals(422,assertThrows(AccessFailure.class,()->CommentValidation.parse(mapper.readTree(tooManyUsers))).status());
    }

}
