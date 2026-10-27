import type { BackendErrorCode } from "./types";

export type ErrorAction = "edit_description" | "retry" | "back_to_idle";

export interface ErrorPresentation {
  /** Honest, specific, user-facing copy. Never the raw backend error body or code. */
  message: string;
  /** Which recovery action the primary button on the error screen performs. */
  action: ErrorAction;
  actionLabel: string;
}

/**
 * The one place that turns a backend error code into user-facing copy,
 * per the Module 5 brief's error-mapping table. Every case here is
 * deliberately specific about what the backend code means; nothing
 * here ever interpolates a raw backend error body or code into the
 * message shown to the user.
 */
export function presentError(code: BackendErrorCode): ErrorPresentation {
  switch (code) {
    case "intake_parsing_failed":
      return {
        message:
          "We couldn't pull clear enough details from that description to classify the device. " +
          "Add a bit more detail -- how it contacts the body, how long it stays in contact, and " +
          "whether it's powered -- and try again.",
        action: "edit_description",
        actionLabel: "Edit description",
      };

    case "upstream_model_api_error":
    case "upstream_model_api_timeout":
      return {
        message:
          "The measurement service is temporarily unavailable. This usually clears up quickly.",
        action: "retry",
        actionLabel: "Retry",
      };

    case "rate_limit_exceeded":
      return {
        message: "You've made a lot of requests in a short time. Please wait a bit and try again.",
        action: "back_to_idle",
        actionLabel: "Back",
      };

    // invalid_request, storage_error, unauthorized, internal_error,
    // network, unknown -- every other case, including a stray
    // unauthorized response, gets the same generic, honest message.
    // Never exposes the raw code or any backend-provided message text.
    default:
      return {
        message: "Something went wrong on our end. Please try again shortly.",
        action: "back_to_idle",
        actionLabel: "Try again",
      };
  }
}
