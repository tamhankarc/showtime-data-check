"use client";
import { useState } from "react";
import { genericInitial } from "@/lib/search";

export function Status({ value }: { value?: string }) {
  const text = value || "not imported";
  const kind = [
    "done",
    "success",
    "confirmed",
    "enabled",
    "active",
    "available",
    "completed",
  ].includes(text.toLowerCase())
    ? "ok"
    : ["failed", "error", "expired", "stale", "disabled"].includes(
          text.toLowerCase(),
        )
      ? "bad"
      : [
            "partial",
            "needs_review",
            "queued",
            "processing",
            "incomplete",
          ].includes(text.toLowerCase())
        ? "warn"
        : "";
  return <span className={`pill ${kind}`}>{text.replaceAll("_", " ")}</span>;
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-mark" aria-hidden="true">
        ⌕
      </span>
      <h2>{title}</h2>
      {children && <div>{children}</div>}
    </div>
  );
}
export function GenericLogo({ name }: { name: string }) {
  const [failed, setFailed] = useState(false);
  const initial = genericInitial(name);
  return (
    <span className="generic-logo" aria-hidden="true">
      {failed ? (
        initial
      ) : (
        <img
          src={`/generic/${initial.toLowerCase()}.png`}
          alt=""
          width="35"
          height="35"
          loading="lazy"
          onError={() => setFailed(true)}
        />
      )}
    </span>
  );
}
export function Pagination({
  page,
  total,
  size,
  onChange,
}: {
  page: number;
  total: number;
  size: number;
  onChange: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="pagination">
      <span>
        {total.toLocaleString()} records · Page {page} of {pages}
      </span>
      <div className="button-row">
        {pages > 2 && (
          <button
            className="button small"
            type="button"
            disabled={page <= 1}
            onClick={() => onChange(1)}
          >
            First
          </button>
        )}
        <button
          className="button small"
          type="button"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
        >
          Previous
        </button>
        <button
          className="button small"
          type="button"
          disabled={page >= pages}
          onClick={() => onChange(page + 1)}
        >
          Next
        </button>
        {pages > 2 && (
          <button
            className="button small"
            type="button"
            disabled={page >= pages}
            onClick={() => onChange(pages)}
          >
            Last
          </button>
        )}
      </div>
    </div>
  );
}
export function ErrorNotice({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="notice danger" role="alert">
      <span>{message}</span>
      {retry && (
        <button className="button small" onClick={retry} type="button">
          Try again
        </button>
      )}
    </div>
  );
}
