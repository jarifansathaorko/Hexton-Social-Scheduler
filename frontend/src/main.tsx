import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { initTheme } from "./lib/theme";
import "./index.css";

/**
 * Resolve the theme before React renders anything, so the first painted frame is
 * already the operator's chosen appearance rather than one frame of the other.
 */
initTheme();

/**
 * Entry point.
 *
 * The Query client is configured to never refetch on window focus. This is an
 * application someone leaves open all day on a second monitor, and a refetch storm
 * every time they alt-tab back would be noise on a local SQLite database for no
 * benefit — the screens that need to be live already poll on their own interval.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 2000,
    },
  },
});

const container = document.getElementById("root");
if (!container) throw new Error("Root element is missing from index.html");

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
