import type { NavigationIntent } from "./appRoute";

export interface NavigationCommitEnvironment {
  pushHistory: (url: string, state?: unknown) => void;
  replaceHistory: (url: string, state?: unknown) => void;
  setHash: (url: string) => void;
  dispatchPopState: () => void;
}

export interface NavigationCommitOptions {
  historyAction?: "push" | "replace";
  historyState?: unknown;
  dispatchPopStateAfterHistory?: boolean;
  /**
   * Some legacy page-owned navigations historically emitted a synthetic
   * popstate even after changing the hash. App-owned navigation keeps the
   * default false because the browser already emits hashchange.
   */
  dispatchPopStateAfterHash?: boolean;
}

type NavigationCommitter = (
  intent: NavigationIntent,
  environment: NavigationCommitEnvironment,
  options: NavigationCommitOptions,
) => void;

const COMMITTERS: Record<NavigationIntent["mode"], NavigationCommitter> = {
  history: (intent, environment, options) => {
    if (options.historyAction === "replace") {
      environment.replaceHistory(intent.url, options.historyState);
    } else {
      environment.pushHistory(intent.url, options.historyState);
    }
    if (options.dispatchPopStateAfterHistory !== false) environment.dispatchPopState();
  },
  hash: (intent, environment, options) => {
    environment.setHash(intent.url);
    if (options.dispatchPopStateAfterHash) environment.dispatchPopState();
  },
};

function browserNavigationEnvironment(): NavigationCommitEnvironment {
  return {
    pushHistory: (url, state = window.history.state) => window.history.pushState(state, "", url),
    replaceHistory: (url, state = window.history.state) =>
      window.history.replaceState(state, "", url),
    setHash: (url) => {
      window.location.hash = url;
    },
    dispatchPopState: () => window.dispatchEvent(new PopStateEvent("popstate")),
  };
}

/** Commits one already validated navigation intent through the deployment adapter. */
export function commitNavigationIntent(
  intent: NavigationIntent,
  options: NavigationCommitOptions = {},
  environment: NavigationCommitEnvironment = browserNavigationEnvironment(),
): void {
  COMMITTERS[intent.mode](intent, environment, options);
}
