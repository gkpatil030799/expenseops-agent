import { ArrowRight } from "lucide-react";

import { Card, StatusPill, statusIcon } from "./sandboxUi";

export type FlowStep = {
  id: string;
  title: string;
  description: string;
  status: "success" | "failed" | "fallback" | "unknown" | "started" | "skipped";
  detail?: string | null;
};

export function SandboxFlowStepper({ steps }: { steps: FlowStep[] }) {
  return (
    <div className="grid gap-3 lg:grid-cols-5">
      {steps.map((step, index) => (
        <FlowStepCard key={step.id} step={step} showArrow={index < steps.length - 1} />
      ))}
    </div>
  );
}

export function FlowStepCard({ step, showArrow }: { step: FlowStep; showArrow?: boolean }) {
  const Icon = statusIcon(step.status);
  return (
    <Card className="relative p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-50 text-slate-700 ring-1 ring-slate-200">
          <Icon className="h-4 w-4" />
        </span>
        <StatusPill value={step.status} />
      </div>
      <div className="mt-3 text-sm font-semibold text-slate-950">{step.title}</div>
      <p className="mt-1 text-xs leading-5 text-slate-500">{step.description}</p>
      {step.detail ? <p className="mt-2 text-xs font-medium text-slate-700">{step.detail}</p> : null}
      {showArrow ? (
        <ArrowRight className="absolute -right-2 top-1/2 hidden h-4 w-4 -translate-y-1/2 text-slate-300 lg:block" />
      ) : null}
    </Card>
  );
}
