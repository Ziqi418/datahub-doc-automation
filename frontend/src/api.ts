import type { Analysis, CatalogItem, EntityKind, ReviewSelection } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
export async function uploadDocument(file: File): Promise<Analysis> { const form = new FormData(); form.append("file", file); return (await request<{ analysis: Analysis }>("/api/analyses", { method: "POST", body: form })).analysis; }
export const getRecommendations = (id: string) => request<Analysis>(`/api/analyses/${id}/recommend`, { method: "POST" });
export async function searchCatalog(kind: EntityKind, q: string): Promise<CatalogItem[]> { return (await request<{ items: CatalogItem[] }>(`/api/catalog/${kind}?q=${encodeURIComponent(q)}&limit=8`)).items; }
export async function saveReview(id: string, selection: ReviewSelection): Promise<Analysis> { return (await request<{ analysis: Analysis }>(`/api/analyses/${id}/review`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) })).analysis; }
