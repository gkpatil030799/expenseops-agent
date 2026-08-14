import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const surfaceVariants = cva("min-w-0 text-ink", {
  variants: {
    variant: {
      command:
        "rounded-card border border-slate-800 bg-gradient-to-br from-slate-950 via-slate-950 to-indigo-950 text-white shadow-primary",
      primary: "rounded-card border border-ui-border bg-white shadow-card",
      secondary: "rounded-card border border-ui-border bg-slate-50/60",
      row: "border-b border-ui-border bg-transparent last:border-b-0",
    },
    padding: {
      none: "",
      compact: "p-4",
      standard: "p-4 sm:p-6",
    },
  },
  defaultVariants: {
    variant: "primary",
    padding: "standard",
  },
});

export interface SurfaceProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof surfaceVariants> {}

const Surface = React.forwardRef<HTMLDivElement, SurfaceProps>(
  ({ className, variant, padding, ...props }, ref) => (
    <div ref={ref} className={cn(surfaceVariants({ variant, padding }), className)} {...props} />
  ),
);
Surface.displayName = "Surface";

export { Surface, surfaceVariants };
