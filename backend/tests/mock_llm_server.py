"""A tiny OpenAI-compatible server used to exercise SUNNY's LLM code path
without spending a real API key.

It echoes back what it received, so you can verify that the system prompt,
the grounded CONTEXT block and the conversation history all arrive intact.

    python tests/mock_llm_server.py           # listens on :9999
"""
import json
import re

from fastapi import FastAPI, Request
import uvicorn

app = FastAPI(title="Mock OpenAI-compatible LLM")


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    msgs = body.get("messages", [])
    system = next((m["content"] for m in msgs if m["role"] == "system"), "")
    user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
    history = [m for m in msgs if m["role"] != "system"][:-1]

    ctx = {}
    if "CONTEXT" in system:
        for line in system.split("CONTEXT", 1)[1].splitlines():
            m = re.match(r"^- ([a-z_]+): (.*)$", line.strip())
            if m:
                ctx[m.group(1)] = m.group(2)

    reply = (
        "[mock model reply]\n\n"
        f"I received your question: \"{user[:90]}\"\n\n"
        f"The system prompt gave me **{len(ctx)} grounded facts** about this "
        f"student, including attendance **{ctx.get('attendance_rate', '?')}%** "
        f"for **{ctx.get('student_name', 'unknown')}** "
        f"(roll {ctx.get('roll_number', '?')}), and "
        f"**{len(history)}** earlier turns of conversation.\n\n"
        "A real model would answer the question here — this proves the request "
        "reached the provider correctly."
    )

    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "model": body.get("model", "mock"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": sum(len(m["content"]) // 4 for m in msgs),
                  "completion_tokens": len(reply) // 4},
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9999, log_level="warning")
