import { ImageDropzone } from "@/components/ImageDropzone";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

export interface IdleScreenProps {
  image: File | null;
  description: string;
  onImageChange: (file: File | null) => void;
  onDescriptionChange: (value: string) => void;
  onSubmit: () => void;
}

/**
 * Idle state: an image dropzone and a description textarea, with a
 * single primary "Measure" action disabled until both fields are
 * filled. There is no other control on this screen competing with
 * Measure -- the theme toggle lives in the shared app header, outside
 * this screen's own hierarchy, and is deliberately ghost-styled there.
 */
export function IdleScreen({
  image,
  description,
  onImageChange,
  onDescriptionChange,
  onSubmit,
}: IdleScreenProps) {
  const canSubmit = Boolean(image) && description.trim().length > 0;

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-6 px-4 py-8 sm:px-6 sm:py-12">
      <header className="space-y-2">
        <h1 className="font-sans text-2xl font-semibold text-text sm:text-3xl">
          Caliper device pre-assessment
        </h1>
        <p className="text-sm text-text-muted">
          Upload a device photo and describe its intended use to get an instrument-style risk
          classification readout.
        </p>
      </header>

      <form
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) onSubmit();
        }}
      >
        <ImageDropzone image={image} onChange={onImageChange} />

        <div>
          <label htmlFor="description" className="mb-2 block text-sm font-medium text-text">
            Intended use description
          </label>
          <Textarea
            id="description"
            data-testid="description-input"
            placeholder="e.g. A single-use adhesive bandage applied to intact skin for short-term wound coverage."
            value={description}
            onChange={(event) => onDescriptionChange(event.target.value)}
            rows={5}
          />
        </div>

        <Button
          type="submit"
          variant="primary"
          size="lg"
          disabled={!canSubmit}
          data-testid="measure-button"
          className="mt-1 w-full"
        >
          Measure
        </Button>
      </form>
    </div>
  );
}
