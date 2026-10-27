import { useEffect, useState } from "react";
import type { DeviceBoundingBox } from "@/lib/types";

export interface AnnotatedImageProps {
  src: string;
  alt: string;
  boundingBox: DeviceBoundingBox | null;
}

/**
 * Renders the uploaded photo with the SAM segmentation box drawn as an
 * SVG overlay, positioned proportionally to the image's own *rendered*
 * size -- never raw pixel coordinates copied onto a differently-sized
 * displayed image, per the Module 5 brief.
 *
 * Mechanism: the overlay <svg> uses `viewBox="0 0 image_width
 * image_height"` (the backend's own reported source pixel dimensions)
 * with `preserveAspectRatio="none"` and is absolutely positioned to
 * exactly cover the <img> element. SVG's viewBox scaling then maps the
 * box's source-pixel coordinates onto whatever size the browser actually
 * renders the image at -- including on resize -- with no manual
 * pixel-ratio math in this component at all.
 */
export function AnnotatedImage({ src, alt, boundingBox }: AnnotatedImageProps) {
  const [imageLoaded, setImageLoaded] = useState(false);

  useEffect(() => {
    setImageLoaded(false);
  }, [src]);

  return (
    <div className="relative w-full overflow-hidden rounded-lg border border-border bg-bg-subtle">
      <img
        src={src}
        alt={alt}
        className="block w-full"
        onLoad={() => setImageLoaded(true)}
        data-testid="result-photo"
      />
      {imageLoaded && boundingBox && (
        <svg
          className="pointer-events-none absolute inset-0 h-full w-full animate-fade-in"
          viewBox={`0 0 ${boundingBox.image_width} ${boundingBox.image_height}`}
          preserveAspectRatio="none"
          aria-hidden="true"
          data-testid="segmentation-overlay"
        >
          <rect
            x={boundingBox.x1}
            y={boundingBox.y1}
            width={Math.max(boundingBox.x2 - boundingBox.x1, 0)}
            height={Math.max(boundingBox.y2 - boundingBox.y1, 0)}
            fill="rgb(var(--color-accent) / 0.12)"
            stroke="rgb(var(--color-accent))"
            strokeWidth={Math.max(boundingBox.image_width, boundingBox.image_height) * 0.006}
            vectorEffect="non-scaling-stroke"
            rx={4}
          />
        </svg>
      )}
    </div>
  );
}
