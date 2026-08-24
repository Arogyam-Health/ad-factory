import { useEffect, useState } from "react";
import { Button } from "@/components/Button";

export function usePageWindow<T>(items: T[], pageSize: number, resetKey = "") {
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(items.length / Math.max(1, pageSize)));
  const safePage = Math.min(page, pageCount - 1);

  useEffect(() => {
    setPage(0);
  }, [resetKey]);

  useEffect(() => {
    if (page > pageCount - 1) setPage(Math.max(0, pageCount - 1));
  }, [page, pageCount]);

  return {
    page: safePage,
    pageCount,
    setPage,
    items: items.slice(safePage * pageSize, safePage * pageSize + pageSize),
  };
}

export function ListPager({
  page,
  pageCount,
  onPage,
  summary = "",
}: {
  page: number;
  pageCount: number;
  onPage: (page: number) => void;
  summary?: string;
}) {
  if (pageCount <= 1 && !summary) return null;
  return (
    <div className="run-pager">
      {pageCount > 1 ? (
        <Button variant="ghost" disabled={page <= 0} onClick={() => onPage(Math.max(0, page - 1))}>
          ← Prev
        </Button>
      ) : null}
      <span className="hint">
        {summary}
        {summary && pageCount > 1 ? " · " : ""}
        {pageCount > 1 ? `Page ${page + 1} of ${pageCount}` : ""}
      </span>
      {pageCount > 1 ? (
        <Button
          variant="ghost"
          disabled={page >= pageCount - 1}
          onClick={() => onPage(Math.min(pageCount - 1, page + 1))}
        >
          Next →
        </Button>
      ) : null}
    </div>
  );
}
