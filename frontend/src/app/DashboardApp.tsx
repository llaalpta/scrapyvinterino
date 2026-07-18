import { DashboardShell } from '../components/layout/DashboardShell';
import { OpportunitiesView } from '../features/opportunities/OpportunitiesView';
import { SettingsView } from '../features/settings/SettingsView';
import { SourcesView } from '../features/sources/SourcesView';
import { useDashboardController } from '../hooks/useDashboardController';
import type { LocalAuthUser } from '../api';

export function DashboardApp({ onLogout, user }: { onLogout: () => void; user: LocalAuthUser }) {
  const dashboard = useDashboardController();

  return (
    <DashboardShell
      activeSection={dashboard.activeSection}
      activeSubtitle={dashboard.activeSubtitle}
      activeTitle={dashboard.activeTitle}
      error={dashboard.error}
      navCollapsed={dashboard.navCollapsed}
      onLogout={onLogout}
      onSelectSection={dashboard.selectSection}
      onToggleNav={() => dashboard.setNavCollapsed((current) => !current)}
      userEmail={user.email}
    >
      {dashboard.activeSection === 'opportunities' ? (
        <OpportunitiesView
          filters={dashboard.opportunityFilters}
          loading={dashboard.loadingOpportunities}
          opportunityCollectionState={dashboard.opportunityCollectionState}
          opportunityPage={dashboard.opportunityPage}
          pageSize={dashboard.opportunitiesPageSize}
          sources={dashboard.sources}
          sourceCollectionState={dashboard.sourceCollectionState}
          onApply={() => void dashboard.loadOpportunities(1)}
          onApplyFilters={(filters) => void dashboard.loadOpportunities(1, filters)}
          onClear={dashboard.clearOpportunityFilters}
          onFilterChange={dashboard.updateOpportunityFilter}
          onPageChange={(page) => void dashboard.loadOpportunities(page)}
          onPageSizeChange={dashboard.changeResultsPageSize}
        />
      ) : null}

      {dashboard.activeSection === 'sources' ? (
        <SourcesView
          key={dashboard.sources.map((source) => source.id).sort((left, right) => left - right).join(':')}
          creatingSource={dashboard.creatingSource}
          monitorEventHistoryLoadedBySource={dashboard.monitorEventHistoryLoadedBySource}
          monitorEventsBySource={dashboard.monitorEventsBySource}
          monitorHiddenEventIdsBySource={dashboard.monitorHiddenEventIdsBySource}
          monitorCommandPending={dashboard.monitorCommandPending}
          monitorRunsBySource={dashboard.monitorRunsBySource}
          pendingStopSourceIds={dashboard.pendingStopSourceIds}
          monitorStatsBySource={dashboard.monitorStatsBySource}
          monitorStatsRangeBySource={dashboard.monitorStatsRangeBySource}
          onCreateSource={dashboard.onCreateSource}
          onClearMonitorEventsView={dashboard.onClearMonitorEventsView}
          onDeleteSource={(source) => void dashboard.onDeleteSource(source)}
          onLoadMonitorEvents={dashboard.loadMonitorEvents}
          onLoadMonitorStats={(sourceId, range) => void dashboard.loadMonitorStats(sourceId, range)}
          onLoadMonitorRuns={dashboard.loadMonitorRuns}
          onRunNow={(source) => void dashboard.onRunNow(source)}
          onSaveSourceSchedule={(source) => void dashboard.onSaveSourceSchedule(source)}
          onStartSession={(source) => void dashboard.onStartSession(source)}
          onStopMonitor={(sourceId) => void dashboard.onStopMonitor(sourceId)}
          runningSessionId={dashboard.runningSessionId}
          savingSourceId={dashboard.savingSourceId}
          sourceDrafts={dashboard.sourceDrafts}
          sourceName={dashboard.sourceName}
          sourceCollectionState={dashboard.sourceCollectionState}
          sources={dashboard.sources}
          sourceUrl={dashboard.sourceUrl}
          streamStatus={dashboard.monitorStreamStatus}
          streamReady={dashboard.monitorStreamReady}
          setSourceName={dashboard.setSourceName}
          setSourceUrl={dashboard.setSourceUrl}
          updateSourceDraft={dashboard.updateSourceDraft}
        />
      ) : null}

      {dashboard.activeSection === 'settings' ? (
        <SettingsView
          onCreateProxy={dashboard.onCreateProxy}
          onTestProxy={(profileId) => void dashboard.onTestProxy(profileId)}
          onToggleProxy={(profile) => void dashboard.onToggleProxy(profile)}
          onUpdateSchedulerConfig={(payload) => void dashboard.onUpdateSchedulerConfig(payload)}
          proxyDraft={dashboard.proxyDraft}
          proxyActionMessages={dashboard.proxyActionMessages}
          proxyCollectionState={dashboard.proxyCollectionState}
          proxyProfiles={dashboard.proxyProfiles}
          savingProxy={dashboard.savingProxy}
          scheduler={dashboard.scheduler}
          schedulerAvailabilityError={dashboard.schedulerAvailabilityError}
          setProxyDraft={dashboard.setProxyDraft}
          testingProxyIds={dashboard.testingProxyIds}
        />
      ) : null}
    </DashboardShell>
  );
}
