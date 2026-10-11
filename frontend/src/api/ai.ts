/** AI-поиск (этап «ИИ-поиск в аппке»): /api/ai/* — тот же агент, что в боте. */

import { api } from "./client";

export interface AiReply {
  session_id: string;
  text: string;
  options: string[];
  status: string;
  finalize_ready: boolean;
  turn_count: number;
  criteria: Record<string, unknown>;
}

export function aiMessage(text: string): Promise<AiReply> {
  return api<AiReply>("/ai/message", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function aiReset(): Promise<{ reset: boolean }> {
  return api<{ reset: boolean }>("/ai/reset", { method: "POST", body: "{}" });
}

export function aiFinalize(criteria?: Record<string, unknown>): Promise<{ sources: string[] }> {
  return api<{ sources: string[] }>("/ai/finalize", {
    method: "POST",
    body: JSON.stringify({ criteria: criteria ?? null }),
  });
}
