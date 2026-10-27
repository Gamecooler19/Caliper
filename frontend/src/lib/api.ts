import { ClassifyRequestError, type BackendErrorCode, type ClassifyResponse } from "./types";

/**
 * The one backend endpoint this frontend ever calls, as a same-origin
 * relative request ("/api/v1/classify") -- never an absolute URL, and
 * never with any API-key header or secret value attached. Caddy is the
 * only layer that ever adds `X-API-Key` (see ../../../Caddyfile); this
 * file contains zero API-key logic of any kind, by design, per the
 * Module 5 brief.
 */
const CLASSIFY_ENDPOINT = "/api/v1/classify";

const KNOWN_ERROR_CODES: readonly BackendErrorCode[] = [
  "invalid_request",
  "intake_parsing_failed",
  "upstream_model_api_timeout",
  "upstream_model_api_error",
  "rate_limit_exceeded",
  "storage_error",
  "unauthorized",
  "internal_error",
];

function coerceErrorCode(value: unknown): BackendErrorCode {
  if (typeof value === "string" && (KNOWN_ERROR_CODES as string[]).includes(value)) {
    return value as BackendErrorCode;
  }
  return "unknown";
}

export interface ClassifyRequest {
  image: File;
  description: string;
}

/**
 * POST the device photo + description to /api/v1/classify and return
 * the parsed success body. Throws ClassifyRequestError with only a
 * backend error *code* on any failure -- the raw response body (which
 * may include a `message` field straight from an internal exception)
 * is read only to find the `error` code; it is never surfaced to the
 * caller or rendered.
 */
export async function classifyDevice(
  request: ClassifyRequest,
  signal?: AbortSignal,
): Promise<ClassifyResponse> {
  const formData = new FormData();
  formData.set("image", request.image);
  formData.set("description", request.description);

  let response: Response;
  try {
    response = await fetch(CLASSIFY_ENDPOINT, {
      method: "POST",
      body: formData,
      signal,
    });
  } catch {
    throw new ClassifyRequestError("network");
  }

  if (!response.ok) {
    let code: unknown;
    try {
      const body = (await response.json()) as { error?: unknown };
      code = body.error;
    } catch {
      code = undefined;
    }
    throw new ClassifyRequestError(coerceErrorCode(code));
  }

  return (await response.json()) as ClassifyResponse;
}
