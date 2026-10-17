import { makeHandler } from "./handler.ts";

const base = Deno.env.get("SUPABASE_URL")!;
const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
async function database(path: string, options: RequestInit = {}) {
  const response = await fetch(`${base}/rest/v1/${path}`, { ...options,
    headers: { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json", ...options.headers },
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error("Database request failed");
  return response;
}
Deno.serve(makeHandler({
  async settings() { return (await (await database("fawn_cloud_settings?id=eq.1&select=secret_sha256,allowed_chat_ids,enabled")).json())[0]; },
  async insert(row) {
    const response = await database("fawn_cloud_inbox?on_conflict=update_id&select=update_id", {
      method: "POST", body: JSON.stringify(row), headers: { Prefer: "resolution=ignore-duplicates,return=representation" },
    });
    return (await response.json()).length === 1;
  },
}));
