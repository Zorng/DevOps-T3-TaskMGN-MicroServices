package com.taskmanagement.task.comments;
import com.taskmanagement.task.access.AccessFailure;
import com.taskmanagement.task.tasks.TaskValidation;
import java.util.*;
import tools.jackson.databind.JsonNode;
public final class CommentValidation {
    private CommentValidation() {}
    private static AccessFailure invalid() { return new AccessFailure(422,"VALIDATION_FAILED"); }
    public static CommentInput parse(JsonNode node) {
        if(node==null || !node.isObject() || !node.has("body") || node.propertyNames().stream().anyMatch(k->!Set.of("body","mentions").contains(k))) throw invalid();
        JsonNode bodyNode=node.get("body"); if(!bodyNode.isString()) throw invalid(); String body=bodyNode.asString();
        int[] codepoints=body.codePoints().toArray(); if(codepoints.length<1 || codepoints.length>2000 || Arrays.stream(codepoints).allMatch(c->Character.isWhitespace(c)||Character.isSpaceChar(c))) throw invalid();
        var ranges=new ArrayList<CommentInput.Mention>(); var distinct=new HashSet<UUID>();
        if(node.has("mentions")) {
            JsonNode mentions=node.get("mentions"); if(!mentions.isArray() || mentions.size()>50) throw invalid();
            for(JsonNode m:mentions) {
                if(!m.isObject() || m.size()!=3 || !m.has("user_id") || !m.has("start") || !m.has("length") || !m.get("user_id").isString() || !m.get("start").isIntegralNumber() || !m.get("start").canConvertToInt() || !m.get("length").isIntegralNumber() || !m.get("length").canConvertToInt()) throw invalid();
                UUID id=TaskValidation.uuid(m.get("user_id").asString()); int start=m.get("start").intValue(),length=m.get("length").intValue();
                if(start<0 || start>1999 || length<2 || length>2000 || start>codepoints.length-length || codepoints[start]!='@') throw invalid();
                ranges.add(new CommentInput.Mention(id,start,length)); distinct.add(id);
            }
        }
        if(distinct.size()>20) throw invalid();
        var sorted=new ArrayList<>(ranges); sorted.sort(Comparator.comparingInt(CommentInput.Mention::start));
        for(int i=1;i<sorted.size();i++) if(sorted.get(i-1).start()+sorted.get(i-1).length()>sorted.get(i).start()) throw invalid();
        return new CommentInput(body,ranges);
    }
}
