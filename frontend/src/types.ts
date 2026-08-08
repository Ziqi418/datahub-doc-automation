export type EntityKind = "domains" | "tags" | "owners" | "datasets";
export interface Evidence { kind: string; matched_text: string; location: string }
export interface Recommendation { urn: string; display_name: string; confidence: number; reason: string; evidence: Evidence[]; source: string }
export interface RecommendationSet { domain: Recommendation | null; tags: Recommendation[]; owner: Recommendation | null; datasets: Recommendation[] }
export interface DatasetCandidatesResponse { items: Recommendation[]; keyword_search_degraded: boolean }
export interface Analysis { id: string; source_filename: string; character_count: number; status: string; recommendations: RecommendationSet | null; error_code: string | null; review_started_at: string | null; review_completed_at: string | null }
export interface CatalogItem { urn: string; name: string; description: string; qualified_name: string | null; owner_type: string | null; title: string | null }
export interface SelectionItem { urn: string; name: string; detail?: string; recommendation?: Recommendation; userSelected?: boolean }
export interface ReviewSelection { domain_urn: string | null; tag_urns: string[]; owner_urn: string | null; dataset_urns: string[] }
export interface ConflictCandidate { document_urn: string; title: string; related_dataset_urns: string[]; score: number; evidence: string[]; detector_version: string; detected_at: string; high_risk: boolean; confirmed: boolean }
export interface PublishResponse { analysis: Analysis; document_urn: string; datahub_document_url: string; related_dataset_urls: string[] }
