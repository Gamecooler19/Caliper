import { useCallback, useReducer, useRef } from "react";
import { classifyDevice } from "./api";
import { ClassifyRequestError, type BackendErrorCode, type ClassifyResponse } from "./types";

/**
 * The screen's state machine: exactly four states, per the Module 5
 * brief -- idle, submitting, result, error. No URL-based routing is
 * involved anywhere in this file; every transition is a plain in-memory
 * reducer action.
 */
export type ClassifyState =
  | { status: "idle"; image: File | null; description: string }
  | { status: "submitting"; image: File; description: string }
  | { status: "result"; image: File; description: string; data: ClassifyResponse }
  | {
      status: "error";
      image: File;
      description: string;
      code: BackendErrorCode;
    };

type Action =
  | { type: "SET_IMAGE"; image: File | null }
  | { type: "SET_DESCRIPTION"; description: string }
  | { type: "SUBMIT" }
  | { type: "RETRY" }
  | { type: "SUCCEEDED"; data: ClassifyResponse }
  | { type: "FAILED"; code: BackendErrorCode }
  | { type: "RESET" }
  | { type: "BACK_TO_IDLE_PRESERVE_INPUT" };

function reducer(state: ClassifyState, action: Action): ClassifyState {
  switch (action.type) {
    case "SET_IMAGE":
      if (state.status !== "idle") return state;
      return { ...state, image: action.image };

    case "SET_DESCRIPTION":
      if (state.status !== "idle") return state;
      return { ...state, description: action.description };

    case "SUBMIT":
      if (state.status !== "idle" || !state.image || !state.description.trim()) return state;
      return { status: "submitting", image: state.image, description: state.description };

    // Retries the exact same input from the error state -- the brief's
    // "offer retry" path for upstream_model_api_error/_timeout.
    case "RETRY":
      if (state.status !== "error") return state;
      return { status: "submitting", image: state.image, description: state.description };

    case "SUCCEEDED":
      if (state.status !== "submitting") return state;
      return {
        status: "result",
        image: state.image,
        description: state.description,
        data: action.data,
      };

    case "FAILED":
      if (state.status !== "submitting") return state;
      return {
        status: "error",
        image: state.image,
        description: state.description,
        code: action.code,
      };

    case "RESET":
      return { status: "idle", image: null, description: "" };

    // Error-recovery path: "go back to idle with their input
    // preserved" (per the brief's intake_parsing_failed and
    // rate_limit_exceeded copy) -- unlike RESET (used by "New
    // Measurement" from the result state), this keeps the image and
    // description so the user can edit and resubmit without
    // re-uploading.
    case "BACK_TO_IDLE_PRESERVE_INPUT":
      if (state.status === "idle") return state;
      return { status: "idle", image: state.image, description: state.description };

    default:
      return state;
  }
}

export function useClassifyMachine() {
  const [state, dispatch] = useReducer(reducer, {
    status: "idle",
    image: null,
    description: "",
  } as ClassifyState);

  const abortRef = useRef<AbortController | null>(null);

  const setImage = useCallback((image: File | null) => dispatch({ type: "SET_IMAGE", image }), []);
  const setDescription = useCallback(
    (description: string) => dispatch({ type: "SET_DESCRIPTION", description }),
    [],
  );

  const runSubmit = useCallback((image: File, description: string) => {
    const controller = new AbortController();
    abortRef.current = controller;

    classifyDevice({ image, description }, controller.signal)
      .then((data) => dispatch({ type: "SUCCEEDED", data }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const code = err instanceof ClassifyRequestError ? err.code : "unknown";
        dispatch({ type: "FAILED", code });
      });
  }, []);

  /** From idle: validate both fields are present, then submit. */
  const submit = useCallback(() => {
    if (state.status !== "idle" || !state.image || !state.description.trim()) return;
    const image = state.image;
    const description = state.description;
    dispatch({ type: "SUBMIT" });
    runSubmit(image, description);
  }, [state, runSubmit]);

  /** From error (upstream_model_api_error/_timeout): resubmit the same input unchanged. */
  const retry = useCallback(() => {
    if (state.status !== "error") return;
    const { image, description } = state;
    dispatch({ type: "RETRY" });
    runSubmit(image, description);
  }, [state, runSubmit]);

  /** "New Measurement" from the result state: a full, clean reset. */
  const reset = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: "RESET" });
  }, []);

  /** From error: back to idle, keeping the user's image/description for editing. */
  const backToIdlePreservingInput = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: "BACK_TO_IDLE_PRESERVE_INPUT" });
  }, []);

  return {
    state,
    setImage,
    setDescription,
    submit,
    retry,
    reset,
    backToIdlePreservingInput,
  };
}
