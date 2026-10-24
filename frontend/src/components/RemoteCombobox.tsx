"use client";
import { useEffect, useRef, useState } from "react";
import { api, items } from "@/lib/api";
import type { ListPage, RecordData } from "@/lib/types";
import type { Choice } from "@/lib/search";
import { Combobox } from "./Combobox";
interface Props {
  endpoint: string | null;
  label: string;
  value: string | string[];
  onChange: (values: string[], records: RecordData[]) => void;
  mapRecord: (record: RecordData) => Choice;
  seedOptions?: Choice[];
  multiple?: boolean;
  disabled?: boolean;
  required?: boolean;
  placeholder?: string;
}
export function RemoteCombobox({
  endpoint,
  label,
  value,
  onChange,
  mapRecord,
  seedOptions = [],
  multiple,
  disabled,
  required,
  placeholder,
}: Props) {
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<Choice[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const cache = useRef(new Map<string, Choice>());
  const recordCache = useRef(new Map<string, RecordData>());
  const selected = Array.isArray(value) ? value : value ? [value] : [];
  const mapper = useRef(mapRecord);
  mapper.current = mapRecord;
  useEffect(() => {
    cache.current.clear();
    recordCache.current.clear();
    setOptions([]);
    setQuery("");
  }, [endpoint]);
  useEffect(() => {
    if (!endpoint) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const timer = setTimeout(
      () => {
        const separator = endpoint.includes("?") ? "&" : "?";
        api<ListPage<RecordData>>(
          `${endpoint}${separator}q=${encodeURIComponent(query)}&page_size=120`,
          { signal: controller.signal },
        )
          .then((result) => {
            const choices = items(result).map((row) => {
              const choice = mapper.current(row);
              cache.current.set(choice.value, choice);
              recordCache.current.set(choice.value, row);
              return choice;
            });
            setOptions(choices);
            setTotal(result.total ?? choices.length);
          })
          .catch((e) => {
            if (e.name !== "AbortError") setError(e.message);
          })
          .finally(() => {
            if (!controller.signal.aborted) setLoading(false);
          });
      },
      query ? 250 : 0,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [endpoint, query]);
  const shown = new Map(options.map((option) => [option.value, option]));
  for (const item of selected) {
    const option =
      cache.current.get(item) ||
      seedOptions.find((option) => option.value === item);
    if (option) shown.set(item, option);
  }
  return (
    <div className="remote-combo">
      <Combobox
        label={label}
        value={value}
        options={[...shown.values()]}
        multiple={multiple}
        onChange={(values) =>
          onChange(
            values,
            values
              .map((id) => recordCache.current.get(id))
              .filter(Boolean) as RecordData[],
          )
        }
        onSearch={setQuery}
        loading={loading}
        totalOptions={total}
        disabled={disabled || !endpoint}
        required={required}
        placeholder={placeholder}
      />
      {error && (
        <small className="missing" role="alert">
          {error}
        </small>
      )}
    </div>
  );
}
