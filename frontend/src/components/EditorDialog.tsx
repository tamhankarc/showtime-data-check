"use client";
import { useEffect, useId, useRef } from "react";
export function EditorDialog({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const element = ref.current;
    element?.showModal();
    return () => {
      element?.close();
    };
  }, []);
  return (
    <dialog
      className="editor-dialog"
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      aria-labelledby={id}
    >
      <div className="dialog-inner">
        <div className="panel-head">
          <h2 id={id}>{title}</h2>
          <button
            type="button"
            className="button small"
            onClick={onClose}
            aria-label="Close editor"
          >
            ×
          </button>
        </div>
        <div className="dialog-body">{children}</div>
      </div>
    </dialog>
  );
}
