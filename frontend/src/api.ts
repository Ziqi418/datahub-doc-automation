import type { Analysis, AnalysisListResponse, CatalogItem, ConflictCandidate, DatasetCandidatesResponse, EntityKind, FieldReviewResponse, FreshnessResponse, PublishResponse, RecentChangeCheckResponse, ReviewDraftSelection, ReviewSelection, SchemaValidationResponse } from "./types";

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
export async function saveReview(id: string, selection: ReviewDraftSelection): Promise<Analysis> { return (await request<{ analysis: Analysis }>(`/api/analyses/${id}/review`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) })).analysis; }
export const saveReviewDraft = (id: string, selection: ReviewDraftSelection) => request<Analysis>(`/api/analyses/${id}/review/draft`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) });
export const publishAnalysis = (id: string) => request<PublishResponse>(`/api/analyses/${id}/publish`, { method: "POST" });
export const getConflicts = (id: string) => request<{ candidates: ConflictCandidate[] }>(`/api/analyses/${id}/conflicts`);
export const checkConflicts = (id: string) => request<{ candidates: ConflictCandidate[] }>(`/api/analyses/${id}/conflicts/check`, { method: "POST" });
export const checkReviewFields = (id: string, selection: ReviewDraftSelection) => request<FieldReviewResponse>(`/api/analyses/${id}/review/field-check`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) });
export const checkReviewConflicts = (id: string, selection: ReviewDraftSelection) => request<{ candidates: ConflictCandidate[] }>(`/api/analyses/${id}/review/conflicts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) });
export const confirmConflict = (id: string, urn: string) => request<{ candidates: ConflictCandidate[] }>(`/api/analyses/${id}/conflicts/${encodeURIComponent(urn)}/confirm`, { method: "PUT" });
export const checkSchemaValidation = (id: string) => request<SchemaValidationResponse>(`/api/analyses/${id}/schema-validation`, { method: "POST" });
export const confirmSchemaReference = (id: string, referenceId: string) => request<SchemaValidationResponse>(`/api/analyses/${id}/schema-validation/${encodeURIComponent(referenceId)}/confirm`, { method: "PUT" });
export const checkFreshness = (id: string) => request<FreshnessResponse>(`/api/analyses/${id}/freshness`, { method: "POST" });
export const acknowledgeFreshness = (id: string) => request<Analysis>(`/api/analyses/${id}/freshness/acknowledge`, { method: "POST" });
export const getAnalyses = () => request<AnalysisListResponse>("/api/analyses");
export const getAnalysis = (id: string) => request<Analysis>(`/api/analyses/${id}`);
export const checkRecentDatabaseChanges = () => request<RecentChangeCheckResponse>("/api/freshness/recent-changes", { method: "POST" });
export const applyFreshnessSuggestion = (id: string, evidenceIndex: number) => request<Analysis>(`/api/analyses/${id}/freshness/apply-suggestion?evidence_index=${evidenceIndex}`, { method: "POST" });
export const returnToReview = (id: string) => request<Analysis>(`/api/analyses/${id}/return-to-review`, { method: "POST" });
