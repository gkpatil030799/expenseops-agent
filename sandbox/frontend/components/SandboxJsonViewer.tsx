import { ChevronDown, Copy } from "lucide-react";
import { useState } from "react";

type Props = {
  title: string;
  data: unknown;
  defaultOpen?: boolean;
};

export function SandboxJsonViewer({ title, data, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const json = JSON.stringify(data ?? null, null, 2);

  return (
    <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-semibold text-slate-900"
      >
        <span>{title}</span>
        <ChevronDown className={`h-4 w-4 transition ${open ? "rotate-180" : ""}`} />
      </button>
      {open ? (
        <div className="border-t border-slate-200 p-3">
          <button
            type="button"
            onClick={() => void navigator.clipboard?.writeText(json)}
            className="mb-2 inline-flex items-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600"
          >
            <Copy className="h-3.5 w-3.5" />
            Copy
          </button>
          <pre className="max-h-80 overflow-auto rounded-lg bg-slate-950 p-3 text-xs leading-5 text-slate-100">
            {json}
          </pre>
        </div>
      ) : null}
    </section>
  );
}
