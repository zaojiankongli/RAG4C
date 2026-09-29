import { Suspense, type ReactNode } from "react";
import ErrorBoundary from "../components/ErrorBoundary";
import PageState from "../components/PageState";
import { PAGE_KEYS, type PageKey } from "./appRoute";

type Props = {
  activePage: PageKey;
  mountedPages: readonly PageKey[];
  pageNodes: Record<PageKey, ReactNode>;
};

/** Keeps visited pages mounted while isolating each route's loading and error state. */
export default function MountedPageHost({
  activePage,
  mountedPages,
  pageNodes,
}: Props) {
  return (
    <>
      {PAGE_KEYS.filter((key) => mountedPages.includes(key)).map((key) => (
        <div key={key} className={activePage === key ? "page-slot" : "page-slot is-hidden"}>
          <ErrorBoundary>
            <Suspense fallback={<PageState status="loading" title="加载中…" />}>
              {pageNodes[key]}
            </Suspense>
          </ErrorBoundary>
        </div>
      ))}
    </>
  );
}
