import { Loader2 } from "lucide-react";

/**
 * Submitting state: one calm, honestly-indeterminate loading state.
 * The backend is a single request with no streaming, so this
 * deliberately shows exactly one line of real status text -- never a
 * fabricated multi-step progress sequence ("uploading... analyzing...
 * classifying...") that would misrepresent what's actually happening
 * server-side.
 */
export function SubmittingScreen() {
  return (
    <div
      className="mx-auto flex w-full max-w-xl flex-col items-center justify-center gap-4 px-4 py-24 text-center sm:py-32"
      data-testid="submitting-screen"
      role="status"
      aria-live="polite"
    >
      <Loader2 className="h-8 w-8 animate-spin text-accent" aria-hidden="true" />
      <p className="text-sm text-text-muted">Measuring your device against MDR 2017 criteria&hellip;</p>
    </div>
  );
}
