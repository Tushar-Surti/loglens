import { AnimatePresence } from 'framer-motion'
import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { LoadingBlock } from '@/components/Panel'
import { AppShell } from '@/layout/AppShell'
import { Overview } from '@/pages/Overview'

// The overview is eager (it is the landing view); everything else is split so
// the first paint stays fast. The globe page in particular pulls in three.js.
const Traffic = lazy(() => import('@/pages/Traffic').then((m) => ({ default: m.Traffic })))
const Endpoints = lazy(() => import('@/pages/Endpoints').then((m) => ({ default: m.Endpoints })))
const Sessions = lazy(() => import('@/pages/Sessions').then((m) => ({ default: m.Sessions })))
const LogExplorer = lazy(() => import('@/pages/LogExplorer').then((m) => ({ default: m.LogExplorer })))
const Geography = lazy(() => import('@/pages/Geography').then((m) => ({ default: m.Geography })))
const Anomalies = lazy(() => import('@/pages/Anomalies').then((m) => ({ default: m.Anomalies })))
const Incidents = lazy(() => import('@/pages/Incidents').then((m) => ({ default: m.Incidents })))
const IncidentDetail = lazy(() => import('@/pages/IncidentDetail').then((m) => ({ default: m.IncidentDetail })))
const Alerts = lazy(() => import('@/pages/Alerts').then((m) => ({ default: m.Alerts })))
const Health = lazy(() => import('@/pages/Health').then((m) => ({ default: m.Health })))
const Settings = lazy(() => import('@/pages/Settings').then((m) => ({ default: m.Settings })))

export function App() {
  const location = useLocation()

  return (
    <AnimatePresence mode="wait">
      <Routes location={location} key={location.pathname}>
        <Route element={<AppShell />}>
          <Route index element={<Overview />} />
          <Route
            path="*"
            element={
              <Suspense fallback={<LoadingBlock height={420} label="Loading view" />}>
                <Routes location={location}>
                  <Route path="/traffic" element={<Traffic />} />
                  <Route path="/endpoints" element={<Endpoints />} />
                  <Route path="/sessions" element={<Sessions />} />
                  <Route path="/logs" element={<LogExplorer />} />
                  <Route path="/geography" element={<Geography />} />
                  <Route path="/anomalies" element={<Anomalies />} />
                  <Route path="/incidents" element={<Incidents />} />
                  <Route path="/incidents/:incidentId" element={<IncidentDetail />} />
                  <Route path="/alerts" element={<Alerts />} />
                  <Route path="/health" element={<Health />} />
                  <Route path="/settings" element={<Settings />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Suspense>
            }
          />
        </Route>
      </Routes>
    </AnimatePresence>
  )
}
