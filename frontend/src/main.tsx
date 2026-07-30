import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <main>
      <h1>DataHub Document Enrichment</h1>
      <p>Review UI is delivered in Phase 6. The Phase 5 workflow API is available at <code>/api</code>.</p>
    </main>
  </StrictMode>,
);
