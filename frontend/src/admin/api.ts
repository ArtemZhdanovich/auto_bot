/**
 * HTTP-клиент админ-панели: аутентификация по X-Internal-Token
 * (MVP, UI/UX ТЗ §3.1; таблица admin_users + JWT — [бэкенд]).
 */

const ADMIN_TOKEN = "autobot.adminToken";

export function getAdminToken(): string | null {
  return sessionStorage.getItem(ADMIN_TOKEN);
}

export function setAdminToken(token: string): void {
  sessionStorage.setItem(ADMIN_TOKEN, token);
}

export function clearAdminToken(): void {
  sessionStorage.removeItem(ADMIN_TOKEN);
}

export class AdminApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function aapi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAdminToken();
  if (!token) throw new AdminApiError(401, "Нет токена");
  const resp = await fetch(`/api/admin${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Token": token,
      ...((options.headers as Record<string, string>) ?? {}),
    },
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : resp.statusText;
    throw new AdminApiError(resp.status, detail);
  }
  return body as T;
}

// --- Типы (контракты app/schemas/criteria.py, partner.py) ---

export interface SourceConfig {
  name: string;
  adapter_class: string;
  kind: "api" | "parser" | "hybrid";
  enabled: boolean;
  priority: number;
  schedule_cron: string | null;
  rate_limit_per_min: number;
  max_concurrency: number;
  timeout_sec: number;
  retry_policy: Record<string, unknown>;
  credentials_ref: string | null;
  capabilities: Record<string, unknown>;
  config: Record<string, unknown>;
}

export interface SourceHealth {
  source_name: string;
  last_success_at: string | null;
  last_error_at: string | null;
  error_streak: number;
  last_error: string | null;
  avg_latency_ms: number | null;
  total_fetched: number;
}

export interface AdminPartner {
  id: number;
  name: string;
  contact: string | null;
  plan: "starter" | "pro" | "salon";
  daily_quota: number;
  status: string;
  created_at: string;
  claimed_today: number;
  key_prefix: string | null;
}

export interface PartnerKeyOut {
  partner_id: number;
  key: string;
  key_prefix: string;
}

export interface AdminLead {
  id: number;
  user_id: number;
  listing_id: number | null;
  source: string | null;
  status: string;
  partner_id: number | null;
  partner_status: string | null;
  comment: string | null;
  qualification: Record<string, unknown> | null;
  created_at: string;
}
