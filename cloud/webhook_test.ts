import { digest, InboxRow, makeHandler, Settings, withoutUrls } from "./handler.ts";
const assert = (value: unknown) => { if (!value) throw new Error("Assertion failed"); };
const secret = "fixture-secret";
const fixture = () => ({ update_id: 123, message: { message_id: 12, date: 1792127000, chat: { id: 42, type: "private" }, text: "https://example.com/\nМой комментарий\nбез изменений" } });
async function setup() {
  const rows: InboxRow[] = [];
  const settings: Settings = { secret_sha256: await digest(secret), allowed_chat_ids: ["42"], enabled: true };
  const handle = makeHandler({ async settings() { return settings; }, async insert(row) { if (rows.some(x => x.update_id === row.update_id)) return false; rows.push(row); return true; } });
  const call = (body: unknown, auth = secret) => handle(new Request("https://receiver.example/", { method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": auth }, body: JSON.stringify(body) }));
  return { rows, settings, handle, call };
}
Deno.test("auth, chat allowlist and pause reject before persisting", async () => {
  const s = await setup();
  assert((await s.call(fixture(), "wrong")).status === 401);
  const bad = fixture(); bad.message.chat.id = 43;
  assert((await s.call(bad)).status === 200 && s.rows.length === 0);
  s.settings.enabled = false;
  assert((await s.call(fixture())).status === 503 && s.rows.length === 0);
});
Deno.test("durable insert, exact comment and duplicate suppression", async () => {
  const s = await setup();
  assert((await (await s.call(fixture())).json()).method === "sendMessage");
  assert(s.rows.length === 1 && s.rows[0].user_comment === "Мой комментарий\nбез изменений");
  assert(s.rows[0].source_text === fixture().message.text);
  assert((await (await s.call(fixture())).json()).inserted === false && s.rows.length === 1);
});
Deno.test("reply comments and edits retain original timestamps and text", async () => {
  const s = await setup();
  const reply = { update_id: 124, edited_message: { message_id: 13, date: 1792127000, edit_date: 1792127100, chat: { id: 42, type: "private" }, text: "Ещё комментарий", reply_to_message: fixture().message } };
  await s.call(reply);
  assert(s.rows[0].urls[0] === "https://example.com/" && s.rows[0].user_comment === "Ещё комментарий");
  assert(s.rows[0].sent_at === "2026-10-16T05:03:20.000Z" && s.rows[0].edited_at === "2026-10-16T05:05:00.000Z");
});
Deno.test("diagnostics cannot become library material", async () => {
  const s = await setup(); const row = fixture(); row.update_id = -123;
  const result = await (await s.call(row)).json();
  assert(s.rows[0].diagnostic && result.method === undefined);
});
Deno.test("oversized bodies and malformed dates are rejected", async () => {
  const s = await setup();
  assert((await s.call({ text: "x".repeat(262145) })).status === 413);
  const bad = fixture(); bad.message.date = -1;
  assert((await s.call(bad)).status === 400 && s.rows.length === 0);
});
Deno.test("hidden Telegram URLs are retained, punctuation stays in comment", async () => {
  const s = await setup();
  const message = { ...fixture().message, text: "Посмотреть", entities: [{ type: "text_link", url: "https://example.com/hidden" }] };
  await s.call({ update_id: 125, message });
  assert(s.rows[0].urls[0] === "https://example.com/hidden" && s.rows[0].user_comment === "Посмотреть");
  assert(withoutUrls("https://example.com/, важно") === ", важно");
});
