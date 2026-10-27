import * as React from "react";
import { cn } from "@/lib/utils";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "flex min-h-28 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text " +
          "placeholder:text-text-muted focus-visible:outline-none focus-visible:ring-2 " +
          "focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg " +
          "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";
