import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";

import "./index.css";

/**
 * Entry point.
 *
 * Mocks are started BEFORE the app renders and only when the build-time flag is
 * on, so the first `/auth/refresh` and all portal requests are already
 * intercepted (CONTRACT §6 — the frontend does not wait for the backend).
 */
async function bootstrap(): Promise<void> {
  if (__ENABLE_MOCKS__) {
    const { startMocks } = await import("./mocks/browser");
    await startMocks();
  }

  const container = document.getElementById("root");
  if (!container) throw new Error("找不到 #root 挂载点");

  createRoot(container).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void bootstrap();
