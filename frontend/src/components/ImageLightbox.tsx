import { useState, useEffect, useCallback } from "react";
import { X } from "lucide-react";

interface ImageLightboxProps {
  src: string;
  alt?: string;
  onClose: () => void;
}

export function ImageLightbox({ src, alt, onClose }: ImageLightboxProps) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    document.addEventListener("keydown", handleKeyDown);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [handleKeyDown]);

  return (
    <div
      className="image-lightbox-overlay"
      onClick={onClose}
    >
      <button
        className="absolute top-4 right-4 z-10 rounded-full bg-black/60 p-2 text-white/80 backdrop-blur-sm transition-colors hover:bg-black/80 hover:text-white"
        onClick={onClose}
        aria-label="Close"
      >
        <X size={24} />
      </button>
      <img
        src={src}
        alt={alt ?? "Expanded image"}
        className="image-lightbox-img"
        onClick={(e) => e.stopPropagation()}
      />
    </div>
  );
}

export function ClickableImage({
  src,
  alt,
  className,
}: {
  src: string;
  alt?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <img
        src={src}
        alt={alt ?? "Figure"}
        className={`cursor-zoom-in ${className ?? ""}`}
        onClick={() => setOpen(true)}
      />
      {open && (
        <ImageLightbox src={src} alt={alt} onClose={() => setOpen(false)} />
      )}
    </>
  );
}
