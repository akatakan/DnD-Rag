import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { applyTheme, readStoredTheme } from "./theme";
import "./styles.css";
import "./handover.css";

// Before the first paint, so the stored theme never flashes past the default.
applyTheme(readStoredTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary area="Masa">
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
