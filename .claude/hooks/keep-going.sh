#!/bin/bash
INPUT=$(cat)
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path')
SESSION=$(echo "$INPUT" | jq -r '.session_id')

# Safety cap: max 25 forced continuations per session
COUNTER="/tmp/keepgoing-$SESSION"
COUNT=$(cat "$COUNTER" 2>/dev/null || echo 0)
if [ "$COUNT" -ge 25 ]; then exit 0; fi

# Look at the last assistant message in the transcript
LAST=$(tail -n 50 "$TRANSCRIPT" | jq -rs '
  [.[] | select(.type=="assistant")] | last
  | .message.content | map(select(.type=="text").text) | join("\n")')

if echo "$LAST" | grep -qE 'STATUS: (DONE|NEEDS_INPUT)'; then
  rm -f "$COUNTER"
  exit 0   # allow stopping
fi

echo $((COUNT + 1)) > "$COUNTER"
echo '{"decision":"block","reason":"You are not finished. Continue working on the task. Only stop when it is complete (end with STATUS: DONE) or you are truly blocked (end with STATUS: NEEDS_INPUT - question)."}'
