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
          opportunityPage={dashboard.opportunityPage}
          pageSize={dashboard.opportunitiesPageSize}
          sources={dashboard.sources}
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
          detailProbeMessages={dashboard.detailProbeMessages}
          detailProbeRefs={dashboard.detailProbeRefs}
          monitorEventHistoryLoadedBySource={dashboard.monitorEventHistoryLoadedBySource}
          monitorEventsBySource={dashboard.monitorEventsBySource}
          monitorHiddenEventIdsBySource={dashboard.monitorHiddenEventIdsBySource}
          monitorRunsBySource={dashboard.monitorRunsBySource}
          monitorStatsBySource={dashboard.monitorStatsBySource}
          monitorStatsRangeBySource={dashboard.monitorStatsRangeBySource}
          onCreateSource={dashboard.onCreateSource}
          onClearMonitorEventsView={dashboard.onClearMonitorEventsView}
          onDeleteSource={(source) => void dashboard.onDeleteSource(source)}
          onLoadMonitorEvents={dashboard.loadMonitorEvents}
          onLoadMonitorStats={(sourceId, range) => void dashboard.loadMonitorStats(sourceId, range)}
          onLoadMonitorRuns={(sourceId) => void dashboard.loadMonitorRuns(sourceId)}
          onPrepareVintedSession={(source) => void dashboard.onPrepareVintedSession(source)}
          onProbeItemDetail={(source) => void dashboard.onProbeItemDetail(source)}
          onRunNow={(source) => void dashboard.onRunNow(source)}
          onSaveSourceSchedule={(source) => void dashboard.onSaveSourceSchedule(source)}
          onStartSession={(source) => void dashboard.onStartSession(source)}
          onStopMonitor={(sourceId) => void dashboard.onStopMonitor(sourceId)}
          runningSessionId={dashboard.runningSessionId}
          savingSourceId={dashboard.savingSourceId}
          sourceDrafts={dashboard.sourceDrafts}
          sourceName={dashboard.sourceName}
          sources={dashboard.sources}
          sourceUrl={dashboard.sourceUrl}
          streamStatus={dashboard.monitorStreamStatus}
          streamReady={dashboard.monitorStreamReady}
          setSourceName={dashboard.setSourceName}
          setSourceUrl={dashboard.setSourceUrl}
          updateDetailProbeRef={dashboard.updateDetailProbeRef}
          updateSourceDraft={dashboard.updateSourceDraft}
        />
      ) : null}

      {dashboard.activeSection === 'settings' ? (
        <SettingsView
          onCreateProxy={dashboard.onCreateProxy}
          onTestProxy={(profileId) => void dashboard.onTestProxy(profileId)}
          onToggleProxy={(profile) => void dashboard.onToggleProxy(profile)}
          onToggleScheduler={() => void dashboard.onToggleScheduler()}
          onUpdateSchedulerConfig={(payload) => void dashboard.onUpdateSchedulerConfig(payload)}
          proxyDraft={dashboard.proxyDraft}
          proxyActionMessages={dashboard.proxyActionMessages}
          proxyProfiles={dashboard.proxyProfiles}
          savingProxy={dashboard.savingProxy}
          savingScheduler={dashboard.savingScheduler}
          scheduler={dashboard.scheduler}
          schedulerAvailabilityError={dashboard.schedulerAvailabilityError}
          setProxyDraft={dashboard.setProxyDraft}
          testingProxyIds={dashboard.testingProxyIds}
        />
      ) : null}
    </DashboardShell>
  );
}
