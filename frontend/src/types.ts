export type EntityKind = "domains" | "tags" | "owners" | "datasets";
export interface Evidence { kind: string; matched_text: string; location: string }
export interface Recommendation { urn: string; display_name: string; confidence: number; reason: string; evidence: Evidence[]; source: string }
export interface RecommendationSet { domain: Recommendation | null; tags: Recommendation[]; owner: Recommendation | null; datasets: Recommendation[] }
export interface DatasetCandidatesResponse { items: Recommendation[]; keyword_search_degraded: boolean }
export interface Analysis { id: string; source_filename: string; character_count: number; status: string; recommendations: RecommendationSet | null; final_selection?: ReviewDraftSelection | null; error_code: string | null; review_started_at: string | null; review_completed_at: string | null; updated_at: string; freshness_status?: string | null; last_freshness_checked_at?: string | null; freshness_evidence?: FreshnessEvidence[] }
export interface CatalogItem { urn: string; name: string; description: string; qualified_name: string | null; owner_type: string | null; title: string | null }
export interface SelectionItem { urn: string; name: string; detail?: string; recommendation?: Recommendation; userSelected?: boolean }
export interface ReviewSelection { domain_urn: string | null; tag_urns: string[]; owner_urn: string | null; dataset_urns: string[] }
export interface FieldDisposition { reference_id: string; action: "accept_suggestion" | "map_dataset" | "business_term" | "keep_unresolved"; dataset_urn?: string | null }
export interface ReviewDraftSelection extends ReviewSelection { field_dispositions: FieldDisposition[] }
export interface FieldSuggestion { reference_id: string; dataset_urn: string; confidence: number; reason: string }
export interface FieldReviewResponse { checked_at: string; references: FieldReference[]; suggestions: FieldSuggestion[]; provider_status: string }
export interface ConflictCandidate { document_urn: string; title: string; related_dataset_urns: string[]; score: number; evidence: string[]; detector_version: string; detected_at: string; high_risk: boolean; confirmed: boolean; semantic_classification: string; semantic_confidence: number | null; semantic_reason: string | null }
export interface PublishResponse { analysis: Analysis; document_urn: string; datahub_document_url: string; related_dataset_urls: string[] }
export interface FieldReference { id: string; raw_reference: string; field_path: string; table_or_alias: string | null; location: string; source: string; confidence: string; status: "resolved" | "ambiguous" | "unresolved"; candidate_dataset_urns: string[]; reason: string; high_risk: boolean; confirmed: boolean }
export interface SchemaValidationResponse { checked_at: string; references: FieldReference[] }
export interface FreshnessEvidence { dataset_urn: string; category: string; field_path: string | null; old_value: unknown; new_value: unknown; affects_referenced_field: boolean; message: string; recommendation_confidence?: number | null; proposed_content?: string | null }
export interface DatabaseChange { id: string; dataset_urn: string; kind: string; field_path: string; replacement_field_path: string | null; migration_id: string; summary: string }
export interface RecentChangeCheckResponse { changes: DatabaseChange[]; checked_analysis_ids: string[]; affected_analysis_ids: string[] }
export interface FreshnessResponse { analysis: Analysis; changed: boolean; differences: string[]; evidence: FreshnessEvidence[] }
export interface AnalysisListResponse { items: Analysis[]; total: number }
