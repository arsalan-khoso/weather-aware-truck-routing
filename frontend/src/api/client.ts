import type { AppConfig, Plan, PlanRequest } from '../types';

/** Error carrying the message the API chose to show the user. */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
  } catch (cause) {
    throw new ApiError('Could not reach the server. Is the backend running?', 0);
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      (body && typeof body.error === 'string' && body.error) ||
      `Request failed with status ${response.status}.`;
    throw new ApiError(message, response.status);
  }
  return body as T;
}

export function getConfig(): Promise<AppConfig> {
  return request<AppConfig>('/api/config');
}

export function planTrip(body: PlanRequest, signal?: AbortSignal): Promise<Plan> {
  return request<Plan>('/api/plan', {
    method: 'POST',
    body: JSON.stringify(body),
    signal,
  });
}
