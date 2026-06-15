import { Navigate, createBrowserRouter } from "react-router-dom";

import { AppShell } from "../components/layout/AppShell";
import { RequireConsoleAuth } from "../features/auth/RequireConsoleAuth";
import { InstanceDetailPage } from "../pages/instance-detail/InstanceDetailPage";
import { LoginPage } from "../pages/login/LoginPage";
import { NotFoundPage } from "../pages/not-found/NotFoundPage";
import { PipelineEditorPage } from "../pages/pipeline-editor/PipelineEditorPage";
import { PipelinesListPage } from "../pages/pipelines-list/PipelinesListPage";
import { SettingsNotificationsPage } from "../pages/settings-notifications/SettingsNotificationsPage";
import { ServiceDetailPage } from "../pages/service-detail/ServiceDetailPage";
import { ServicesListPage } from "../pages/services-list/ServicesListPage";
import { TaskDetailPage } from "../pages/task-detail/TaskDetailPage";

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
            path: "pipelines",
            element: <PipelinesListPage />,
          },
          {
            path: "pipelines/:pipelineId",
            element: <PipelineEditorPage />,
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
