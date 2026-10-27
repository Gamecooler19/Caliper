import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, ShieldAlert } from "lucide-react";
import { AnnotatedImage } from "@/components/AnnotatedImage";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import type { ClassifyResponse } from "@/lib/types";

export interface ResultScreenProps {
  image: File;
  data: ClassifyResponse;
  onNewMeasurement: () => void;
}

/**
 * Result state. Renders, per the Module 5 brief:
 *  - the uploaded photo with the segmentation box overlay
 *    (AnnotatedImage; proportional, never raw-pixel)
 *  - the risk class letter in large instrument-readout typography
 *    (font-mono, the JetBrains/IBM Plex Mono split)
 *  - licensing authority, applicable form, clinical investigation flag
 *  - consistency_flags, rendered only when non-empty, in the amber
 *    "review suggested" treatment -- visually/semantically distinct
 *    from the danger-red error state -- with an explicit green
 *    no-discrepancy confirmation when the array is empty
 *  - the full report text with its disclaimer sentence in its own
 *    bordered block
 *  - a single primary "New Measurement" action; every other element
 *    on this screen is informational, not another competing action
 */
export function ResultScreen({ image, data, onNewMeasurement }: ResultScreenProps) {
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const { classification, consistency_flags, report, device_bounding_box } = data;

  useEffect(() => {
    const url = URL.createObjectURL(image);
    setPhotoUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

  // Split the report body from its fixed disclaimer sentence, which
  // backend/app/report/generator.py always appends verbatim after a
  // blank-line separator ("{prose}\n\n{disclaimer}") -- rendering it in
  // its own bordered block only requires splitting on that separator,
  // never re-deriving the disclaimer text itself.
  const [reportBody, disclaimer] = useMemo(() => {
    const separatorIndex = report.lastIndexOf("\n\n");
    if (separatorIndex === -1) return [report, null] as const;
    return [report.slice(0, separatorIndex).trim(), report.slice(separatorIndex).trim()] as const;
  }, [report]);

  return (
    <div
      className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-8 animate-fade-in sm:px-6 sm:py-12"
      data-testid="result-screen"
    >
      {photoUrl && (
        <AnnotatedImage src={photoUrl} alt="Uploaded device" boundingBox={device_bounding_box} />
      )}

      <Card className="flex flex-col gap-4 p-5 sm:p-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Risk class
            </p>
            <p
              className="font-mono text-6xl font-bold leading-none text-accent sm:text-7xl"
              data-testid="risk-class-readout"
            >
              {classification.risk_class}
            </p>
          </div>
          <dl className="grid grid-cols-1 gap-2 text-right text-sm">
            <div>
              <dt className="text-text-muted">Licensing authority</dt>
              <dd className="font-mono font-medium text-text" data-testid="licensing-authority">
                {classification.applicable_licensing_authority}
              </dd>
            </div>
            <div>
              <dt className="text-text-muted">Applicable form</dt>
              <dd className="font-mono font-medium text-text" data-testid="applicable-form">
                {classification.applicable_form ?? "None"}
              </dd>
            </div>
          </dl>
        </div>

        <div
          className="flex items-center gap-2 rounded-md border border-border bg-bg-subtle px-3 py-2 text-sm"
          data-testid="clinical-investigation-flag"
        >
          <span className="font-medium text-text">Clinical investigation required:</span>
          <span className="font-mono font-semibold text-text">
            {classification.clinical_investigation_required ? "Yes" : "No"}
          </span>
        </div>
        <p className="text-xs text-text-muted">{classification.clinical_investigation_basis}</p>
      </Card>

      {consistency_flags.length > 0 ? (
        <Card
          className="flex flex-col gap-3 border-amber-border bg-amber-subtle p-5 sm:p-6"
          data-testid="consistency-flags-callout"
        >
          <div className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 text-amber" aria-hidden="true" />
            <p className="text-sm font-semibold text-amber">Review suggested</p>
          </div>
          <ul className="flex flex-col gap-2">
            {consistency_flags.map((flag) => (
              <li key={flag.field_name} className="text-sm text-text" data-testid="consistency-flag-item">
                {flag.message}
              </li>
            ))}
          </ul>
        </Card>
      ) : (
        <Card
          className="flex items-center gap-2 border-success-border bg-success-subtle p-4"
          data-testid="no-discrepancy-confirmation"
        >
          <CheckCircle2 className="h-5 w-5 text-success" aria-hidden="true" />
          <p className="text-sm font-medium text-success">
            No discrepancies found between the stated description and the photo.
          </p>
        </Card>
      )}

      <Card className="flex flex-col gap-4 p-5 sm:p-6" data-testid="report-card">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
          Generated report
        </h2>
        <p className="whitespace-pre-wrap text-sm text-text">{reportBody}</p>
        {disclaimer && (
          <div
            className="rounded-md border border-border-strong bg-bg-subtle p-3 text-xs text-text-muted"
            data-testid="report-disclaimer"
          >
            {disclaimer}
          </div>
        )}
      </Card>

      <Button
        type="button"
        variant="primary"
        size="lg"
        data-testid="new-measurement-button"
        onClick={onNewMeasurement}
        className="w-full"
      >
        New Measurement
      </Button>
    </div>
  );
}
