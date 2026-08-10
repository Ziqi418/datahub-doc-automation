import {
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Center,
  Checkbox,
  Container,
  FileInput,
  Group,
  List,
  MantineProvider,
  Progress,
  SimpleGrid,
  Stack,
  Stepper,
  Text,
  TextInput,
  ThemeIcon,
  Title,
  Tooltip,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import {
  IconAlertCircle,
  IconArrowLeft,
  IconArrowRight,
  IconCheck,
  IconCircleCheck,
  IconFileText,
  IconLoader2,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconSparkles,
  IconTrash,
  IconUpload,
} from "@tabler/icons-react";
import { useEffect, useState } from "react";
import {
  BrowserRouter,
  Link,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";
import {
  acknowledgeFreshness,
  applyFreshnessSuggestion,
  checkConflicts,
  checkFreshness,
  checkRecentDatabaseChanges,
  checkReviewConflicts,
  checkReviewFields,
  checkSchemaValidation,
  confirmConflict,
  confirmSchemaReference,
  deleteAnalysis,
  getAnalyses,
  getAnalysis,
  getConflicts,
  getDatasetCandidates,
  getRecommendations,
  publishAnalysis,
  returnToReview,
  saveReview,
  saveReviewDraft,
  searchCatalog,
  uploadDocument,
} from "./api";
import type {
  Analysis,
  CatalogItem,
  ConflictCandidate,
  EntityKind,
  FreshnessResponse,
  Recommendation,
  SchemaValidationResponse,
  FieldDisposition,
  FieldReviewResponse,
  SelectionItem,
} from "./types";

const MAX_MULTI_SELECTION = 20;
type Screen = "upload" | "analyzing" | "review" | "result";
const labels: Record<EntityKind, string> = {
  domains: "Domain",
  tags: "Tags",
  owners: "Owner",
  datasets: "Related datasets",
};
const toRecommended = (item: Recommendation): SelectionItem => ({
  urn: item.urn,
  name: item.display_name,
  detail: item.source === "rule" ? "Rule match" : "Model recommendation",
  recommendation: item,
});
const toCatalog = (item: CatalogItem): SelectionItem => ({
  urn: item.urn,
  name: item.name,
  detail:
    item.qualified_name ?? item.title ?? item.owner_type ?? item.description,
  userSelected: true,
});

function isDatasetBackedRecommendation(item: Recommendation | null | undefined) {
  return Boolean(item && item.confidence >= 0.8 && item.evidence.some((evidence) =>
    evidence.kind === "related_dataset_domain" || evidence.kind === "related_dataset_owner",
  ));
}

function isSqlDatasetRecommendation(item: Recommendation) {
  return item.evidence.some((evidence) => evidence.kind === "sql_table_reference");
}

function savedSelectionItem(urn: string, recommendations: Recommendation[] = []): SelectionItem {
  const recommendation = recommendations.find((item) => item.urn === urn);
  if (recommendation) return toRecommended(recommendation);
  return {
    urn,
    name: urn.split(",")[1]?.replace(/[()]/g, "") ?? urn,
    detail: "Saved selection",
    userSelected: true,
  };
}

export function Workflow({ analysisId }: { analysisId?: string }) {
  const navigate = useNavigate();
  const [screen, setScreen] = useState<Screen>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [domain, setDomain] = useState<SelectionItem | null>(null);
  const [owner, setOwner] = useState<SelectionItem | null>(null);
  const [tags, setTags] = useState<SelectionItem[]>([]);
  const [datasets, setDatasets] = useState<SelectionItem[]>([]);
  const [datasetCandidates, setDatasetCandidates] = useState<Recommendation[]>(
    [],
  );
  const [keywordSearchDegraded, setKeywordSearchDegraded] = useState(false);
  const [fieldDispositions, setFieldDispositions] = useState<FieldDisposition[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [loadingReview, setLoadingReview] = useState(Boolean(analysisId));
  const [analysisProgress, setAnalysisProgress] = useState(12);
  const active =
    screen === "upload"
      ? 0
      : screen === "analyzing"
        ? 1
        : screen === "review"
          ? 2
          : 3;
  function applyRecommendations(next: Analysis) {
    const recs = next.recommendations;
    setAnalysis(next);
    setDomain(recs?.domain && isDatasetBackedRecommendation(recs.domain) ? toRecommended(recs.domain) : null);
    setOwner(recs?.owner && isDatasetBackedRecommendation(recs.owner) ? toRecommended(recs.owner) : null);
    setTags([]);
    setDatasets(recs?.datasets.filter(isSqlDatasetRecommendation).map(toRecommended) ?? []);
  }
  useEffect(() => {
    const id = analysisId;
    if (typeof id !== "string") {
      setLoadingReview(false);
      return;
    }
    const reviewId: string = id;
    let cancelled = false;
    async function resumeDraft() {
      try {
        const saved = await getAnalysis(reviewId);
        if (saved.status !== "READY_FOR_REVIEW" || !saved.recommendations) {
          throw new Error("This document is not available for review.");
        }
        const candidates = await getDatasetCandidates(reviewId);
        if (cancelled) return;
        const draft = saved.final_selection;
        if (draft) {
          setAnalysis(saved);
          setDomain(draft.domain_urn ? savedSelectionItem(draft.domain_urn, [saved.recommendations.domain].filter(Boolean) as Recommendation[]) : null);
          setOwner(draft.owner_urn ? savedSelectionItem(draft.owner_urn, [saved.recommendations.owner].filter(Boolean) as Recommendation[]) : null);
          setTags(draft.tag_urns.map((urn) => savedSelectionItem(urn, saved.recommendations!.tags)));
          setDatasets(draft.dataset_urns.map((urn) => savedSelectionItem(urn, saved.recommendations!.datasets)));
          setFieldDispositions(draft.field_dispositions);
        } else {
          applyRecommendations(saved);
        }
        setDatasetCandidates(candidates.items);
        setKeywordSearchDegraded(candidates.keyword_search_degraded);
        setScreen("review");
        setLoadingReview(false);
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Could not resume this review.");
          setLoadingReview(false);
        }
      }
    }
    void resumeDraft();
    return () => {
      cancelled = true;
    };
  }, [analysisId]);
  async function analyze() {
    if (!file) {
      setError("Choose a Markdown or text file before continuing.");
      return;
    }
    const ext = file.name.split(".").pop()?.toLowerCase();
    if (!["md", "txt"].includes(ext ?? "")) {
      setError("Only .md and .txt files are supported.");
      return;
    }
    if (file.size > 256 * 1024) {
      setError("File exceeds the 256 KiB limit.");
      return;
    }
    setError(null);
    setAnalysisProgress(8);
    setScreen("analyzing");
    try {
      const uploaded = await uploadDocument(file);
      setAnalysisProgress(35);
      const ready = await getRecommendations(uploaded.id);
      setAnalysisProgress(75);
      applyRecommendations(ready);
      const candidates = await getDatasetCandidates(uploaded.id);
      setAnalysisProgress(95);
      setDatasetCandidates(candidates.items);
      setKeywordSearchDegraded(candidates.keyword_search_degraded);
      setAnalysisProgress(100);
      await new Promise((resolve) => window.setTimeout(resolve, 400));
      setScreen("review");
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Analysis could not be completed.",
      );
      setScreen("upload");
    }
  }
  function currentSelection() {
    return { domain_urn: domain?.urn ?? null, tag_urns: tags.map((x) => x.urn), owner_urn: owner?.urn ?? null,
      dataset_urns: datasets.map((x) => x.urn), field_dispositions: fieldDispositions };
  }
  async function submitReview() {
    if (!analysis) return;
    setError(null);
    setIsSaving(true);
    try {
      const updated = await saveReview(analysis.id, currentSelection());
      setAnalysis(updated);
      setScreen("result");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Review could not be saved.",
      );
    } finally {
      setIsSaving(false);
    }
  }
  async function saveDraft(): Promise<boolean> {
    if (!analysis) return false;
    setIsSaving(true);
    try {
      setAnalysis(await saveReviewDraft(analysis.id, currentSelection()));
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Draft could not be saved.");
      return false;
    } finally {
      setIsSaving(false);
    }
  }
  async function saveDraftAndExit() {
    if (await saveDraft()) navigate("/documents");
  }
  function restart() {
    setScreen("upload");
    setFile(null);
    setAnalysis(null);
    setDomain(null);
    setOwner(null);
    setTags([]);
    setDatasets([]);
    setDatasetCandidates([]);
    setKeywordSearchDegraded(false);
    setFieldDispositions([]);
    setError(null);
  }
  async function backToReview() {
    if (!analysis) return;
    try {
      setAnalysis(await returnToReview(analysis.id));
      setScreen("review");
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not return to review.",
      );
    }
  }
  return (
    <MantineProvider
      forceColorScheme="light"
      theme={{
        primaryColor: "indigo",
        defaultRadius: "md",
        fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
        headings: { fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif" },
      }}
    >
      <Box className="app-shell">
        <Container size="lg" py={{ base: "xl", sm: 56 }}>
          <header className="app-header">
            <Group gap="sm">
              <ThemeIcon size={36} radius="md" variant="light">
                <IconSparkles size={19} />
              </ThemeIcon>
              <div>
                <Text fw={700} size="sm">
                  DataHub document enrichment
                </Text>
                <Text c="dimmed" size="xs">
                  Human review workspace
                </Text>
              </div>
            </Group>
            <Group gap="sm">
              <Button
                component={Link}
                to="/documents"
                variant="subtle"
                size="compact-sm"
              >
                Documents
              </Button>
              <Badge variant="outline" color="gray">
                Review-first
              </Badge>
            </Group>
          </header>
          <main>
            <section className="hero">
              <Text className="eyebrow">DOCUMENT METADATA</Text>
              <Title order={1}>
                Review recommendations
                <br />
                before they reach your catalog.
              </Title>
              <Text c="dimmed" maw={590} mt="md">
                Match a document with the existing DataHub domain, tags, owner
                and datasets. Nothing is published automatically.
              </Text>
            </section>
            <Stepper
              active={active}
              allowNextStepsSelect={false}
              className="workflow-stepper"
              mb="xl"
            >
              <Stepper.Step label="Upload" description="Choose document" />
              <Stepper.Step label="Analyze" description="Find matches" />
              <Stepper.Step label="Review" description="Confirm metadata" />
              <Stepper.Step label="Saved" description="Ready to publish" />
            </Stepper>
            {error && (
              <Alert
                color="red"
                variant="light"
                icon={<IconAlertCircle size={18} />}
                mb="lg"
                withCloseButton
                onClose={() => setError(null)}
              >
                {error}
              </Alert>
            )}
            {loadingReview && <ReviewLoadingPanel />}
            {!loadingReview && screen === "upload" && (
              <UploadPanel
                file={file}
                onFileChange={setFile}
                onAnalyze={analyze}
              />
            )}
            {!loadingReview && screen === "analyzing" && (
                <AnalyzingPanel fileName={file?.name ?? "document"} progress={analysisProgress} />
            )}
            {!loadingReview && screen === "review" && analysis && (
              <ReviewPanel
                analysis={analysis}
                domain={domain}
                owner={owner}
                tags={tags}
                datasets={datasets}
                datasetCandidates={datasetCandidates}
                recommendedTags={analysis.recommendations?.tags ?? []}
                recommendedDatasets={analysis.recommendations?.datasets ?? []}
                keywordSearchDegraded={keywordSearchDegraded}
                onDomainChange={setDomain}
                onOwnerChange={setOwner}
                onTagsChange={setTags}
                onDatasetsChange={setDatasets}
                fieldDispositions={fieldDispositions}
                onFieldDispositionsChange={setFieldDispositions}
                onSave={submitReview}
                onSaveDraftAndExit={saveDraftAndExit}
                isSaving={isSaving}
              />
            )}
            {!loadingReview && screen === "result" && analysis && (
              <ResultPanel
                analysis={analysis}
                domain={domain}
                owner={owner}
                tags={tags}
                datasets={datasets}
                onRestart={restart}
                onBackToReview={backToReview}
              />
            )}
          </main>
        </Container>
      </Box>
    </MantineProvider>
  );
}

