import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import { ApiStatusBanner } from "./components/ui/api-status-banner";
import { ErrorBoundary } from "./components/ui/error-boundary";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <ApiStatusBanner />
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
