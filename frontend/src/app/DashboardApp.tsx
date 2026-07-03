import { DashboardShell } from '../components/layout/DashboardShell';
import { OpportunitiesView } from '../features/opportunities/OpportunitiesView';
import { ResultsView } from '../features/results/ResultsView';
import { RunsView } from '../features/runs/RunsView';
import { SettingsView } from '../features/settings/SettingsView';
import { SourcesView } from '../features/sources/SourcesView';
import { useDashboardController } from '../hooks/useDashboardController';

export function DashboardApp() {
  const dashboard = useDashboardController();

  return (
    <DashboardShell
      activeSection={dashboard.activeSection}
      activeSource={dashboard.activeSource}
      activeSubtitle={dashboard.activeSubtitle}
      activeTitle={dashboard.activeTitle}
      error={dashboard.error}
      navCollapsed={dashboard.navCollapsed}
      runningSourceId={dashboard.runningSourceId}
      onRunSource={(sourceId) => void dashboard.onRunSource(sourceId)}
      onSelectSection={dashboard.selectSection}
      onToggleNav={() => dashboard.setNavCollapsed((current) => !current)}
    >
      {dashboard.activeSection === 'results' ? (
        <ResultsView
          filters={dashboard.resultFilters}
          itemPage={dashboard.itemPage}
          loading={dashboard.loadingResults}
          pageSize={dashboard.resultsPageSize}
          sources={dashboard.sources}
          onApply={() => void dashboard.loadItems(1)}
          onApplyFilters={(filters) => void dashboard.loadItems(1, filters)}
          onClear={dashboard.clearResultFilters}
          onFilterChange={dashboard.updateResultFilter}
          onPageChange={(page) => void dashboard.loadItems(page)}
          onPageSizeChange={dashboard.changeResultsPageSize}
        />
      ) : null}

      {dashboard.activeSection === 'opportunities' ? (
        <OpportunitiesView
          loading={dashboard.loadingOpportunities}
          opportunityPage={dashboard.opportunityPage}
          onPageChange={(page) => void dashboard.loadOpportunities(page)}
        />
      ) : null}

      {dashboard.activeSection === 'sources' ? (
        <SourcesView
          onCreateSource={dashboard.onCreateSource}
          onRunSource={(sourceId) => void dashboard.onRunSource(sourceId)}
          onSaveSourceSchedule={(source) => void dashboard.onSaveSourceSchedule(source)}
          onToggleSource={(source) => void dashboard.onToggleSource(source)}
          runningSourceId={dashboard.runningSourceId}
          savingSourceId={dashboard.savingSourceId}
          sourceDrafts={dashboard.sourceDrafts}
          sourceName={dashboard.sourceName}
          sources={dashboard.sources}
          sourceUrl={dashboard.sourceUrl}
          setSourceName={dashboard.setSourceName}
          setSourceUrl={dashboard.setSourceUrl}
          updateSourceDraft={dashboard.updateSourceDraft}
        />
      ) : null}

      {dashboard.activeSection === 'filters' ? (
        <section className="section-panel">
          <div className="panel-heading">
            <h3>Filtros</h3>
            <span>0</span>
          </div>
          <p className="empty-inline">Sin filtros configurados. Las reglas locales se implementaran en la siguiente vertical.</p>
        </section>
      ) : null}

      {dashboard.activeSection === 'runs' ? <RunsView getSourceName={dashboard.getSourceName} runs={dashboard.runs} /> : null}

      {dashboard.activeSection === 'settings' ? (
        <SettingsView
          onToggleScheduler={() => void dashboard.onToggleScheduler()}
          savingScheduler={dashboard.savingScheduler}
          scheduler={dashboard.scheduler}
        />
      ) : null}
    </DashboardShell>
  );
}
