export interface SseMessage {
  event: string;
  data: string;
}

export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseMessage> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "";
  let data: string[] = [];

  function* takeLines(final: boolean): Generator<SseMessage> {
    let pending = buffer;
    let carry = "";
    // A trailing "\r" may be the first half of a "\r\n" split across chunks.
    if (!final && pending.endsWith("\r")) {
      carry = "\r";
      pending = pending.slice(0, -1);
    }
    const lines = pending.split(/\r\n|\n|\r/);
    buffer = (lines.pop() ?? "") + carry;
    for (const line of lines) {
      if (line === "") {
        if (data.length > 0) yield { event: event || "message", data: data.join("\n") };
        event = "";
        data = [];
        continue;
      }
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
  }

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      yield* takeLines(false);
    }
    buffer += decoder.decode() + "\n\n";
    yield* takeLines(true);
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}
