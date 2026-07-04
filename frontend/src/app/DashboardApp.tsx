import { DashboardShell } from '../components/layout/DashboardShell';
import { FiltersView } from '../features/filters/FiltersView';
import { OpportunitiesView } from '../features/opportunities/OpportunitiesView';
import { RunsView } from '../features/runs/RunsView';
import { SettingsView } from '../features/settings/SettingsView';
import { SourcesView } from '../features/sources/SourcesView';
import { useDashboardController } from '../hooks/useDashboardController';

export function DashboardApp() {
  const dashboard = useDashboardController();

  return (
    <DashboardShell
      activeSection={dashboard.activeSection}
      activeSubtitle={dashboard.activeSubtitle}
      activeTitle={dashboard.activeTitle}
      error={dashboard.error}
      navCollapsed={dashboard.navCollapsed}
      onSelectSection={dashboard.selectSection}
      onToggleNav={() => dashboard.setNavCollapsed((current) => !current)}
    >
      {dashboard.activeSection === 'opportunities' ? (
        <OpportunitiesView
          filters={dashboard.resultFilters}
          loading={dashboard.loadingOpportunities}
          opportunityPage={dashboard.opportunityPage}
          pageSize={dashboard.opportunitiesPageSize}
          sources={dashboard.sources}
          onApply={() => void dashboard.loadOpportunities(1)}
          onApplyFilters={(filters) => void dashboard.loadOpportunities(1, filters)}
          onClear={dashboard.clearResultFilters}
          onFilterChange={dashboard.updateResultFilter}
          onPageChange={(page) => void dashboard.loadOpportunities(page)}
          onPageSizeChange={dashboard.changeResultsPageSize}
        />
      ) : null}

      {dashboard.activeSection === 'sources' ? (
        <SourcesView
          filterRules={dashboard.filterRules}
          onCreateSource={dashboard.onCreateSource}
          onDeleteSource={(source) => void dashboard.onDeleteSource(source)}
          onRunMonitor={(sourceId) => void dashboard.onRunMonitor(sourceId)}
          onSaveSourceSchedule={(source) => void dashboard.onSaveSourceSchedule(source)}
          onStartSession={(source) => void dashboard.onStartSession(source)}
          onStopMonitor={(sourceId) => void dashboard.onStopMonitor(sourceId)}
          proxyProfiles={dashboard.proxyProfiles}
          runningSessionId={dashboard.runningSessionId}
          savingSourceId={dashboard.savingSourceId}
          selectedFilterIdsBySource={dashboard.selectedFilterIdsBySource}
          selectedProxyBySource={dashboard.selectedProxyBySource}
          sourceDrafts={dashboard.sourceDrafts}
          sourceName={dashboard.sourceName}
          sources={dashboard.sources}
          sourceUrl={dashboard.sourceUrl}
          setSourceName={dashboard.setSourceName}
          setSourceUrl={dashboard.setSourceUrl}
          toggleSourceFilter={dashboard.toggleSourceFilter}
          updateSourceDraft={dashboard.updateSourceDraft}
          updateSourceProxy={dashboard.updateSourceProxy}
        />
      ) : null}

      {dashboard.activeSection === 'filters' ? (
        <FiltersView
          filterName={dashboard.filterName}
          filterRules={dashboard.filterRules}
          filterTerms={dashboard.filterTerms}
          saving={dashboard.savingFilter}
          onCreateFilter={dashboard.onCreateFilter}
          setFilterName={dashboard.setFilterName}
          setFilterTerms={dashboard.setFilterTerms}
        />
      ) : null}

      {dashboard.activeSection === 'runs' ? (
        <RunsView
          getSourceName={dashboard.getSourceName}
          runs={dashboard.runs}
          onLoadRunEvents={dashboard.onLoadRunEvents}
        />
      ) : null}

      {dashboard.activeSection === 'settings' ? (
        <SettingsView
          onCreateProxy={dashboard.onCreateProxy}
          onTestProxy={(profileId) => void dashboard.onTestProxy(profileId)}
          onToggleScheduler={() => void dashboard.onToggleScheduler()}
          proxyDraft={dashboard.proxyDraft}
          proxyProfiles={dashboard.proxyProfiles}
          savingProxy={dashboard.savingProxy}
          savingScheduler={dashboard.savingScheduler}
          scheduler={dashboard.scheduler}
          setProxyDraft={dashboard.setProxyDraft}
        />
      ) : null}
    </DashboardShell>
  );
}
