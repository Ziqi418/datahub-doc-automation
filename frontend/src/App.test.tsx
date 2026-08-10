import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { Workflow } from "./App";

const recommendation = {
  urn: "urn:li:tag:finance",
  display_name: "Finance",
  confidence: 0.92,
  reason: "Named in source",
  evidence: [{ kind: "text", matched_text: "finance", location: "line 2" }],
  source: "rule",
};
const analysis = {
  id: "analysis-123",
  source_filename: "policy.md",
  character_count: 45,
  status: "READY_FOR_REVIEW",
  recommendations: {
    domain: { ...recommendation, urn: "urn:li:domain:finance" },
    tags: [recommendation],
    owner: { ...recommendation, urn: "urn:li:corpuser:data_steward", display_name: "Data steward" },
    datasets: [{ ...recommendation, urn: "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)", display_name: "fct_orders" }],
  },
  error_code: null,
  review_started_at: null,
  review_completed_at: null,
};

describe("review flow", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/analyses") return new Response(JSON.stringify({ analysis }), { status: 201 });
      if (url.endsWith("/recommend")) return new Response(JSON.stringify(analysis));
      if (url.endsWith("/dataset-candidates")) return new Response(JSON.stringify({ items: [], keyword_search_degraded: false }));
      if (url.endsWith("/review") && init?.method === "PUT") return new Response(JSON.stringify({ analysis: { ...analysis, review_completed_at: "2026-08-03T10:00:00Z" } }));
      return new Response(JSON.stringify({ items: [] }));
    }));
  });

  it("keeps recommendations optional and opens the publishing preview", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Workflow /></MemoryRouter>);
    const file = new File(["# Policy\nFinance"], "policy.md", { type: "text/markdown" });

    await user.upload(document.querySelector("input[type=file]")!, file);
    await user.click(screen.getByRole("button", { name: /analyze document/i }));
    await screen.findByRole("heading", { name: /confirm the document context/i });
    expect(screen.queryByRole("button", { name: /remove finance/i })).not.toBeInTheDocument();
    await user.click(screen.getByTestId("save-review"));

    await waitFor(() => expect(screen.getByRole("heading", { name: /ready to publish/i })).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/review$/), expect.objectContaining({ method: "PUT" }));
  });

  it("restores a saved draft into review", async () => {
    const saved = {
      ...analysis,
      final_selection: {
        domain_urn: "urn:li:domain:finance",
        owner_urn: null,
        tag_urns: ["urn:li:tag:finance"],
        dataset_urns: ["urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"],
        field_dispositions: [],
      },
    };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/analyses/analysis-123") return new Response(JSON.stringify(saved));
      if (url.endsWith("/dataset-candidates")) return new Response(JSON.stringify({ items: [], keyword_search_degraded: false }));
      return new Response(JSON.stringify({ items: [] }));
    }));

    render(<MemoryRouter><Workflow analysisId="analysis-123" /></MemoryRouter>);

    await screen.findByRole("heading", { name: /confirm the document context/i });
    expect(screen.getByText("fct_orders")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Clear all" })).toHaveLength(2);
  });
});
