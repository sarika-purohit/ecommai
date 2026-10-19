import { ApiError } from "@/lib/api";
import { strings } from "@/lib/strings";

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.agentMessage ?? strings.errors[error.kind];
  return strings.errors.network;
}

export function canRetry(error: unknown): boolean {
  return error instanceof ApiError ? error.retryable : true;
}
