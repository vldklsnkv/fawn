// Telegram authenticates with its secret header. No Telegram token is deployed.
export type Settings = { secret_sha256: string; allowed_chat_ids: string[]; enabled: boolean };
export type InboxRow = {
  update_id: number; message_id: number; chat_id: string;
  sent_at: string; edited_at: string | null; received_at: string;
  source_text: string; user_comment: string; urls: string[];
  raw_update: Record<string, unknown>; diagnostic: boolean;
};
export type Database = {
  settings(): Promise<Settings | undefined>;
  insert(row: InboxRow): Promise<boolean>;
};
const json = (value: unknown, status = 200) => Response.json(value, { status });
export const digest = async (text: string) => Array.from(new Uint8Array(
  await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)),
)).map(x => x.toString(16).padStart(2, "0")).join("");

const urlPattern = /https?:\/\/[^\s<>\]\[(){}]+/gi;
export function urlsIn(text: string): string[] {
  return [...new Set((text.match(urlPattern) || []).map(value => value.replace(/[.,;:!?"']+$/, "")))];
}
export function withoutUrls(text: string): string {
  return text.replace(urlPattern, value => value.slice(value.replace(/[.,;:!?"']+$/, "").length)).trim();
}
const textOf = (message: Record<string, any>) => typeof message.text === "string" ? message.text : typeof message.caption === "string" ? message.caption : "";

export function makeHandler(db: Database) {
  return async (req: Request): Promise<Response> => {
    if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);
    const secret = req.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
    if (!secret || secret.length > 256) return json({ error: "Unauthorized" }, 401);
    try {
      const setting = await db.settings();
      if (!setting || await digest(secret) !== setting.secret_sha256) return json({ error: "Unauthorized" }, 401);
      if (!setting.enabled) return json({ error: "Receiver paused" }, 503);
      // Check while streaming; do not allocate an unbounded request body first.
      let length = 0;
      const parts: Uint8Array[] = [];
      const reader = req.body?.getReader();
      if (reader) while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.length;
        if (length > 262144) { await reader.cancel(); return json({ error: "Payload too large" }, 413); }
        parts.push(value);
      }
      const bytes = new Uint8Array(length);
      let offset = 0;
      for (const part of parts) { bytes.set(part, offset); offset += part.length; }
      let update: Record<string, any>;
      try { update = JSON.parse(new TextDecoder().decode(bytes)); } catch { return json({ error: "Invalid JSON" }, 400); }
      if (!update || typeof update !== "object" || Array.isArray(update)) return json({ error: "Invalid update" }, 400);
      const message = update.message || update.edited_message;
      if (!message) return json({ ok: true });
      const chat = message.chat;
      if (chat?.type !== "private" || !setting.allowed_chat_ids.includes(String(chat.id))) return json({ ok: true });
      if (![update.update_id, message.message_id, message.date].every(Number.isSafeInteger)) return json({ error: "Invalid message" }, 400);
      if (message.date < 0 || message.date > 8640000000000 || (message.edit_date !== undefined && (!Number.isSafeInteger(message.edit_date) || message.edit_date < 0 || message.edit_date > 8640000000000))) return json({ error: "Invalid date" }, 400);
      const text = textOf(message);
      const currentUrls = urlsIn(text);
      const replyText = textOf(message.reply_to_message || {});
      const source = currentUrls.length ? text : [replyText, text].filter(Boolean).join("\n");
      // Also retain Telegram's hidden text-link targets without altering the comment.
      const entities = message.entities || message.caption_entities || [];
      const replyEntities = message.reply_to_message?.entities || message.reply_to_message?.caption_entities || [];
      const hidden = [...entities, ...(!currentUrls.length ? replyEntities : [])]
        .filter(x => x?.type === "text_link" && typeof x.url === "string" && /^https?:\/\//i.test(x.url)).map(x => x.url);
      const urls = [...new Set([...urlsIn(source), ...hidden])];
      const diagnostic = update.update_id < 0;
      const row: InboxRow = {
        update_id: update.update_id, message_id: message.message_id, chat_id: String(chat.id),
        sent_at: new Date(message.date * 1000).toISOString(),
        edited_at: message.edit_date === undefined ? null : new Date(message.edit_date * 1000).toISOString(),
        received_at: new Date().toISOString(), source_text: source,
        user_comment: withoutUrls(text), urls, raw_update: update, diagnostic,
      };
      const inserted = await db.insert(row);
      if (!inserted || diagnostic) return json({ ok: true, inserted });
      // A webhook response sends the acknowledgement without persisting a bot token.
      return json({ method: "sendMessage", chat_id: chat.id, text: urls.length
        ? `Сохранено в облако: ${urls.length}. Добавлю в Fawn Space по расписанию.`
        : "Сообщение сохранено в облако. Для материала пришли ссылку или ответь на сообщение со ссылкой." });
    } catch {
      // Do not log payloads, identifiers, credentials, or upstream response bodies.
      return json({ error: "Receiver temporarily unavailable" }, 503);
    }
  };
}
