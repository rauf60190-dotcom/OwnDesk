export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const csrf =
    document.cookie
      .split("; ")
      .find((x) => x.startsWith("csrftoken="))
      ?.split("=")[1] || "";
  const response = await fetch("/api/v1" + path, {
    ...options,
    credentials: "same-origin",
    headers: {
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      "X-CSRFToken": csrf,
      ...options.headers,
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new ApiError(
      response.status,
      data.message || data.detail || "Не удалось выполнить запрос.",
    );
  return data;
}
export const post = <T = any>(path: string, data: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(data) });
export const patch = <T = any>(path: string, data: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(data) });
export const statuses: Record<string, string> = {
  new: "Новое",
  in_progress: "В работе",
  waiting_customer: "Ожидаем клиента",
  waiting_specialist: "Ожидаем специалиста",
  resolved: "Решено",
  closed: "Закрыто",
};
export const ticketTransitions: Record<string, string[]> = {
  new: ["in_progress"],
  in_progress: ["waiting_customer", "waiting_specialist", "resolved"],
  waiting_customer: ["in_progress"],
  waiting_specialist: ["in_progress"],
  resolved: ["in_progress", "closed"],
  closed: [],
};
export const escalationLevels = { l1: "L1", l2: "L2", l3: "L3", development: "Разработчики" };
export type EscalationLevel = keyof typeof escalationLevels;
export const priorities: Record<string, string> = {
  low: "Низкий",
  normal: "Обычный",
  high: "Высокий",
  critical: "Критический",
};
export const queues: Record<string, string> = {
  active: "Активные",
  mine: "Мои",
  free: "Свободные",
  overdue: "Просроченные",
  all: "Все запросы",
};
export const roles: Record<string, string> = {
  admin: "Администратор",
  manager: "Руководитель",
  operator: "Оператор",
  observer: "Наблюдатель",
};
export type Ticket = {
  id: string;
  number: number;
  subject: string;
  description: string;
  escalation_level: EscalationLevel;
  status: string;
  priority: string;
  version: number;
  team_id: string;
  team_name: string;
  assignee_id: string | null;
  assignee_name: string | null;
  customer_name: string | null;
  company_name: string | null;
  conversation_id: string;
  conversation_title: string;
  telegram_connected: boolean;
  web_support?: boolean;
  unread: number;
  last_message: { text: string; author: string; visibility: string } | null;
  deadline: {
    at: string;
    kind: string;
    paused: boolean;
    overdue: boolean;
  } | null;
  created_at: string;
  updated_at: string;
  resolution: string;
};
export type Me = {
  deployment_mode: "portal" | "organization" | "demo";
  is_owner: boolean;
  id: string;
  name: string;
  username: string;
  role: string;
  membership_id: string;
  development: boolean;
  local_telegram: boolean;
  workspace: { id: string; name: string; timezone: string };
  preferences: Record<string, any>;
};
export type Page<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};
export function date(value: string, zone = "Asia/Samarkand") {
  return new Intl.DateTimeFormat("ru", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: zone,
  }).format(new Date(value));
}
