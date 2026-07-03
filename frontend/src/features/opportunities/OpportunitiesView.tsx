import type { OpportunityResult, Page } from '../../api';
import { ItemCell } from '../../components/ItemCell';
import { Pagination } from '../../components/Pagination';
import { RowActions } from '../../components/RowActions';
import { formatDate } from '../../utils/format';

export function OpportunitiesView({
  loading,
  opportunityPage,
  onPageChange
}: {
  loading: boolean;
  opportunityPage: Page<OpportunityResult>;
  onPageChange: (page: number) => void;
}) {
  return (
    <section className="section-panel">
      <div className="panel-heading">
        <h3>Oportunidades</h3>
        <span>{opportunityPage.total}</span>
      </div>
      {opportunityPage.items.length === 0 ? (
        <p className="empty-inline">Todavia no hay oportunidades. Se crearan cuando implementemos reglas locales.</p>
      ) : (
        <div className="opportunity-list">
          {opportunityPage.items.map((opportunity) => (
            <article className="opportunity-row" key={opportunity.id}>
              <ItemCell item={opportunity.item} />
              <span>{opportunity.source_name}</span>
              <span>{formatDate(opportunity.created_at)}</span>
              <RowActions item={opportunity.item} />
            </article>
          ))}
        </div>
      )}
      <Pagination
        page={opportunityPage.page}
        pageSize={opportunityPage.page_size}
        total={opportunityPage.total}
        totalPages={opportunityPage.total_pages}
        onPageChange={onPageChange}
        disabled={loading}
      />
    </section>
  );
}
