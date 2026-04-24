import { AuthResponse } from "./types";

const SESSION_KEY = "bestshotai_web_session";
const LAST_PROJECT_KEY = "bestshotai_last_project";
const SESSION_EVENT = "bestshotai:session-changed";
const PROJECT_EVENT = "bestshotai:last-project-changed";

export function getStoredSession(): AuthResponse | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthResponse;
  } catch {
    return null;
  }
}

export function setStoredSession(session: AuthResponse): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  window.dispatchEvent(new CustomEvent(SESSION_EVENT));
}

export function clearStoredSession(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(SESSION_KEY);
  window.dispatchEvent(new CustomEvent(SESSION_EVENT));
}

export function getLastProjectId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(LAST_PROJECT_KEY);
}

export function setLastProjectId(projectId: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(LAST_PROJECT_KEY, projectId);
  window.dispatchEvent(new CustomEvent(PROJECT_EVENT));
}

export function clearLastProjectId(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(LAST_PROJECT_KEY);
  window.dispatchEvent(new CustomEvent(PROJECT_EVENT));
}

export function addSessionListener(listener: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const onStorage = (event: StorageEvent) => {
    if (event.key === SESSION_KEY) listener();
  };
  const onCustom = () => listener();
  window.addEventListener("storage", onStorage);
  window.addEventListener(SESSION_EVENT, onCustom);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(SESSION_EVENT, onCustom);
  };
}

export function addLastProjectListener(listener: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const onStorage = (event: StorageEvent) => {
    if (event.key === LAST_PROJECT_KEY) listener();
  };
  const onCustom = () => listener();
  window.addEventListener("storage", onStorage);
  window.addEventListener(PROJECT_EVENT, onCustom);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(PROJECT_EVENT, onCustom);
  };
}
