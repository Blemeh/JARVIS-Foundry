import { ChevronLeft, ChevronRight } from "lucide-react";

interface PaginationProps {
  page: number;
  pageCount: number;
  onPage: (page: number) => void;
  showingLabel: string;
}

export function Pagination({ page, pageCount, onPage, showingLabel }: PaginationProps) {
  return (
    <div className="pagination">
      <span className="t-mono text-muted pagination-label">{showingLabel}</span>
      <div className="pagination-controls">
        <button
          type="button"
          className="page-btn"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          <ChevronLeft size={14} />
        </button>
        {Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => (
          <button
            key={p}
            type="button"
            className={`page-btn ${p === page ? "page-btn-active" : ""}`}
            aria-label={`Page ${p}`}
            aria-current={p === page ? "page" : undefined}
            onClick={() => onPage(p)}
          >
            {p}
          </button>
        ))}
        <button
          type="button"
          className="page-btn"
          aria-label="Next page"
          disabled={page >= pageCount}
          onClick={() => onPage(page + 1)}
        >
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}
