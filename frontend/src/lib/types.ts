/**
 * Types mirroring backend/app/api/schemas.py and
 * backend/app/classification/models.py exactly. Kept hand-in-sync with
 * the Python models rather than generated, since Module 5's backend
 * change is a single small additive field (see backend/app/api/routes.py
 * and schemas.py) -- there is no OpenAPI codegen step in this repo.
 */

export type RiskClass = "A" | "B" | "C" | "D";
export type LicensingAuthority = "State" | "Central";
export type ApplicableForm = "MD-3" | "MD-5";

export interface ClassificationResult {
  risk_class: RiskClass;
  matched_rule_id: string;
  applicable_licensing_authority: LicensingAuthority;
  applicable_form: ApplicableForm | null;
  clinical_investigation_required: boolean;
  clinical_investigation_basis: string;
  disclaimer: string;
}

export interface ConsistencyFlag {
  field_name: string;
  stated_value: string;
  vision_signal: boolean;
  message: string;
}

/** Module 5 addition: mirrors backend/app/api/schemas.py's DeviceBoundingBox. */
export interface DeviceBoundingBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  image_width: number;
  image_height: number;
}

export interface ClassifyResponse {
  classification: ClassificationResult;
  consistency_flags: ConsistencyFlag[];
  report: string;
  device_bounding_box: DeviceBoundingBox | null;
}

/**
 * The exact set of `error` codes backend/app/api/errors.py's registered
 * handlers can produce, plus a `network` code this client assigns
 * itself when the request never got a response at all (offline, DNS
 * failure, etc.) -- backend/app/api/errors.py has no handler for that
 * case because it isn't a backend response, so it's modeled here
 * instead and treated identically to "anything else" by the UI.
 */
export type BackendErrorCode =
  | "invalid_request"
  | "intake_parsing_failed"
  | "upstream_model_api_timeout"
  | "upstream_model_api_error"
  | "rate_limit_exceeded"
  | "storage_error"
  | "unauthorized"
  | "internal_error"
  | "network"
  | "unknown";

/**
 * Thrown by classifyDevice() on any non-2xx response or network
 * failure. Carries only the parsed error `code` -- never the raw
 * backend error body or message -- so UI code can never accidentally
 * render backend-internal text (see src/lib/errorMessages.ts, which is
 * the only place that turns a code into user-facing copy).
 */
export class ClassifyRequestError extends Error {
  readonly code: BackendErrorCode;

  constructor(code: BackendErrorCode) {
    super(`classify request failed: ${code}`);
    this.name = "ClassifyRequestError";
    this.code = code;
  }
}
