"use client";
import { useEffect, useId, useRef, useState } from "react";
import { rankChoices, type Choice } from "@/lib/search";

interface Props {
  label: string;
  options: Choice[];
  value: string | string[];
  onChange: (value: string[]) => void;
  multiple?: boolean;
  disabled?: boolean;
  placeholder?: string;
  required?: boolean;
  onSearch?: (query: string) => void;
  loading?: boolean;
  totalOptions?: number;
}
export function Combobox({
  label,
  options,
  value,
  onChange,
  multiple = false,
  disabled,
  placeholder = "Select…",
  required,
  onSearch,
  loading,
  totalOptions,
}: Props) {
  const id = useId();
  const selected = Array.isArray(value)
    ? value
    : value || options.some((option) => option.value === "")
      ? [value]
      : [];
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const allMatches = rankChoices(options, query);
  const matches = allMatches.slice(0, 120);
  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
    const outside = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  useEffect(() => {
    setActive(0);
  }, [query]);
  useEffect(() => {
    if (active >= matches.length || active < 0) setActive(0);
  }, [active, matches.length]);
  useEffect(() => {
    if (open)
      document
        .getElementById(`${id}-option-${active}`)
        ?.scrollIntoView({ block: "nearest" });
  }, [active, open, id]);
  function choose(choice: Choice) {
    onChange(
      multiple
        ? selected.includes(choice.value)
          ? selected.filter((x) => x !== choice.value)
          : [...selected, choice.value]
        : [choice.value],
    );
    if (!multiple) {
      setOpen(false);
      setQuery("");
      buttonRef.current?.focus();
    } else searchRef.current?.focus();
  }
  function close() {
    setOpen(false);
    setQuery("");
    buttonRef.current?.focus();
  }
  const currentLabel =
    selected.length === 1
      ? options.find((option) => option.value === selected[0])?.label ||
        selected[0]
      : selected.length
        ? `${selected.length} movies selected`
        : placeholder;
  return (
    <div className="field combo" ref={ref}>
      <label id={`${id}-label`}>
        {label}
        {required && <span aria-hidden="true"> *</span>}
      </label>
      <button
        ref={buttonRef}
        type="button"
        className="select-trigger"
        role="combobox"
        aria-labelledby={`${id}-label`}
        aria-controls={`${id}-list`}
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={disabled}
        onClick={() => {
          setOpen(!open);
          setQuery("");
          if (!open) onSearch?.("");
        }}
        onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp"].includes(event.key)) {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span className={!selected.length ? "muted" : ""}>{currentLabel}</span>
        <span aria-hidden="true">⌄</span>
      </button>
      {multiple && selected.length > 0 && (
        <div className="selected-chips">
          {selected.map((item) => (
            <button
              key={item}
              type="button"
              title="Remove selection"
              disabled={disabled}
              onClick={() => onChange(selected.filter((x) => x !== item))}
            >
              {options.find((option) => option.value === item)?.label || item}
              {options.find((option) => option.value === item)?.detail && (
                <span className="chip-provider">
                  {" "}
                  ·{" "}
                  {
                    options
                      .find((option) => option.value === item)!
                      .detail!.split(" · ")[0]
                  }
                </span>
              )}
              <span aria-hidden="true"> ×</span>
            </button>
          ))}
          <button
            type="button"
            className="clear"
            disabled={disabled}
            onClick={() => onChange([])}
          >
            Clear all
          </button>
        </div>
      )}
      {open && (
        <div className="combo-menu">
          <input
            ref={searchRef}
            className="combo-search"
            placeholder={`Search ${label.toLowerCase()}…`}
            aria-label={`Search ${label}`}
            aria-controls={`${id}-list`}
            aria-activedescendant={
              matches.length ? `${id}-option-${active}` : undefined
            }
            autoComplete="off"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              onSearch?.(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                close();
              } else if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((x) =>
                  Math.max(0, Math.min(x + 1, matches.length - 1)),
                );
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((x) => Math.max(x - 1, 0));
              } else if (e.key === "Enter") {
                e.preventDefault();
                if (matches[active]) choose(matches[active]);
              } else if (e.key === "Tab") {
                setOpen(false);
                setQuery("");
              }
            }}
          />
          <div
            className="combo-options"
            id={`${id}-list`}
            role="listbox"
            aria-labelledby={`${id}-label`}
            aria-multiselectable={multiple || undefined}
          >
            {matches.map((choice, index) => (
              <button
                key={choice.value}
                type="button"
                id={`${id}-option-${index}`}
                role="option"
                aria-selected={selected.includes(choice.value)}
                className={index === active ? "focused" : ""}
                onPointerMove={() => setActive(index)}
                onClick={() => choose(choice)}
                tabIndex={-1}
              >
                <span>
                  <span className="option-title">{choice.label}</span>
                  {choice.detail && <small>{choice.detail}</small>}
                </span>
                <span aria-hidden="true">
                  {selected.includes(choice.value) ? "✓" : ""}
                </span>
              </button>
            ))}
            {loading && (
              <p className="combo-empty" role="status">
                Searching…
              </p>
            )}
            {!loading && !matches.length && (
              <p className="combo-empty">No matching options.</p>
            )}
          </div>
          {(totalOptions || allMatches.length) > 120 && (
            <small className="combo-hint">
              Showing the first 120 matches. Type to narrow the list.
            </small>
          )}
          {multiple && (
            <button
              type="button"
              className="button small combo-done"
              onClick={close}
            >
              Done
            </button>
          )}
        </div>
      )}
    </div>
  );
}
