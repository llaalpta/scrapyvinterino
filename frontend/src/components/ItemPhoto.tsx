import { ImageOff } from 'lucide-react';
import { useState } from 'react';

export function ItemPhoto({
  alt,
  className,
  loading = 'lazy',
  src
}: {
  alt: string;
  className?: string;
  loading?: 'eager' | 'lazy';
  src: string | null;
}) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const failed = src !== null && failedSrc === src;

  if (!src || failed) {
    return (
      <span
        aria-label={alt || undefined}
        className={['item-photo-fallback', className].filter(Boolean).join(' ')}
        role={alt ? 'img' : undefined}
      >
        <ImageOff aria-hidden="true" size={20} />
      </span>
    );
  }

  return (
    <img
      alt={alt}
      className={className}
      decoding="async"
      loading={loading}
      referrerPolicy="no-referrer"
      src={src}
      onError={() => setFailedSrc(src)}
    />
  );
}