const workspaceTheme = {
  primaryColor: "indigo",
  defaultRadius: "md",
  fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
  headings: { fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif" },
};
function WorkspaceFrame({ children }: { children: React.ReactNode }) {
  return (
    <MantineProvider theme={workspaceTheme} forceColorScheme="light">
      <Box className="app-shell">
        <Container size="lg" py={{ base: "xl", sm: 56 }}>
          <header className="app-header">
            <Group gap="sm">
              <ThemeIcon size={36} radius="md" variant="light">
                <IconSparkles size={19} />
              </ThemeIcon>
              <div>
                <Text fw={700} size="sm">
                  DataHub document enrichment
                </Text>
                <Text c="dimmed" size="xs">
                  Document workspace
                </Text>
              </div>
            </Group>
            <Button
              component={Link}
              to="/documents/new"
              leftSection={<IconUpload size={15} />}
            >
              New document
            </Button>
          </header>
          {children}
        </Container>
      </Box>
    </MantineProvider>
  );
}

function freshnessColor(status?: string | null) {
  return status === "NEEDS_REVIEW"
    ? "orange"
    : status === "ACKNOWLEDGED"
      ? "yellow"
      : status === "ACTIVE"
        ? "green"
        : "gray";
}
function DocumentsPage() {
  const [items, setItems] = useState<Analysis[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const load = () =>
    getAnalyses()
      .then((result) => setItems(result.items))
      .catch((caught) =>
        setError(
          caught instanceof Error
            ? caught.message
            : "Could not load documents.",
        ),
      );
  useEffect(() => {
    void load();
  }, []);
  async function checkRecentChanges() {
    setChecking(true);
    setError(null);
    try {
      const result = await checkRecentDatabaseChanges();
      await load();
      if (result.affected_analysis_ids.length === 0)
        setError(
          "Recent migrations did not affect any published document references.",
        );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not check recent database changes.",
      );
    } finally {
      setChecking(false);
    }
  }
  async function removeDocument(item: Analysis) {
    const message = item.status === "PUBLISHED"
      ? "Delete this document from both the workspace and DataHub?"
      : "Delete this draft from the workspace? This cannot be undone.";
    if (!window.confirm(message)) return;
    setDeletingId(item.id);
    setError(null);
    try {
      await deleteAnalysis(item.id);
      setItems((current) => current.filter((candidate) => candidate.id !== item.id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete this document.");
    } finally {
      setDeletingId(null);
    }
  }
  return (
    <WorkspaceFrame>
      <main>
        <section className="page-heading">
          <Group justify="space-between" align="end" wrap="wrap">
            <div>
              <Text className="eyebrow">DOCUMENTS</Text>
              <Title order={1}>Document workspace</Title>
              <Text c="dimmed" mt="xs">
                Check only documents related to recent database migrations.
              </Text>
            </div>
            <Button
              loading={checking}
              leftSection={<IconRefresh size={16} />}
              onClick={checkRecentChanges}
            >
              Check recent database changes
            </Button>
          </Group>
        </section>
        {error && (
          <Alert
            color={error.startsWith("Recent migrations") ? "blue" : "red"}
            mb="lg"
          >
            {error}
          </Alert>
        )}
        <Stack gap="sm">
          {items.length === 0 && (
            <Card className="surface-card" withBorder padding="xl">
              <Title order={2} size="h3">
                No documents yet
              </Title>
              <Text c="dimmed" mt="xs">
                Upload a Markdown or text document to start a review.
              </Text>
            </Card>
          )}
          {items.map((item) => (
            <Card
              key={item.id}
              className="surface-card document-row"
              withBorder
              padding="md"
            >
              <Group justify="space-between" wrap="nowrap">
                <Button
                  component={Link}
                  to={item.status === "READY_FOR_REVIEW" ? `/documents/${item.id}/review` : `/documents/${item.id}`}
                  variant="subtle"
                  color="dark"
                  p={0}
                  h="auto"
                  justify="flex-start"
                  className="document-row-link"
                  style={{ flex: 1, minWidth: 0 }}
                >
                  <div className="document-row-copy">
                    <Group gap="xs" wrap="nowrap" align="center">
                      <Text fw={650}>{item.source_filename}</Text>
                      <Badge color={freshnessColor(item.freshness_status)} variant="light">
                        {item.freshness_status?.replaceAll("_", " ") ?? "NOT CHECKED"}
                      </Badge>
                    </Group>
                    <Text size="xs" c="dimmed" mt={3}>
                      {item.status.replaceAll("_", " ")} · Updated{" "}
                      {new Intl.DateTimeFormat(undefined, {
                        dateStyle: "medium",
                      }).format(new Date(item.updated_at))}
                    </Text>
                  </div>
                </Button>
                <Group gap="xs">
                  <Tooltip label={item.status === "PUBLISHED" ? "Delete from workspace and DataHub" : "Delete draft"}>
                    <Button
                      variant="subtle"
                      color="gray"
                      aria-label={`Delete ${item.source_filename}`}
                      p={5}
                      h="auto"
                      loading={deletingId === item.id}
                      onClick={() => void removeDocument(item)}
                    >
                      <IconTrash size={17} />
                    </Button>
                  </Tooltip>
                </Group>
              </Group>
            </Card>
          ))}
        </Stack>
      </main>
    </WorkspaceFrame>
  );
}

function DocumentDetailPage() {
  const { id = "" } = useParams();
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getAnalysis(id)
      .then(setAnalysis)
      .catch((caught) =>
        setError(
          caught instanceof Error
            ? caught.message
            : "Could not load this document.",
        ),
      );
  }, [id]);
  async function check() {
    setChecking(true);
    setError(null);
    try {
      setAnalysis((await checkFreshness(id)).analysis);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Freshness check failed.",
      );
    } finally {
      setChecking(false);
    }
  }
  async function acknowledge() {
    try {
      setAnalysis(await acknowledgeFreshness(id));
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not acknowledge the changes.",
      );
    }
  }
  return (
    <WorkspaceFrame>
      <main>
        {error && (
          <Alert color="red" mb="lg">
            {error}
          </Alert>
        )}
        {!analysis ? (
          <Center py="xl">
            <IconLoader2 className="spin" />
          </Center>
        ) : (
          <>
            <Button
              component={Link}
              to="/documents"
              variant="subtle"
              leftSection={<IconArrowLeft size={15} />}
            >
              All documents
            </Button>
            <section className="page-heading">
              <Text className="eyebrow">DOCUMENT</Text>
              <Group justify="space-between" align="start" wrap="wrap">
                <div>
                  <Title order={1}>{analysis.source_filename}</Title>
                  <Text c="dimmed" mt="xs">
                    {analysis.status.replaceAll("_", " ")} ·{" "}
                    {analysis.character_count.toLocaleString()} characters
                  </Text>
                </div>
                <Badge
                  size="lg"
                  color={freshnessColor(analysis.freshness_status)}
                  variant="light"
                >
                  {analysis.freshness_status?.replaceAll("_", " ") ??
                    "NOT CHECKED"}
                </Badge>
              </Group>
            </section>
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              <Card className="surface-card" withBorder padding="lg">
                <Text fw={650}>Source & review</Text>
                <Text size="sm" c="dimmed" mt="xs">
                  Download the original source or start a new review from the
                  workspace.
                </Text>
                <Group mt="md">
                  <Button
                    component="a"
                    href={`/api/analyses/${analysis.id}/source`}
                    download
                    variant="default"
                  >
                    Download source
                  </Button>
                  <Button component={Link} to="/documents/new" variant="subtle">
                    New review
                  </Button>
                  {analysis.status === "READY_FOR_REVIEW" && (
                    <Button component={Link} to={`/documents/${analysis.id}/review`} variant="default">
                      Continue review
                    </Button>
                  )}
                </Group>
              </Card>
              <Card className="surface-card" withBorder padding="lg">
                <Text fw={650}>Freshness</Text>
                <Text size="sm" c="dimmed" mt="xs">
                  Checks this document’s published Dataset baseline against the
                  current DataHub catalog.
                </Text>
                <Button
                  mt="md"
                  loading={checking}
                  disabled={analysis.status !== "PUBLISHED"}
                  leftSection={<IconRefresh size={16} />}
                  onClick={check}
                >
                  Check freshness
                </Button>
                {analysis.status !== "PUBLISHED" && (
                  <Text size="xs" c="dimmed" mt="xs">
                    Available after publishing.
                  </Text>
                )}
              </Card>
            </SimpleGrid>
            <Card className="surface-card" withBorder padding="lg" mt="md">
              <Group justify="space-between">
                <div>
                  <Text fw={650}>Detected changes</Text>
                  <Text size="sm" c="dimmed">
                    Changes are retained after a check so reviewers can return
                    later.
                  </Text>
                </div>
                {analysis.freshness_status === "NEEDS_REVIEW" && (
                  <Button variant="default" onClick={acknowledge}>
                    Acknowledge changes
                  </Button>
                )}
              </Group>
              {analysis.freshness_evidence?.length ? (
                <Stack gap="xs" mt="md">
                  {analysis.freshness_evidence.map((item, index) => (
                    <Alert
                      key={`${item.dataset_urn}-${item.category}-${index}`}
                      color={item.affects_referenced_field ? "orange" : "blue"}
                    >
                      <Text fw={600} size="sm">
                        {item.category.replaceAll("_", " ")}
                        {item.affects_referenced_field
                          ? " · referenced by this document"
                          : ""}
                      </Text>
                      <Text size="sm">{item.message}</Text>
                    </Alert>
                  ))}
                </Stack>
              ) : (
                <Text size="sm" c="dimmed" mt="md">
                  No recorded changes yet. Run a freshness check after
                  publishing.
                </Text>
              )}
            </Card>
          </>
        )}
      </main>
    </WorkspaceFrame>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<DocumentsPage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/documents/new" element={<Workflow />} />
        <Route path="/documents/:id/review" element={<ResumeReviewPage />} />
        <Route path="/documents/:id" element={<DocumentDetailPage />} />
      </Routes>
    </BrowserRouter>
  );
}
function ResumeReviewPage() {
  const { id } = useParams();
  return <Workflow analysisId={id} />;
}
function UploadPanel({
  file,
  onFileChange,
  onAnalyze,
}: {
  file: File | null;
  onFileChange: (file: File | null) => void;
  onAnalyze: () => void;
}) {
  return (
    <Card className="surface-card upload-card" padding="xl" withBorder>
      <Stack gap="lg" align="center">
        <ThemeIcon size={48} radius="xl" variant="light">
          <IconUpload size={22} />
        </ThemeIcon>
        <div className="center-copy">
          <Title order={2} size="h3">
            Start with a source document
          </Title>
          <Text c="dimmed" size="sm" mt={6}>
            Markdown or plain text · UTF-8 · up to 256 KiB / 30,000 characters
          </Text>
        </div>
        <FileInput
          aria-label="Source document"
          data-testid="file-input"
          accept=".md,.txt,text/markdown,text/plain"
          placeholder="Choose a .md or .txt file"
          value={file}
          onChange={onFileChange}
          leftSection={<IconFileText size={16} />}
          w="min(100%, 470px)"
          clearable
        />
        <Button
          rightSection={<IconArrowRight size={16} />}
          onClick={onAnalyze}
          size="md"
        >
          Analyze document
        </Button>
      </Stack>
    </Card>
  );
}
function AnalyzingPanel({ fileName, progress }: { fileName: string; progress: number }) {
  return (
    <Card className="surface-card" padding="xl" withBorder>
      <Center>
        <Stack align="center" gap="md" py="xl">
          <ThemeIcon size={48} radius="xl" variant="light">
            <IconLoader2 className="spin" size={23} />
          </ThemeIcon>
          <div className="center-copy">
            <Title order={2} size="h3">
              Analyzing {fileName}
            </Title>
            <Text c="dimmed" size="sm" mt={6}>
              Reading DataHub, matching rules, then ranking candidates.
            </Text>
          </div>
          <Progress
            value={progress}
            animated
            transitionDuration={400}
            w={260}
            aria-label="Analysis in progress"
          />
        </Stack>
      </Center>
    </Card>
  );
}
function ReviewLoadingPanel() {
  return (
    <Card className="surface-card" padding="xl" withBorder>
      <Center>
        <Stack align="center" gap="md" py="xl">
          <ThemeIcon size={48} radius="xl" variant="light">
            <IconLoader2 className="spin" size={22} />
          </ThemeIcon>
          <div className="center-copy">
            <Title order={2} size="h3">Loading saved review</Title>
            <Text c="dimmed" size="sm" mt={6}>Restoring your selections and review context.</Text>
          </div>
        </Stack>
      </Center>
    </Card>
  );
}
function ReviewPanel({
  analysis,
  domain,
  owner,
  tags,
  datasets,
  datasetCandidates,
  recommendedTags,
  recommendedDatasets,
  keywordSearchDegraded,
  onDomainChange,
  onOwnerChange,
  onTagsChange,
  onDatasetsChange,
  fieldDispositions,
  onFieldDispositionsChange,
  onSave,
  onSaveDraftAndExit,
  isSaving,
}: {
  analysis: Analysis;
  domain: SelectionItem | null;
  owner: SelectionItem | null;
  tags: SelectionItem[];
  datasets: SelectionItem[];
  datasetCandidates: Recommendation[];
  recommendedTags: Recommendation[];
  recommendedDatasets: Recommendation[];
  keywordSearchDegraded: boolean;
  onDomainChange: (x: SelectionItem | null) => void;
  onOwnerChange: (x: SelectionItem | null) => void;
  onTagsChange: (x: SelectionItem[]) => void;
  onDatasetsChange: (x: SelectionItem[]) => void;
  fieldDispositions: FieldDisposition[];
  onFieldDispositionsChange: (x: FieldDisposition[]) => void;
  onSave: () => void;
  onSaveDraftAndExit: () => void;
  isSaving: boolean;
}) {
  return (
    <div className="review-layout">
      <aside className="document-summary">
        <Text className="eyebrow">SOURCE</Text>
        <Title order={2} size="h3" mt={8}>
          {analysis.source_filename}
        </Title>
        <Text size="sm" c="dimmed" mt="xs">
          {analysis.character_count.toLocaleString()} characters
        </Text>
        <div className="summary-divider" />
        <Text size="sm" fw={600}>
          Review guidance
        </Text>
        <Text size="sm" c="dimmed" mt={6}>
          Evidence strength is not a calibrated probability. Verify associations
          against the source before saving.
        </Text>
      </aside>
      <section className="review-content" aria-label="Metadata review">
        <Group className="recommendation-heading" justify="space-between" mb="md" align="end">
          <div>
            <Text className="eyebrow">RECOMMENDATIONS</Text>
            <Title order={2} size="h3" mt={6}>
              Confirm the document context
            </Title>
          </div>
          <Text size="xs" c="dimmed">
            Constraints enforced
          </Text>
        </Group>
        {keywordSearchDegraded && (
          <Alert color="yellow" mb="md">
            DataHub keyword search is unavailable; showing deterministic
            evidence-only candidates.
          </Alert>
        )}
        <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
          <ReviewCard
            kind="domains"
            selected={domain ? [domain] : []}
            onChange={(x) => onDomainChange(x[0] ?? null)}
          />
          <ReviewCard
            kind="owners"
            selected={owner ? [owner] : []}
            onChange={(x) => onOwnerChange(x[0] ?? null)}
          />
          <ReviewCard kind="tags" selected={tags} recommended={recommendedTags} onChange={onTagsChange} />
          <ReviewCard
            kind="datasets"
            selected={datasets}
            candidates={datasetCandidates}
            recommended={recommendedDatasets}
            onChange={onDatasetsChange}
          />
        </SimpleGrid>
        <ReviewIntelligence
          analysis={analysis}
          selection={{ domain_urn: domain?.urn ?? null, owner_urn: owner?.urn ?? null, tag_urns: tags.map((item) => item.urn), dataset_urns: datasets.map((item) => item.urn), field_dispositions: fieldDispositions }}
          fieldDispositions={fieldDispositions}
          onFieldDispositionsChange={onFieldDispositionsChange}
        />
        <Card className="save-bar" withBorder mt="lg" padding="md">
          <Group justify="space-between" align="center" wrap="wrap">
            <Text size="sm" c="dimmed">Save this review and return to the document workspace, or continue to the publishing preview.</Text>
            <Group gap="xs"><Button variant="default" loading={isSaving} onClick={onSaveDraftAndExit}>Save & exit</Button><Button data-testid="save-review" loading={isSaving} leftSection={<IconCheck size={16} />} onClick={onSave}>Preview & publish</Button></Group>
          </Group>
        </Card>
      </section>
    </div>
  );
}
function ReviewCard({
  kind,
  selected,
  candidates = [],
  recommended = [],
  onChange,
}: {
  kind: EntityKind;
  selected: SelectionItem[];
  candidates?: Recommendation[];
  recommended?: Recommendation[];
  onChange: (x: SelectionItem[]) => void;
}) {
  const multi = kind === "tags" || kind === "datasets";
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [debouncedQuery] = useDebouncedValue(query, 300);
  const [results, setResults] = useState<CatalogItem[]>([]);
  const [searching, setSearching] = useState(false);
  useEffect(() => {
    let cancelled = false;
    if (debouncedQuery.trim().length < 2) {
      setResults([]);
      return;
    }
    setSearching(true);
    searchCatalog(kind, debouncedQuery)
      .then((x) => {
        if (!cancelled) setResults(x);
      })
      .catch(() => {
        if (!cancelled) setResults([]);
      })
      .finally(() => {
        if (!cancelled) setSearching(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery, kind]);
  function addSelection(candidate: SelectionItem) {
    if (selected.some((x) => x.urn === candidate.urn)) return;
    onChange(
      multi
        ? [...selected, candidate].slice(0, MAX_MULTI_SELECTION)
        : [candidate],
    );
  }
  function add(item: CatalogItem) {
    addSelection(toCatalog(item));
    setQuery("");
    setResults([]);
  }
  const unselectedRecommended = recommended.filter(
    (item) => !selected.some((selection) => selection.urn === item.urn),
  );
  const unselectedCandidates = candidates.filter(
    (item) =>
      !selected.some((selection) => selection.urn === item.urn) &&
      !recommended.some((recommendation) => recommendation.urn === item.urn),
  );
  const visibleCandidates = expanded ? unselectedCandidates : unselectedCandidates.slice(0, 5);
  return (
    <Card className="surface-card review-card" withBorder padding="lg">
      <Group justify="space-between" align="start" mb="sm">
        <div>
          <Text fw={650}>{labels[kind]}</Text>
          <Text size="xs" c="dimmed">
            {multi
              ? `Select up to ${MAX_MULTI_SELECTION}`
              : "Select one existing entity"}
          </Text>
        </div>
        {multi && (
          <Group gap={4}>
            <Badge variant="light" color={selected.length === MAX_MULTI_SELECTION ? "orange" : "gray"}>{selected.length} / {MAX_MULTI_SELECTION}</Badge>
            {selected.length > 0 && <Button size="compact-xs" variant="subtle" color="gray" onClick={() => onChange([])}>Clear all</Button>}
          </Group>
        )}
      </Group>
      <Stack gap="xs">
        {selected.map((item) => (
          <SelectedItem
            key={item.urn}
            item={item}
            onRemove={() =>
              onChange(selected.filter((x) => x.urn !== item.urn))
            }
          />
        ))}
      </Stack>
      {multi && unselectedRecommended.length > 0 && (
        <Stack gap={4} mt="sm">
          <Text size="xs" c="dimmed">Recommended</Text>
          {unselectedRecommended.map((item) => (
            <RecommendationOption
              key={item.urn}
              item={item}
              disabled={selected.length >= MAX_MULTI_SELECTION}
              onSelect={() => addSelection(toRecommended(item))}
            />
          ))}
        </Stack>
      )}
      {kind === "datasets" && unselectedCandidates.length > 0 && (
        <Stack gap={4} mt="sm">
          <Text size="xs" c="dimmed">
            {unselectedCandidates.length === 1
              ? "1 evidence-backed candidate"
              : `Top ${Math.min(5, unselectedCandidates.length)} of ${unselectedCandidates.length} evidence-backed candidates`}
          </Text>
          {visibleCandidates.map((item) => (
              <Button
                key={item.urn}
                size="compact-xs"
                variant="subtle"
                justify="space-between"
                onClick={() => addSelection(toRecommended(item))}
              >
                {item.display_name}
                <span>{Math.round(item.confidence * 100)}% evidence</span>
              </Button>
            ))}
          {unselectedCandidates.length > 5 && (
            <Button
              size="compact-xs"
              variant="default"
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded
                ? "Show default 5"
                : `Show all ${unselectedCandidates.length} candidates`}
            </Button>
          )}
        </Stack>
      )}
      <div className="catalog-search">
        <TextInput
          label={`Search ${labels[kind]}`}
          description="Type at least 2 characters"
          placeholder={`Find a ${labels[kind].toLowerCase().replace("related ", "")}`}
          value={query}
          onChange={(event) => setQuery(event.currentTarget.value)}
          leftSection={<IconSearch size={15} />}
          rightSection={
            searching ? <IconLoader2 className="spin" size={15} /> : null
          }
        />
        {results.length > 0 && (
          <div
            className="search-results"
            role="listbox"
            aria-label={`${labels[kind]} search results`}
          >
            {results.map((item) => {
              const disabled =
                selected.some((x) => x.urn === item.urn) ||
                (multi && selected.length >= MAX_MULTI_SELECTION);
              return (
                <button
                  type="button"
                  role="option"
                  key={item.urn}
                  disabled={disabled}
                  onClick={() => add(item)}
                  className="search-result"
                >
                  <span>
                    <strong>{item.name}</strong>
                    <small>
                      {item.qualified_name ??
                        item.title ??
                        item.owner_type ??
                        item.description}
                    </small>
                  </span>
                  <IconPlus size={16} />
                </button>
              );
            })}
          </div>
        )}
      </div>
    </Card>
  );
}

function ReviewIntelligence({
  analysis,
  selection,
  fieldDispositions,
  onFieldDispositionsChange,
}: {
  analysis: Analysis;
  selection: { domain_urn: string | null; owner_urn: string | null; tag_urns: string[]; dataset_urns: string[]; field_dispositions: FieldDisposition[] };
  fieldDispositions: FieldDisposition[];
  onFieldDispositionsChange: (items: FieldDisposition[]) => void;
}) {
  const [fieldReview, setFieldReview] = useState<FieldReviewResponse | null>(null);
  const [conflicts, setConflicts] = useState<ConflictCandidate[] | null>(null);
  const [checkingFields, setCheckingFields] = useState(false);
  const [checkingConflicts, setCheckingConflicts] = useState(false);
  const [ignoredConflictUrns, setIgnoredConflictUrns] = useState<string[]>([]);
  const nameFor = (urn: string) => urn.split(",")[1]?.replace(/[()]/g, "") ?? urn;
  async function checkFields() {
    setCheckingFields(true);
    try {
      setFieldReview(await checkReviewFields(analysis.id, selection));
    } finally {
      setCheckingFields(false);
    }
  }
  async function checkSimilarDocuments() {
    setCheckingConflicts(true);
    try {
      setConflicts((await checkReviewConflicts(analysis.id, selection)).candidates);
    } finally {
      setCheckingConflicts(false);
    }
  }
  function decide(referenceId: string, action: FieldDisposition["action"], datasetUrn?: string) {
    onFieldDispositionsChange([
      ...fieldDispositions.filter((item) => item.reference_id !== referenceId),
      { reference_id: referenceId, action, dataset_urn: datasetUrn ?? null },
    ]);
  }
  const needsDecision = fieldReview?.references.filter((item) => item.status !== "resolved") ?? [];
  return (
    <Stack mt="lg" gap="md">
      <Card className="surface-card" withBorder padding="lg">
        <Group justify="space-between" align="start" wrap="wrap">
          <div>
            <Text fw={650}>Field validation</Text>
            <Text size="sm" c="dimmed">Validate explicit field references against the selected Dataset schemas. Only ambiguous fields need a mapping decision.</Text>
          </div>
          <Button variant="default" loading={checkingFields} onClick={() => void checkFields()} disabled={selection.dataset_urns.length === 0}>Check field validation</Button>
        </Group>
        {fieldReview && (
          <Stack mt="md" gap="xs">
            {fieldReview.provider_status !== "available" && fieldReview.provider_status !== "cached" && (
              <Alert color="yellow">Model suggestions are unavailable. Choose a Dataset, mark a business term, or explicitly keep unresolved.</Alert>
            )}
            {needsDecision.length === 0 ? <Text size="sm" c="dimmed">No ambiguous or unresolved explicit fields were found.</Text> : needsDecision.map((reference) => {
              const suggestion = fieldReview.suggestions.find((item) => item.reference_id === reference.id);
              const decision = fieldDispositions.find((item) => item.reference_id === reference.id);
              return <Card key={reference.id} withBorder padding="sm">
                <Group justify="space-between" align="start" wrap="wrap">
                  <div><Text fw={600} size="sm">{reference.raw_reference} · {reference.location}</Text><Text size="xs" c="dimmed">{suggestion ? suggestion.reason : reference.reason}</Text></div>
                  {decision && <Badge color="green" variant="light">{decision.action.replaceAll("_", " ")}</Badge>}
                </Group>
                {suggestion && <Group mt="sm" gap="xs"><Text size="xs" c="dimmed">Recommended Dataset</Text><Badge variant="light">{nameFor(suggestion.dataset_urn)} · {Math.round(suggestion.confidence * 100)}%</Badge><Button size="compact-xs" variant="light" onClick={() => decide(reference.id, "accept_suggestion", suggestion.dataset_urn)}>Accept</Button></Group>}
                <Group mt="xs" gap="xs" wrap="wrap">
                  <Button size="compact-xs" variant="light" onClick={() => decide(reference.id, "business_term")}>Business term</Button>
                  <Button size="compact-xs" variant="light" onClick={() => decide(reference.id, "keep_unresolved")}>Keep unresolved</Button>
                </Group>
                {reference.status === "ambiguous" && reference.candidate_dataset_urns.filter((urn) => urn !== suggestion?.dataset_urn).length > 0 && <Stack gap="xs" mt="sm"><Text size="xs" c="dimmed">Or map <strong>{reference.raw_reference}</strong> to another selected Dataset</Text>{reference.candidate_dataset_urns.filter((urn) => urn !== suggestion?.dataset_urn).map((urn) => <Group key={urn} justify="space-between" className="selected-item"><div><Text fw={600} size="sm">{nameFor(urn)}</Text><Text size="xs" c="dimmed">Map this field to this Dataset</Text></div><Button size="compact-xs" variant="light" onClick={() => decide(reference.id, "map_dataset", urn)}>Map</Button></Group>)}</Stack>}
              </Card>;
            })}
          </Stack>
        )}
      </Card>
      <Card className="surface-card" withBorder padding="lg">
        <Group justify="space-between" align="start" wrap="wrap">
          <div><Text fw={650}>Similar documents</Text><Text size="sm" c="dimmed">Candidates are recalled by rules, then classified semantically in one batch.</Text></div>
          <Group gap="xs"><Button variant="default" loading={checkingConflicts} onClick={() => void checkSimilarDocuments()}>Check similar documents</Button>{conflicts && conflicts.some((item) => !ignoredConflictUrns.includes(item.document_urn)) && <Button variant="light" onClick={() => setIgnoredConflictUrns(conflicts.map((item) => item.document_urn))}>Ignore all</Button>}</Group>
        </Group>
        {conflicts && <Stack mt="md" gap="xs">
          {conflicts.filter((item) => !ignoredConflictUrns.includes(item.document_urn)).length === 0 ? <Text size="sm" c="dimmed">No active similar-document candidates.</Text> : conflicts.filter((item) => !ignoredConflictUrns.includes(item.document_urn)).map((item) => <Alert key={item.document_urn} color={item.high_risk ? "orange" : "blue"}>
            <Group gap="xs"><Text fw={600} size="sm">{item.title}</Text><Badge variant="light">{item.semantic_classification}</Badge>{item.semantic_confidence !== null && <Tooltip label="Model confidence in this classification, not a duplicate-risk score."><Badge variant="outline" color="gray">{Math.round(item.semantic_confidence * 100)}% classification confidence</Badge></Tooltip>}</Group>
            <Text size="sm">{item.semantic_reason ?? "Awaiting semantic review; shared datasets alone are not a conflict."}</Text>
            <Button size="compact-xs" variant="light" mt="xs" onClick={() => setIgnoredConflictUrns((items) => [...items, item.document_urn])}>Ignore</Button>
          </Alert>)}
        </Stack>}
      </Card>
    </Stack>
  );
}
function SelectedItem({
  item,
  onRemove,
}: {
  item: SelectionItem;
  onRemove: () => void;
}) {
  const [showEvidence, setShowEvidence] = useState(false);
  const confidence = item.recommendation
    ? Math.round(item.recommendation.confidence * 100)
    : null;
  return (
    <div className="selected-item">
      <Group justify="space-between" align="start" gap="xs" wrap="nowrap">
        <div className="selected-copy">
          <Group gap={6}>
            <Text fw={600} size="sm">
              {item.name}
            </Text>
            {confidence !== null && (
              <Badge
                color={confidence >= 80 ? "green" : "yellow"}
                variant="light"
              >
                {confidence}% evidence
              </Badge>
            )}
            {item.userSelected && (
              <Badge color="gray" variant="outline">
                Manual
              </Badge>
            )}
          </Group>
          <Text size="xs" c="dimmed" lineClamp={1}>
            {item.detail || item.urn}
          </Text>
        </div>
        <Checkbox
          checked
          aria-label={`Select ${item.name}`}
          onChange={onRemove}
        />
      </Group>
      {item.recommendation && (
        <>
          <Text size="xs" mt={7}>
            {item.recommendation.reason}
          </Text>
          {item.recommendation.evidence.length > 0 && (
            <button
              type="button"
              className="evidence-toggle"
              onClick={() => setShowEvidence((x) => !x)}
              aria-expanded={showEvidence}
            >
              {showEvidence ? "Hide evidence" : "Show evidence"}
            </button>
          )}
          {showEvidence && (
            <Stack gap={4} mt={6}>
              {item.recommendation.evidence.map((x, i) => (
                <Text size="xs" c="dimmed" key={`${x.location}-${i}`}>
                  “{x.matched_text}” · {x.location}
                </Text>
              ))}
            </Stack>
          )}
        </>
      )}
    </div>
  );
}

function RecommendationOption({
  item,
  disabled,
  onSelect,
}: {
  item: Recommendation;
  disabled: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className="recommendation-option"
      disabled={disabled}
      onClick={onSelect}
    >
      <span>
        <strong>{item.display_name}</strong>
        <small>{item.reason}</small>
      </span>
      <Checkbox checked={false} readOnly aria-label={`Select ${item.display_name}`} />
    </button>
  );
}
function PreviewEntity({ label, item, empty }: { label: string; item: SelectionItem | null; empty: string }) {
  return <Card withBorder padding="md"><Text size="xs" fw={700} c="dimmed">{label.toUpperCase()}</Text>{item ? <><Text fw={650} size="lg" mt={8}>{item.name}</Text><Text size="xs" c="dimmed" mt={4}>Confirmed in Review</Text></> : <Text size="sm" c="dimmed" mt={8}>{empty}</Text>}</Card>;
}

function ResultPanel({
  analysis,
  domain,
  owner,
  tags,
  datasets,
  onRestart,
  onBackToReview,
}: {
  analysis: Analysis;
  domain: SelectionItem | null;
  owner: SelectionItem | null;
  tags: SelectionItem[];
  datasets: SelectionItem[];
  onRestart: () => void;
  onBackToReview: () => void;
}) {
  const [conflicts, setConflicts] = useState<ConflictCandidate[]>([]);
  const [validation, setValidation] = useState<SchemaValidationResponse | null>(
    null,
  );
  const [freshness, setFreshness] = useState<FreshnessResponse | null>(null);
  const [showResolved, setShowResolved] = useState(false);
  const [evidenceChecked, setEvidenceChecked] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishedUrl, setPublishedUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reviewedAt = analysis.review_completed_at
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(analysis.review_completed_at))
    : "just now";
  async function publish() {
    setPublishing(true);
    setError(null);
    try {
      const result = await publishAnalysis(analysis.id);
      setPublishedUrl(result.datahub_document_url);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Publish failed.");
    } finally {
      setPublishing(false);
    }
  }
  async function confirm(urn: string) {
    setConflicts((await confirmConflict(analysis.id, urn)).candidates ?? []);
  }
  async function confirmReference(id: string) {
    setValidation(await confirmSchemaReference(analysis.id, id));
  }
  async function runEvidenceCheck() {
    setError(null);
    try {
      const [nextConflicts, nextValidation] = await Promise.all([
        checkConflicts(analysis.id),
        checkSchemaValidation(analysis.id),
      ]);
      setConflicts(nextConflicts.candidates ?? []);
      setValidation(nextValidation);
      setEvidenceChecked(true);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Evidence check failed.",
      );
    }
  }
  async function runFreshness() {
    setError(null);
    try {
      setFreshness(await checkFreshness(analysis.id));
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Freshness check failed.",
      );
    }
  }
  const hasUnconfirmedRisk =
    conflicts.some((item) => item.high_risk && !item.confirmed) ||
    (validation?.references ?? []).some(
      (item) => item.high_risk && !item.confirmed,
    );
  const hasAmbiguousFields = (validation?.references ?? []).some(
    (item) => item.status === "ambiguous",
  );
  const resolvedCount = (validation?.references ?? []).filter(
    (item) => item.status === "resolved",
  ).length;
  return (
    <Card className="surface-card result-card" withBorder padding="xl">
      <ThemeIcon size={52} radius="xl" color="green" variant="light">
        <IconCircleCheck size={26} />
      </ThemeIcon>
      <Title order={2} mt="md">
        {publishedUrl ? "Published safely" : "Ready to publish"}
      </Title>
      <Text c="dimmed" mt="xs" maw={590}>
        Review the metadata that will be sent to DataHub. Nothing is written
        until you explicitly publish.
      </Text>
      <SimpleGrid cols={{ base: 1, sm: 3 }} mt="xl" spacing="sm">
        <div className="result-detail">
          <Text size="xs" c="dimmed">
            STATUS
          </Text>
          <Badge variant="light" mt={5}>
            {publishedUrl ? "PUBLISHED" : "READY TO PUBLISH"}
          </Badge>
        </div>
        <div className="result-detail">
          <Text size="xs" c="dimmed">
            REVIEWED
          </Text>
          <Text size="sm" fw={600} mt={5}>
            {reviewedAt}
          </Text>
        </div>
        <div className="result-detail">
          <Text size="xs" c="dimmed">
            REVIEW STATUS
          </Text>
          <Text size="sm" fw={600} mt={5}>
            Metadata confirmed
          </Text>
        </div>
      </SimpleGrid>
      {error && (
        <Alert color="orange" mt="lg">
          {error}
        </Alert>
      )}
      {!publishedUrl && (
        <Card withBorder padding="lg" mt="lg" className="publishing-preview">
          <Group justify="space-between" align="start" wrap="wrap">
            <div><Text className="eyebrow">DATAHUB PAYLOAD</Text><Text fw={650} size="lg" mt={3}>Publishing preview</Text><Text size="sm" c="dimmed" mt={4}>These are the associations that will be written to the document.</Text></div>
            <Badge size="lg" variant="light">{datasets.length} datasets · {tags.length} tags</Badge>
          </Group>
          <SimpleGrid cols={{ base: 1, sm: 2 }} mt="lg" spacing="md">
            <PreviewEntity label="Domain" item={domain} empty="No domain selected" />
            <PreviewEntity label="Owner" item={owner} empty="No owner selected" />
          </SimpleGrid>
          <div className="preview-section">
            <Text size="xs" fw={700} c="dimmed">TAGS</Text>
            <Group gap="xs" mt={7}>{tags.length ? tags.map((item) => <Badge key={item.urn} variant="light" color="indigo">{item.name}</Badge>) : <Text size="sm" c="dimmed">No tags selected</Text>}</Group>
          </div>
          <div className="preview-section">
            <Group justify="space-between"><Text size="xs" fw={700} c="dimmed">RELATED DATASETS</Text><Badge variant="outline" color="gray">{datasets.length}</Badge></Group>
            {datasets.length ? <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm" mt="md">{datasets.map((item) => <Card key={item.urn} withBorder padding="md"><Text fw={650} size="sm">{item.name}</Text><Text size="xs" c="dimmed" mt={5}>Confirmed in Review</Text></Card>)}</SimpleGrid> : <Text size="sm" c="dimmed" mt="sm">No datasets selected</Text>}
          </div>
        </Card>
      )}
      {validation && (
        <Stack gap="sm" mt="lg">
          <Group justify="space-between">
            <div>
              <Text fw={600}>Schema evidence</Text>
              <Text size="xs" c="dimmed">
                Can every field reference be mapped to exactly one selected
                Dataset?
              </Text>
            </div>
            {resolvedCount > 0 && (
              <Button
                size="compact-xs"
                variant="subtle"
                onClick={() => setShowResolved((value) => !value)}
              >
                {showResolved
                  ? "Hide resolved fields"
                  : `Show ${resolvedCount} resolved field${resolvedCount === 1 ? "" : "s"}`}
              </Button>
            )}
          </Group>
          {validation.references.length === 0 ? (
            <Text size="sm" c="dimmed">
              No explicit field references found.
            </Text>
          ) : (
            validation.references
              .filter((item) => showResolved || item.status !== "resolved")
              .map((item) => (
                <Card key={item.id} withBorder padding="sm">
                  <Group justify="space-between" align="start">
                    <div>
                      <Text size="sm" fw={600}>
                        {item.raw_reference}{" "}
                        <Badge
                          size="xs"
                          color={
                            item.status === "resolved"
                              ? "green"
                              : item.status === "unresolved"
                                ? "orange"
                                : "yellow"
                          }
                        >
                          {item.status}
                        </Badge>
                      </Text>
                      <List size="xs" mt={5} spacing={2}>
                        <List.Item>Referenced at {item.location}</List.Item>
                        <List.Item>{item.reason}</List.Item>
                      </List>
                      {item.status === "ambiguous" && (
                        <List size="xs" mt={5} spacing={2}>
                          <List.Item>
                            Matching Datasets:{" "}
                            {item.candidate_dataset_urns.join(", ") || "none"}
                          </List.Item>
                          <List.Item>
                            Return to review and remove unrelated Datasets, or
                            qualify the field with a table or alias in the
                            source.
                          </List.Item>
                        </List>
                      )}
                    </div>
                    {item.status === "ambiguous" ? (
                      <Button
                        size="xs"
                        variant="default"
                        onClick={onBackToReview}
                      >
                        Resolve in review
                      </Button>
                    ) : (
                      item.high_risk && (
                        <Button
                          size="xs"
                          disabled={item.confirmed}
                          onClick={() => confirmReference(item.id)}
                        >
                          {item.confirmed ? "Confirmed" : "Confirm warning"}
                        </Button>
                      )
                    )}
                  </Group>
                </Card>
              ))
          )}
          {validation.references.length > 0 &&
            validation.references.every((item) => item.status === "resolved") &&
            !showResolved && (
              <Text size="sm" c="dimmed">
                All explicit field references resolved successfully.
              </Text>
            )}
        </Stack>
      )}
      {conflicts.length > 0 && (
        <Stack gap="sm" mt="lg">
          <div>
            <Text fw={600}>Duplicate-document conflicts</Text>
            <Text size="xs" c="dimmed">
              Could this overlap an existing DataHub document on the same
              Dataset?
            </Text>
          </div>
          {conflicts.map((item) => (
            <Card key={item.document_urn} withBorder padding="sm">
              <Group justify="space-between" align="start">
                <div>
                  <Text size="sm" fw={600}>
                    {item.title || item.document_urn}
                  </Text>
                  <List size="xs" mt={5} spacing={2}>
                    {item.evidence.map((evidence) => (
                      <List.Item key={evidence}>{evidence}</List.Item>
                    ))}
                  </List>
                </div>
                {item.high_risk ? (
                  <Button
                    size="xs"
                    variant={item.confirmed ? "light" : "filled"}
                    disabled={item.confirmed}
                    onClick={() => confirm(item.document_urn)}
                  >
                    {item.confirmed ? "Confirmed" : "Confirm review"}
                  </Button>
                ) : (
                  <Badge variant="light">Visible only</Badge>
                )}
              </Group>
            </Card>
          ))}
        </Stack>
      )}
      {publishedUrl && (
        <>
          <Button component="a" href={publishedUrl} target="_blank" mt="xl">
            Open published Document
          </Button>
          <Button variant="default" mt="xl" ml="sm" onClick={runFreshness}>
            Check freshness
          </Button>
          {freshness && (
            <Stack mt="md" gap="xs">
              <Alert color={freshness.changed ? "orange" : "green"}>
                {freshness.changed
                  ? `Freshness check completed: ${freshness.evidence.length} change(s) require review.`
                  : "Freshness check completed: no metadata or schema changes found."}
              </Alert>
              {freshness.evidence.map((item, index) => (
                <Alert
                  key={index}
                  color={item.affects_referenced_field ? "orange" : "blue"}
                >
                  {item.message}
                  {item.affects_referenced_field
                    ? " — affects a referenced field"
                    : ""}
                </Alert>
              ))}
            </Stack>
          )}
        </>
      )}
      {!publishedUrl && (
        <>
          <Group mt="xl">
            <Button
              leftSection={<IconArrowLeft size={16} />}
              variant="default"
              onClick={onBackToReview}
            >
              Back to review
            </Button>
            <Button
              component="a"
              href={`/api/analyses/${analysis.id}/source`}
              download
            >
              Download source
            </Button>
            <Button
              loading={publishing}
              onClick={publish}
            >
              Publish to DataHub
            </Button>
          </Group>
        </>
      )}
      <Button variant="default" mt="xl" ml="sm" onClick={onRestart}>
        Review another document
      </Button>
    </Card>
  );
}
