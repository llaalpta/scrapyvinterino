import { ChevronLeft, ChevronRight } from 'lucide-react';

export function Pagination({
  disabled = false,
  onPageChange,
  page,
  pageSize,
  total,
  totalPages,
  onPageSizeChange
}: {
  disabled?: boolean;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
}) {
  const firstItem = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const lastItem = Math.min(page * pageSize, total);

  return (
    <div className="pagination">
      <span>
        Mostrando {firstItem}-{lastItem} de {total}
      </span>
      <div>
        {onPageSizeChange ? (
          <label>
            Elementos por pagina
            <select value={pageSize} disabled={disabled} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </label>
        ) : null}
        <button type="button" disabled={disabled || page <= 1} onClick={() => onPageChange(page - 1)}>
          <ChevronLeft size={17} />
          Anterior
        </button>
        <span>Pagina {page} de {totalPages || 1}</span>
        <button type="button" disabled={disabled || totalPages === 0 || page >= totalPages} onClick={() => onPageChange(page + 1)}>
          Siguiente
          <ChevronRight size={17} />
        </button>
      </div>
    </div>
  );
}
