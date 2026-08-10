import "@mantine/core/styles.css";
import "./styles.css";
import "./document-row.css";
import "./tech-theme.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
