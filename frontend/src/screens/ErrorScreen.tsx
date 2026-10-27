import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { presentError } from "@/lib/errorMessages";
import type { BackendErrorCode } from "@/lib/types";

export interface ErrorScreenProps {
  code: BackendErrorCode;
  onRetry: () => void;
  onEditDescription: () => void;
  onBackToIdle: () => void;
}

/**
 * Error state: one honest, specific message per backend error code
 * (see src/lib/errorMessages.ts, the single place that maps a code to
 * copy). Visually and semantically distinct from the amber
 * "review suggested" consistency-flags treatment on the result screen
 * -- this screen alone uses the reserved danger-red token.
 */
export function ErrorScreen({ code, onRetry, onEditDescription, onBackToIdle }: ErrorScreenProps) {
  const presentation = presentError(code);

  const handlePrimaryAction = () => {
    if (presentation.action === "retry") onRetry();
    else if (presentation.action === "edit_description") onEditDescription();
    else onBackToIdle();
  };

  return (
    <div
      className="mx-auto flex w-full max-w-xl flex-col items-center gap-5 px-4 py-20 text-center sm:py-28"
      data-testid="error-screen"
      role="alert"
    >
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-danger-subtle">
        <AlertTriangle className="h-7 w-7 text-danger" aria-hidden="true" />
      </div>

      <p className="text-base text-text" data-testid="error-message">
        {presentation.message}
      </p>

      <Button
        type="button"
        variant="primary"
        size="lg"
        data-testid="error-primary-action"
        onClick={handlePrimaryAction}
        className="w-full"
      >
        {presentation.actionLabel}
      </Button>
    </div>
  );
}
