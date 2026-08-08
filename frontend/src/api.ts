import type { Analysis, CatalogItem, ConflictCandidate, DatasetCandidatesResponse, EntityKind, PublishResponse, ReviewSelection } from "./types";

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
export const getDatasetCandidates = (id: string) => request<DatasetCandidatesResponse>(`/api/analyses/${id}/dataset-candidates`);
export async function searchCatalog(kind: EntityKind, q: string): Promise<CatalogItem[]> { return (await request<{ items: CatalogItem[] }>(`/api/catalog/${kind}?q=${encodeURIComponent(q)}&limit=8`)).items; }
export async function saveReview(id: string, selection: ReviewSelection): Promise<Analysis> { return (await request<{ analysis: Analysis }>(`/api/analyses/${id}/review`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) })).analysis; }
export const publishAnalysis = (id: string) => request<PublishResponse>(`/api/analyses/${id}/publish`, { method: "POST" });
export const getConflicts = (id: string) => request<{ candidates: ConflictCandidate[] }>(`/api/analyses/${id}/conflicts`);
export const checkConflicts = (id: string) => request<{ candidates: ConflictCandidate[] }>(`/api/analyses/${id}/conflicts/check`, { method: "POST" });
export const confirmConflict = (id: string, urn: string) => request<{ candidates: ConflictCandidate[] }>(`/api/analyses/${id}/conflicts/${encodeURIComponent(urn)}/confirm`, { method: "PUT" });
