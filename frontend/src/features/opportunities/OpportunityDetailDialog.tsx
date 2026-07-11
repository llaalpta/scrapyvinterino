import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { OpportunityResult } from '../../api';
import { ItemPhoto } from '../../components/ItemPhoto';
import { RowActions } from '../../components/RowActions';
import { PriceBreakdown } from './PriceBreakdown';
import { AvailabilityBadge } from './opportunityPresentation';

export function OpportunityDetailDialog({
  onClose,
  opportunity
}: {
  onClose: () => void;
  opportunity: OpportunityResult;
}) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const touchStartXRef = useRef<number | null>(null);
  const [photoIndex, setPhotoIndex] = useState(0);
  const item = opportunity.item;
  const photos = useMemo(() => {
    const detailPhotos = item.photos.filter(Boolean);
    return [...new Set(detailPhotos.length > 0 ? detailPhotos : item.image_url ? [item.image_url] : [])];
  }, [item.image_url, item.photos]);
  const photoCount = photos.length;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) {
      returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      dialog.showModal();
    }
  }, []);

  function showPreviousPhoto() {
    if (photoCount > 1) {
      setPhotoIndex((current) => (current - 1 + photoCount) % photoCount);
    }
  }

  function showNextPhoto() {
    if (photoCount > 1) {
      setPhotoIndex((current) => (current + 1) % photoCount);
    }
  }

  function closeDialog() {
    dialogRef.current?.close();
  }

  function handleDialogClosed() {
    const returnFocus = returnFocusRef.current;
    onClose();
    window.requestAnimationFrame(() => returnFocus?.focus());
  }

  return (
    <dialog
      aria-labelledby="opportunity-detail-title"
      className="opportunity-detail-dialog"
      ref={dialogRef}
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          closeDialog();
        }
      }}
      onClose={handleDialogClosed}
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft') {
          event.preventDefault();
          showPreviousPhoto();
        }
        if (event.key === 'ArrowRight') {
          event.preventDefault();
          showNextPhoto();
        }
      }}
    >
      <div className="opportunity-detail-shell">
        <header className="opportunity-detail-header">
          <div>
            <h3 id="opportunity-detail-title">{item.title}</h3>
            <AvailabilityBadge item={item} />
          </div>
          <button aria-label="Cerrar detalle" className="icon-button" title="Cerrar" type="button" onClick={closeDialog}>
            <X size={19} />
          </button>
        </header>

        <div className="opportunity-detail-content">
          <section aria-label="Fotos del articulo" className="opportunity-gallery">
            <div
              className="opportunity-gallery-stage"
              onTouchEnd={(event) => {
                const startX = touchStartXRef.current;
                touchStartXRef.current = null;
                if (startX === null) {
                  return;
                }
                const distance = event.changedTouches[0].clientX - startX;
                if (Math.abs(distance) < 48) {
                  return;
                }
                if (distance > 0) {
                  showPreviousPhoto();
                } else {
                  showNextPhoto();
                }
              }}
              onTouchStart={(event) => {
                touchStartXRef.current = event.touches[0].clientX;
              }}
            >
              <ItemPhoto
                key={photos[photoIndex] ?? 'missing'}
                alt={photoCount > 0 ? `Foto ${photoIndex + 1} de ${photoCount} de ${item.title}` : `Sin foto de ${item.title}`}
                className="opportunity-gallery-image"
                loading="eager"
                src={photos[photoIndex] ?? null}
              />
              {photoCount > 1 ? (
                <>
                  <button aria-label="Foto anterior" className="gallery-nav previous" title="Foto anterior" type="button" onClick={showPreviousPhoto}>
                    <ChevronLeft size={22} />
                  </button>
                  <button aria-label="Foto siguiente" className="gallery-nav next" title="Foto siguiente" type="button" onClick={showNextPhoto}>
                    <ChevronRight size={22} />
                  </button>
                  <span className="gallery-counter" aria-live="polite">
                    {photoIndex + 1} / {photoCount}
                  </span>
                </>
              ) : null}
            </div>

            {photoCount > 1 ? (
              <div aria-label="Seleccionar foto" className="gallery-thumbnails">
                {photos.map((photo, index) => (
                  <button
                    aria-current={index === photoIndex ? 'true' : undefined}
                    aria-label={`Ver foto ${index + 1}`}
                    className={index === photoIndex ? 'active' : undefined}
                    key={photo}
                    type="button"
                    onClick={() => setPhotoIndex(index)}
                  >
                    <ItemPhoto alt="" className="gallery-thumbnail-image" src={photo} />
                  </button>
                ))}
              </div>
            ) : null}
          </section>

          <section aria-label="Informacion del articulo" className="opportunity-detail-data">
            <PriceBreakdown item={item} />
            <dl className="opportunity-metadata">
              <Metadata label="Marca" value={item.brand} />
              <Metadata label="Talla" value={item.size} />
              <Metadata label="Condicion" value={item.status} />
              <Metadata label="Monitor" value={opportunity.source_name} />
            </dl>
            <div className="opportunity-description">
              <h4>Descripcion</h4>
              <p>{item.description || 'Sin descripcion.'}</p>
            </div>
            <RowActions item={item} />
          </section>
        </div>
      </div>
    </dialog>
  );
}

function Metadata({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value || '-'}</dd>
    </div>
  );
}
