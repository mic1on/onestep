import { Navigate, createBrowserRouter } from "react-router-dom";

import { AppShell } from "../components/layout/AppShell";
import { RequireConsoleAuth } from "../features/auth/RequireConsoleAuth";
import { ConnectorsPage } from "../pages/connectors/ConnectorsPage";
import { WorkerEditorPage } from "../pages/workers/WorkerEditorPage";
import { WorkersListPage } from "../pages/workers/WorkersListPage";
import { AgentDetailPage } from "../pages/worker-deployments/AgentDetailPage";
import { DeploymentEventsPage } from "../pages/worker-deployment-events/DeploymentEventsPage";
import { InstanceDetailPage } from "../pages/instance-detail/InstanceDetailPage";
import { LoginPage } from "../pages/login/LoginPage";
import { NotFoundPage } from "../pages/not-found/NotFoundPage";
import { SettingsNotificationsPage } from "../pages/settings-notifications/SettingsNotificationsPage";
import { ServiceDetailPage } from "../pages/service-detail/ServiceDetailPage";
import { ServicesListPage } from "../pages/services-list/ServicesListPage";
import { TaskDetailPage } from "../pages/task-detail/TaskDetailPage";
import { WorkerAgentsPage } from "../pages/worker-agents/WorkerAgentsPage";

export const router = createBrowserRouter([
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/",
    element: <RequireConsoleAuth />,
    children: [
      {
        element: <AppShell />,
        children: [
          {
            index: true,
            element: <Navigate to="/services?environment=all" replace />,
          },
          {
            path: "services",
            element: <ServicesListPage />,
          },
          {
            path: "services/:serviceName",
            element: <ServiceDetailPage />,
          },
          {
            path: "services/:serviceName/tasks/:taskName",
            element: <TaskDetailPage />,
          },
          {
            path: "services/:serviceName/instances/:instanceId",
            element: <InstanceDetailPage />,
          },
          {
            path: "agents",
            element: <WorkerAgentsPage />,
          },
          {
            path: "connectors",
            element: <ConnectorsPage />,
          },
          {
            path: "workers",
            element: <WorkersListPage />,
          },
          {
            path: "workers/:workerId",
            element: <WorkerEditorPage />,
          },
          {
            path: "agents/:agentId",
            element: <AgentDetailPage />,
          },
          {
            path: "agents/:agentId/deployments/:deploymentId/events",
            element: <DeploymentEventsPage />,
          },
          {
            path: "settings/notifications",
            element: <SettingsNotificationsPage />,
          },
          {
            path: "*",
            element: <NotFoundPage />,
          },
        ],
      },
    ],
  },
]);
