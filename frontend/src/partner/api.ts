/**
 * HTTP-клиент партнёрского ЛК: аутентификация по API-ключу
 * (X-API-Key, UI/UX ТЗ §4.1 — MVP вход только по ключу).
 */

const PARTNER_KEY = "autobot.partnerKey";

export function getPartnerKey(): string | null {
  return sessionStorage.getItem(PARTNER_KEY);
}

export function setPartnerKey(key: string): void {
  sessionStorage.setItem(PARTNER_KEY, key);
}

export function clearPartnerKey(): void {
  sessionStorage.removeItem(PARTNER_KEY);
}

export class PartnerApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function papi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const key = getPartnerKey();
  if (!key) throw new PartnerApiError(401, "Нет API-ключа");
  const resp = await fetch(`/api/partner${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": key,
      ...((options.headers as Record<string, string>) ?? {}),
    },
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : resp.statusText;
    throw new PartnerApiError(resp.status, detail);
  }
  return body as T;
}

// --- Типы (контракты app/schemas/partner.py) ---

export interface PartnerMe {
  id: number;
  name: string;
  plan: "starter" | "pro" | "salon";
  daily_quota: number;
  claimed_today: number;
  quota_remaining: number;
  key_prefix: string | null;
}

export interface PartnerLead {
  id: number;
  source: string | null;
  status: string;
  comment: string | null;
  qualification: Record<string, unknown> | null;
  created_at: string;
  partner_id: number | null;
  partner_status: string | null;
  claimed_at: string | null;
  user_id: number | null;
}

export interface ClaimOut {
  lead_id: number;
  partner_id: number;
  partner_status: string;
  user_id: number | null;
  qualification: Record<string, unknown> | null;
  claimed_at: string | null;
}

export interface BatchClaimOut {
  claimed: ClaimOut[];
}

// --- Биллинг ---

export interface PlanInfo {
  key: "starter" | "pro" | "salon";
  title: string;
  daily_quota: number;
  price_usd: number;
}

export interface BillingOut {
  plan: string;
  daily_quota: number;
  claimed_today: number;
  quota_remaining: number;
  plans: PlanInfo[];
}
