import { useCallback, useId, useRef, useState } from "react";
import { ImageUp, X } from "lucide-react";
import { cn } from "@/lib/utils";

export interface ImageDropzoneProps {
  image: File | null;
  onChange: (file: File | null) => void;
  disabled?: boolean;
}

/**
 * A single drag-and-drop/click-to-browse image input. Deliberately its
 * own small component (not Mantine's Dropzone, to keep styling fully on
 * this project's token system) -- Mantine is still used elsewhere
 * (hooks, primitives) per the brief's stack list.
 */
export function ImageDropzone({ image, onChange, disabled }: ImageDropzoneProps) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragActive, setIsDragActive] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const setFile = useCallback(
    (file: File | null) => {
      setPreviewUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return file ? URL.createObjectURL(file) : null;
      });
      onChange(file);
    },
    [onChange],
  );

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragActive(false);
      if (disabled) return;
      const file = event.dataTransfer.files?.[0];
      if (file) setFile(file);
    },
    [disabled, setFile],
  );

  return (
    <div>
      <label htmlFor={inputId} className="mb-2 block text-sm font-medium text-text">
        Device photo
      </label>
      <div
        data-testid="image-dropzone"
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setIsDragActive(true);
        }}
        onDragLeave={() => setIsDragActive(false)}
        onDrop={handleDrop}
        onClick={() => !disabled && inputRef.current?.click()}
        className={cn(
          "relative flex min-h-40 cursor-pointer flex-col items-center justify-center gap-2 " +
            "rounded-lg border-2 border-dashed border-border bg-bg-subtle p-4 text-center " +
            "transition-colors hover:border-accent sm:min-h-48",
          isDragActive && "border-accent bg-accent-subtle",
          disabled && "cursor-not-allowed opacity-60",
        )}
      >
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept="image/*"
          className="sr-only"
          disabled={disabled}
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />

        {previewUrl ? (
          <div className="relative">
            <img
              src={previewUrl}
              alt="Selected device"
              className="max-h-40 rounded-md object-contain sm:max-h-48"
            />
            {!disabled && (
              <button
                type="button"
                aria-label="Remove image"
                onClick={(event) => {
                  event.stopPropagation();
                  setFile(null);
                }}
                className="absolute -right-2 -top-2 rounded-full bg-surface p-1 text-text-muted shadow hover:text-danger"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        ) : (
          <>
            <ImageUp className="h-8 w-8 text-text-muted" aria-hidden="true" />
            <p className="text-sm text-text">
              <span className="font-medium text-accent">Click to upload</span> or drag a photo here
            </p>
            <p className="text-xs text-text-muted">PNG or JPG</p>
          </>
        )}
      </div>
      {image && (
        <p className="mt-1.5 truncate text-xs text-text-muted" data-testid="selected-filename">
          {image.name}
        </p>
      )}
    </div>
  );
}
